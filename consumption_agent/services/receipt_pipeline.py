from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from matcher import _build_normalized_index, match_record, normalize
from repositories.items import (
    ensure_delivery_column,
    get_category_id,
    insert_receipt_item,
    update_purchase_details,
)
from repositories.purchases import insert_receipt_purchase
from scripts import receipt_ocr


log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXTENSIONS = {".txt", ".text", ".ocr", ".sample"}
PDF_EXTENSIONS = {".pdf"}
DELIVERY_KEYWORDS = (
    "доставка",
    "доставк",
    "курьер",
    "shipping",
    "delivery",
    "почт",
    "postage",
    "транспорт",
    "service fee",
    "сервисный сбор",
    "service charge",
)
DATE_PREFIX_RE = re.compile(r"^(?:\d{2}[./]\d{2}[./]\d{2,4}(?:\s+\d{1,2}:\d{2})?\s+)+")


@dataclass
class StructuredReceiptItem:
    name: str
    price: float | None = None
    qty: float = 1
    total: float | None = None
    is_delivery: bool = False
    matched_item_id: int | None = None
    match_score: float | None = None
    match_method: str | None = None

    def __post_init__(self) -> None:
        if self.total is None and self.price is not None:
            self.total = self.price * self.qty
        self.is_delivery = self.is_delivery or is_delivery_name(self.name)


@dataclass
class StructuredReceipt:
    store: str = ""
    date: str = field(default_factory=lambda: date.today().isoformat())
    total: float | None = None
    items: list[StructuredReceiptItem] = field(default_factory=list)
    delivery_total: float = 0.0
    raw_text: str = ""
    ocr_score: int = 0
    engine: str = "text"
    source_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def product_items(self) -> list[StructuredReceiptItem]:
        return [item for item in self.items if not item.is_delivery]

    @property
    def delivery_items(self) -> list[StructuredReceiptItem]:
        return [item for item in self.items if item.is_delivery]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineApplyResult:
    dry_run: bool
    purchase_id: int | None = None
    created_item_ids: list[int] = field(default_factory=list)
    matched_item_ids: list[int] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)


def is_delivery_name(name: str | None) -> bool:
    lower = (name or "").lower()
    return any(keyword in lower for keyword in DELIVERY_KEYWORDS)


def clean_item_name(name: str) -> str:
    name = DATE_PREFIX_RE.sub("", name or "").strip()
    return re.sub(r"\s+", " ", name)


def find_delivery_name(text: str) -> str:
    for raw_line in (text or "").splitlines():
        line = clean_item_name(raw_line)
        if line and is_delivery_name(line):
            return line
    return "Доставка"


def detect_input_type(path_or_text: str) -> str:
    path = Path(path_or_text)
    if path.exists():
        ext = path.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            return "image"
        if ext in PDF_EXTENSIONS:
            return "pdf"
        if ext in TEXT_EXTENSIONS:
            return "text_file"
    return "text"


