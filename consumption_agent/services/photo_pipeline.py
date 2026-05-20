"""Photo processing pipeline for the Telegram bot.

This module centralises the heavy, Telegram-independent logic that used to live
inside ``telegram_bot.photo_handler``:

* mode parsing (receipt session / tag / add_item redirect) from the caption;
* image-type heuristics (tag vs receipt correction) — pure and unit-testable;
* QR + OCR extraction (delegates to ``services.ocr``);
* receipt extraction + persistence (delegates to ``services.receipt_pipeline``);
* lightweight ``ocr_attempts`` logging.

Everything here is callable without ``python-telegram-bot`` so it can be tested
with mocked Vision / Tesseract backends. The Telegram orchestration (download,
reply formatting) stays in ``bot/handlers/photos.py``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.ocr import (
    classify_image_type,
    decode_qr,
    ocr_image,
    parse_clothing_tag,
)
from services.receipt_pipeline import persist_receipt, process_source

log = logging.getLogger(__name__)

# Item types that should be recognised as a product (not a receipt/tag).
ITEM_IMAGE_TYPES = ('clothing', 'food', 'interior', 'tech', 'item', 'other', 'unknown')

_TAG_INDICATORS = (
    'СОСТАВ', 'СТРАНА', 'РАЗМЕР', 'SIZE', 'MADE IN',
    'АРТИКУЛ', 'ARTICLE', 'CARE', 'WASH',
)
_RECEIPT_INDICATORS = (
    'КАССОВЫЙ ЧЕК', 'ФИСКАЛЬНЫЙ', 'ФН ', 'ФПД', 'ОФД',
    'ИНН', 'ИТОГ', 'БЕЗНАЛИЧ', 'СУММА', 'ДОСТАВКА',
)


# ---------------------------------------------------------------------------
# Mode parsing (pure)
# ---------------------------------------------------------------------------

@dataclass
class PhotoMode:
    """Result of inspecting the caption + user session for processing intent."""

    force_receipt: bool = False
    force_tag: bool = False
    receipts_remaining: int | None = None  # new session value to store, if any
    redirect_add_item: bool = False
    add_item_args: list[str] = field(default_factory=list)


def parse_photo_mode(
    caption: str,
    *,
    receipts_remaining: int = 0,
    force_tag_flag: bool = False,
) -> PhotoMode:
    """Decide how an incoming photo should be processed.

    Pure function: takes the caption and the relevant pieces of user session
    state, returns a :class:`PhotoMode`. The caller is responsible for reading
    and writing ``ctx.user_data``.
    """
    mode = PhotoMode()
    caption = caption or ''
    caption_lower = caption.strip().lower()

    # Active multi-receipt session started by a previous "чек N" caption.
    if receipts_remaining > 0:
        mode.force_receipt = True
        mode.receipts_remaining = receipts_remaining - 1

    if caption_lower.startswith('чек'):
        mode.force_receipt = True
        parts = caption_lower.split()
        if len(parts) > 1 and parts[1].isdigit():
            count = int(parts[1])
            if count > 1:
                mode.receipts_remaining = count - 1

    mode.force_tag = force_tag_flag
    if caption_lower.startswith('бирка') or caption_lower.startswith('tag'):
        mode.force_tag = True

    # Receipt always wins over tag.
    if mode.force_receipt:
        mode.force_tag = False

    # /add_item redirect (only when not a forced receipt).
    if not mode.force_receipt and caption.strip().startswith('/add_item'):
        mode.redirect_add_item = True
        mode.add_item_args = caption.strip().split()[1:]

    return mode


def looks_like_item_description(caption: str) -> tuple[bool, list[str]]:
    """Return (is_item_description, args) using the brand parser heuristic."""
    if not caption or not caption.strip():
        return False, []
    try:
        from brand_parser import parse_brand_and_name
    except Exception:  # pragma: no cover - brand_parser optional in tests
        return False, []
    bp = parse_brand_and_name(caption)
    if bp.get('name') and (bp.get('brand') or bp.get('replace_months')):
        return True, caption.strip().split()
    return False, []


# ---------------------------------------------------------------------------
# Image-type heuristics (pure)
# ---------------------------------------------------------------------------

@dataclass
class TagDetection:
    image_type: str
    is_real_tag: bool
    has_brand: bool
    has_article: bool
    has_barcode: bool


def resolve_image_type(
    image_type: str,
    *,
    tag_probe: dict[str, Any],
    pyzbar_barcode: str | None,
    qr_data: dict | None,
    total_amount: float | None,
    force_receipt: bool,
) -> TagDetection:
    """Refine ``image_type`` using OCR-derived tag/receipt signals.

    Mirrors the heuristic block from the legacy ``photo_handler``. Pure and
    unit-testable — no Telegram, no I/O.
    """
    tag_probe = tag_probe or {}

    has_barcode = bool(
        (tag_probe.get('barcode') and len(str(tag_probe.get('barcode'))) >= 8)
        or (pyzbar_barcode and len(pyzbar_barcode) >= 8)
    )
    has_article = bool(tag_probe.get('article') and len(str(tag_probe.get('article'))) >= 5)
    has_brand = bool(tag_probe.get('brand') and len(str(tag_probe.get('brand'))) >= 2)

    raw_text = (tag_probe.get('raw') or '').upper()
    has_tag_indicators = any(ind in raw_text for ind in _TAG_INDICATORS)

    is_real_tag = bool(
        (has_brand and (has_article or has_barcode))
        or (has_barcode and has_tag_indicators)
        or (pyzbar_barcode and len(pyzbar_barcode) >= 10)  # EAN-13 → definitely a tag
    )

    # Vision said tech/other but signals say tag → override to tag.
    if (
        not force_receipt
        and image_type in ('unknown', 'other', 'tech')
        and is_real_tag
        and not total_amount
    ):
        image_type = 'tag'

    # Correct tag → receipt when receipt signals are present.
    has_receipt_indicators = len([i for i in _RECEIPT_INDICATORS if i in raw_text]) >= 2
    has_fns_qr = bool(qr_data and 't' in qr_data and 's' in qr_data and 'fn' in qr_data)
    if image_type == 'tag' and (
        force_receipt
        or has_fns_qr
        or has_receipt_indicators
        or (total_amount and not is_real_tag)
    ):
        image_type = 'receipt'

    return TagDetection(
        image_type=image_type,
        is_real_tag=is_real_tag,
        has_brand=has_brand,
        has_article=has_article,
        has_barcode=has_barcode,
    )


# ---------------------------------------------------------------------------
# QR + OCR extraction (sync, run inside asyncio.to_thread by the caller)
# ---------------------------------------------------------------------------

@dataclass
class QrOcrResult:
    qr_data: dict | None = None
    total_amount: float | None = None
    purchase_date: str | None = None
    text: str = ''


def run_qr_ocr(receipt_path: str, *, write_debug: bool = True) -> QrOcrResult:
    """Decode the FNS QR code and run OCR for a receipt/tag photo (sync)."""
    result = QrOcrResult()

    qr_data = decode_qr(receipt_path)
    if qr_data:
        result.qr_data = qr_data
        total = qr_data.get('s')
        if total:
            result.total_amount = float(total)
        date_str = qr_data.get('t')
        if date_str and len(date_str) >= 8:
            result.purchase_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    text = ocr_image(receipt_path) or ''
    result.text = text
    if write_debug:
        try:
            with open(receipt_path.replace('.jpg', '_ocr.txt'), 'w', encoding='utf-8') as f:
                f.write(text or 'NO_OCR_TEXT')
        except OSError as e:  # pragma: no cover - debug only
            log.debug('failed to write OCR debug file: %s', e)

    return result


def read_pyzbar_barcode(receipt_path: str) -> str | None:
    """Return the first barcode found by pyzbar, or None."""
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image

        codes = decode(Image.open(receipt_path))
        if codes:
            barcode = codes[0].data.decode('utf-8')
            log.info('pyzbar found barcode: %s', barcode)
            return barcode
    except Exception as e:  # pragma: no cover - optional dependency / image issues
        log.debug('pyzbar failed: %s', e)
    return None


def probe_clothing_tag(text: str, receipt_path: str) -> dict[str, Any]:
    """Thin wrapper over services.ocr.parse_clothing_tag (sync)."""
    return parse_clothing_tag(text or '', receipt_path)


def classify_from_ocr(text: str) -> str:
    """Fallback OCR-based image classification."""
    return classify_image_type(text or '')


# ---------------------------------------------------------------------------
# Receipt extraction + persistence
# ---------------------------------------------------------------------------

@dataclass
class ReceiptExtraction:
    purchase_id: int | None
    store: str | None
    total: float | None
    date: str | None
    engine: str | None
    ocr_score: int
    items: list[dict[str, Any]] = field(default_factory=list)
    delivery_items: list[dict[str, Any]] = field(default_factory=list)
    delivery_total: float = 0.0
    category_reviews: list[dict[str, Any]] = field(default_factory=list)


def extract_receipt(
    conn,
    receipt_path: str,
    *,
    total_amount: float | None = None,
    purchase_date: str | None = None,
    source: str = 'telegram_photo',
    data_origin: str = 'telegram_photo',
) -> ReceiptExtraction:
    """Run the unified receipt pipeline and persist the result (sync)."""
    receipt = process_source(receipt_path, input_type='image', vision_fallback=True)
    if total_amount and receipt.total is None:
        receipt.total = total_amount
    if purchase_date:
        receipt.date = purchase_date

    apply_result = persist_receipt(
        conn,
        receipt,
        dry_run=False,
        source=source,
        data_origin=data_origin,
        receipt_url=receipt_path,
    )

    items = [
        {
            'name': item.name,
            'price': item.price or 0,
            'qty': item.qty,
            'total': item.total or item.price or 0,
        }
        for item in receipt.product_items
    ]
    delivery_items = [
        {
            'name': item.name,
            'price': item.price or item.total or 0,
            'qty': item.qty,
            'total': item.total or item.price or 0,
        }
        for item in receipt.delivery_items
    ]

    return ReceiptExtraction(
        purchase_id=apply_result.purchase_id,
        store=receipt.store,
        total=receipt.total,
        date=receipt.date,
        engine=receipt.engine,
        ocr_score=receipt.ocr_score,
        items=items,
        delivery_items=delivery_items,
        delivery_total=receipt.delivery_total,
        category_reviews=apply_result.category_reviews,
    )


# ---------------------------------------------------------------------------
# ocr_attempts logging
# ---------------------------------------------------------------------------

def ensure_ocr_attempts_schema(conn) -> None:
    """Create the ``ocr_attempts`` table if it does not exist (idempotent)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ocr_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            image_sha TEXT,
            engine TEXT,
            image_type TEXT,
            status TEXT,
            elapsed_ms INTEGER,
            error TEXT
        )
        """
    )
    conn.commit()


def file_sha256(path: str) -> str | None:
    """Return the sha256 of a file, or None if unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:  # pragma: no cover
        return None


def log_ocr_attempt(
    conn,
    *,
    image_sha: str | None,
    engine: str,
    image_type: str | None = None,
    status: str = 'ok',
    elapsed_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Insert a row into ``ocr_attempts``; never raises on logging failure."""
    try:
        ensure_ocr_attempts_schema(conn)
        conn.execute(
            """
            INSERT INTO ocr_attempts (image_sha, engine, image_type, status, elapsed_ms, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (image_sha, engine, image_type, status, elapsed_ms, (error or '')[:500] or None),
        )
        conn.commit()
    except Exception as e:  # pragma: no cover - logging must never break the flow
        log.debug('failed to log ocr_attempt: %s', e)


class _Timer:
    """Context manager that measures elapsed milliseconds."""

    def __enter__(self) -> _Timer:
        self._start = time.time()
        self.elapsed_ms = 0
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed_ms = int((time.time() - self._start) * 1000)


def timer() -> _Timer:
    return _Timer()


def today_iso() -> str:
    return date.today().isoformat()