def extract_pdf_text(path: str) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:
        log.info("pypdf extraction failed for %s: %s", path, exc)

    try:
        proc = subprocess.run(
            ["pdftotext", path, "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
        log.info("pdftotext failed for %s: %s", path, proc.stderr[:200])
    except Exception as exc:
        log.info("pdftotext unavailable for %s: %s", path, exc)
    return ""


def run_easyocr_text(image_path: str) -> tuple[str, int]:
    try:
        import easyocr
    except Exception:
        return "", 0

    try:
        reader = easyocr.Reader(["ru", "en"], gpu=False)
        chunks = reader.readtext(image_path, detail=0, paragraph=True)
        text = "\n".join(str(chunk) for chunk in chunks if chunk)
        return text, receipt_ocr._score_ocr_text(text)
    except Exception as exc:
        log.warning("EasyOCR failed for %s: %s", image_path, exc)
        return "", 0


def normalize_receipt(receipt: StructuredReceipt) -> StructuredReceipt:
    delivery_items = [item for item in receipt.items if item.is_delivery]
    product_items = [item for item in receipt.items if not item.is_delivery]
    delivery_total = receipt.delivery_total + sum((item.total or item.price or 0) for item in delivery_items)

    normalized_items = product_items + delivery_items
    if delivery_total and not delivery_items:
        normalized_items.append(
            StructuredReceiptItem(
                name="Доставка",
                price=delivery_total,
                qty=1,
                total=delivery_total,
                is_delivery=True,
            )
        )

    receipt.items = normalized_items
    receipt.delivery_total = round(delivery_total, 2) if delivery_total else 0.0
    if receipt.total is None:
        subtotal = sum(item.total or 0 for item in receipt.items)
        receipt.total = round(subtotal, 2) if subtotal else None
    return receipt


def parse_receipt_text(text: str, *, source_path: str | None = None, engine: str = "text") -> StructuredReceipt:
    items, delivery_cost = receipt_ocr.parse_items(text)
    if not items:
        try:
            from services.ocr import _parse_receipt_lines

            parsed_lines = _parse_receipt_lines(text)
            items = [
                receipt_ocr.ReceiptItem(
                    name=item["name"],
                    price=float(item.get("price") or 0),
                    qty=float(item.get("qty") or 1),
                    total=float(item.get("total") or 0),
                )
                for item in parsed_lines
            ]
        except Exception as exc:
            log.info("fallback receipt line parser failed: %s", exc)

    structured_items = [
        StructuredReceiptItem(
            name=clean_item_name(item.name),
            price=float(item.price) if item.price is not None else None,
            qty=float(item.qty or 1),
            total=float(item.total) if item.total is not None else None,
            is_delivery=is_delivery_name(item.name),
        )
        for item in items
    ]
    if delivery_cost and not any(item.is_delivery for item in structured_items):
        structured_items.append(
            StructuredReceiptItem(
                name=find_delivery_name(text),
                price=float(delivery_cost),
                qty=1,
                total=float(delivery_cost),
                is_delivery=True,
            )
        )
        delivery_cost = 0
    receipt = StructuredReceipt(
        store=receipt_ocr._detect_shop(text),
        date=receipt_ocr.parse_date(text),
        total=receipt_ocr.parse_total(text),
        items=structured_items,
        delivery_total=float(delivery_cost or 0),
        raw_text=text,
        ocr_score=receipt_ocr._score_ocr_text(text),
        engine=engine,
        source_path=source_path,
    )
    return normalize_receipt(receipt)


def receipt_from_ocr_result(result: Any, *, source_path: str, engine: str = "tesseract") -> StructuredReceipt:
    structured_items = [
        StructuredReceiptItem(
            name=item.name,
            price=float(item.price) if item.price is not None else None,
            qty=float(item.qty or 1),
            total=float(item.total) if item.total is not None else None,
            is_delivery=is_delivery_name(item.name),
        )
        for item in (result.items or [])
    ]
    receipt = StructuredReceipt(
        store=result.shop or "",
        date=result.date or date.today().isoformat(),
        total=result.total,
        items=structured_items,
        delivery_total=float(result.delivery_cost or 0),
        raw_text=result.raw_text or "",
        ocr_score=int(result.ocr_score or 0),
        engine=engine,
        source_path=source_path,
    )
    return normalize_receipt(receipt)


def receipt_from_vision_result(result: dict[str, Any], *, source_path: str) -> StructuredReceipt:
    if "error" in result:
        raise RuntimeError(result["error"])

    delivery = result.get("delivery") or {}
    structured_items = []
    for item in result.get("items") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        qty = float(item.get("qty") or 1)
        price = item.get("price")
        price = float(price) if price is not None else None
        structured_items.append(
            StructuredReceiptItem(
                name=name,
                price=price,
                qty=qty,
                is_delivery=is_delivery_name(name),
            )
        )

    receipt = StructuredReceipt(
        store=result.get("store") or "",
        date=result.get("date") or date.today().isoformat(),
        total=float(result["total"]) if result.get("total") is not None else None,
        items=structured_items,
        delivery_total=float(delivery.get("price") or 0),
        raw_text=result.get("raw") or "",
        ocr_score=100,
        engine=f"vision:{result.get('_model', 'unknown')}",
        source_path=source_path,
        meta={"vision": {k: v for k, v in result.items() if k.startswith("_")}},
    )
    if delivery.get("name") and delivery.get("price"):
        receipt.items.append(
            StructuredReceiptItem(
                name=str(delivery.get("name") or "Доставка"),
                price=float(delivery.get("price") or 0),
                qty=1,
                is_delivery=True,
            )
        )
    return normalize_receipt(receipt)


def is_weak_receipt(receipt: StructuredReceipt, *, min_ocr_score: int = 30) -> bool:
    return receipt.ocr_score < min_ocr_score or (not receipt.product_items and receipt.total is None)


def process_source(
    source: str,
    *,
    input_type: str = "auto",
    vision_fallback: bool = True,
    easyocr_fallback: bool = True,
    min_ocr_score: int = 30,
) -> StructuredReceipt:
    kind = detect_input_type(source) if input_type == "auto" else input_type

    if kind == "text_file":
        text = Path(source).read_text(encoding="utf-8", errors="replace")
        return parse_receipt_text(text, source_path=source, engine="text")
    if kind == "text":
        return parse_receipt_text(source, source_path=None, engine="text")
    if kind == "pdf":
        text = extract_pdf_text(source)
        return parse_receipt_text(text, source_path=source, engine="pdf_text")
    if kind != "image":
        raise ValueError(f"Unsupported receipt input type: {kind}")

    tesseract_result = receipt_ocr.process_receipt(source)
    receipt = receipt_from_ocr_result(tesseract_result, source_path=source, engine="tesseract")
    if not is_weak_receipt(receipt, min_ocr_score=min_ocr_score):
        return receipt

    if easyocr_fallback:
        text, score = run_easyocr_text(source)
        if text:
            easy_receipt = parse_receipt_text(text, source_path=source, engine="easyocr")
            easy_receipt.ocr_score = score
            if not is_weak_receipt(easy_receipt, min_ocr_score=min_ocr_score):
                return easy_receipt

    if vision_fallback:
        try:
            from vision_receipt import recognize_receipt

            vision = recognize_receipt(source)
            return receipt_from_vision_result(vision, source_path=source)
        except Exception as exc:
            receipt.meta["vision_error"] = str(exc)
            log.warning("Vision fallback failed for %s: %s", source, exc)

    return receipt


def load_match_items(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, COALESCE(brand,'') AS brand, COALESCE(sku,'') AS sku
        FROM items
        WHERE deleted_at IS NULL
        """
    ).fetchall()
    return [dict(row) for row in rows]


def match_receipt_items(
    receipt: StructuredReceipt,
    conn,
    *,
    min_score: int = 85,
    medium_score: int = 90,
) -> StructuredReceipt:
    all_items = load_match_items(conn)
    norm_index = _build_normalized_index(all_items)
    norm_item_cache = {id(item): normalize(item["name"]) for item in all_items}

    for item in receipt.product_items:
        rec = {
            "recognized_product": item.name,
            "confidence": "high",
            "brand": "",
            "sku": "",
        }
        candidates = match_record(rec, norm_index, all_items, norm_item_cache, min_score, medium_score)
        if candidates and candidates[0]["score"] >= min_score:
            best = candidates[0]
            item.matched_item_id = best["item"]["id"]
            item.match_score = float(best["score"])
            item.match_method = best["method"]
    return receipt


def persist_receipt(
    conn,
    receipt: StructuredReceipt,
    *,
    dry_run: bool = False,
    source: str = "receipt_pipeline",
    data_origin: str = "receipt_pipeline",
    receipt_url: str | None = None,
    email_message_id: str | None = None,
    order_number: str | None = None,
) -> PipelineApplyResult:
    if dry_run:
        try:
            match_receipt_items(receipt, conn)
        except Exception as exc:
            receipt.meta["match_error"] = str(exc)
    else:
        ensure_delivery_column(conn)
        match_receipt_items(receipt, conn)

    result = PipelineApplyResult(dry_run=dry_run)
    notes = {
        "engine": receipt.engine,
        "ocr_score": receipt.ocr_score,
        "source_path": receipt.source_path,
        "delivery_total": receipt.delivery_total,
    }
    purchase_action = {
        "action": "create_purchase",
        "date": receipt.date,
        "total": receipt.total,
        "source": source,
        "store": receipt.store,
    }
    result.actions.append(purchase_action)

    for item in receipt.items:
        action = {
            "action": "upsert_item",
            "name": item.name,
            "price": item.price,
            "qty": item.qty,
            "is_delivery": item.is_delivery,
            "matched_item_id": item.matched_item_id,
            "match_score": item.match_score,
        }
        result.actions.append(action)

    if dry_run:
        return result

    purchase_id = insert_receipt_purchase(
        conn,
        purchase_date=receipt.date,
        total_amount=receipt.total,
        source=source,
        data_origin=data_origin,
        store_name=receipt.store or None,
        order_number=order_number,
        receipt_url=receipt_url or receipt.source_path,
        email_message_id=email_message_id,
        notes=json.dumps(notes, ensure_ascii=False),
    )
    result.purchase_id = purchase_id

    other_category = get_category_id(conn, "other", fallback_slug=None)
    service_category = get_category_id(conn, "service", fallback_slug=None) or other_category

    for item in receipt.items:
        if item.matched_item_id and not item.is_delivery:
            update_purchase_details(
                conn,
                item_id=item.matched_item_id,
                purchase_id=purchase_id,
                purchase_price=item.price,
                purchase_date=receipt.date,
                quantity=item.qty,
            )
            result.matched_item_ids.append(item.matched_item_id)
            continue

        item_id = insert_receipt_item(
            conn,
            name=item.name,
            price=item.price,
            purchase_date=receipt.date,
            category_id=service_category if item.is_delivery else other_category,
            purchase_id=purchase_id,
            is_delivery=item.is_delivery,
            data_origin=data_origin,
        )
        result.created_item_ids.append(item_id)

    conn.commit()
    return result


def result_to_json(receipt: StructuredReceipt, apply_result: PipelineApplyResult | None = None) -> str:
    payload: dict[str, Any] = {"receipt": receipt.to_dict()}
    if apply_result is not None:
        payload["apply"] = asdict(apply_result)
    return json.dumps(payload, ensure_ascii=False, indent=2)
