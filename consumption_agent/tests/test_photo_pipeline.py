"""Tests for services.photo_pipeline — pure logic, no Telegram, no Vision/Tesseract."""

from __future__ import annotations

from pathlib import Path

import pytest

from consumption.db import connect
from services import photo_pipeline as pipeline


# ---------------------------------------------------------------------------
# parse_photo_mode
# ---------------------------------------------------------------------------

def test_parse_photo_mode_plain_caption():
    mode = pipeline.parse_photo_mode('просто фото')
    assert not mode.force_receipt
    assert not mode.force_tag
    assert not mode.redirect_add_item
    assert mode.receipts_remaining is None


def test_parse_photo_mode_receipt_keyword():
    mode = pipeline.parse_photo_mode('чек')
    assert mode.force_receipt
    assert not mode.force_tag
    assert mode.receipts_remaining is None


def test_parse_photo_mode_receipt_session_count():
    mode = pipeline.parse_photo_mode('чек 3')
    assert mode.force_receipt
    assert mode.receipts_remaining == 2  # 3 - 1


def test_parse_photo_mode_active_session_decrements():
    mode = pipeline.parse_photo_mode('', receipts_remaining=2)
    assert mode.force_receipt
    assert mode.receipts_remaining == 1


def test_parse_photo_mode_tag_keyword():
    assert pipeline.parse_photo_mode('бирка').force_tag
    assert pipeline.parse_photo_mode('tag').force_tag


def test_parse_photo_mode_tag_flag_from_command():
    mode = pipeline.parse_photo_mode('', force_tag_flag=True)
    assert mode.force_tag


def test_parse_photo_mode_receipt_beats_tag():
    # force_tag flag set, but caption says receipt → receipt wins
    mode = pipeline.parse_photo_mode('чек', force_tag_flag=True)
    assert mode.force_receipt
    assert not mode.force_tag


def test_parse_photo_mode_add_item_redirect():
    mode = pipeline.parse_photo_mode('/add_item пиджак Corneliani')
    assert mode.redirect_add_item
    assert mode.add_item_args == ['пиджак', 'Corneliani']


def test_parse_photo_mode_add_item_ignored_when_receipt_session():
    mode = pipeline.parse_photo_mode('/add_item пиджак', receipts_remaining=1)
    assert mode.force_receipt
    assert not mode.redirect_add_item


# ---------------------------------------------------------------------------
# resolve_image_type
# ---------------------------------------------------------------------------

def test_resolve_image_type_brand_plus_article_is_tag():
    det = pipeline.resolve_image_type(
        'other',
        tag_probe={'brand': 'Nike', 'article': 'AB1234', 'raw': ''},
        pyzbar_barcode=None,
        qr_data=None,
        total_amount=None,
        force_receipt=False,
    )
    assert det.image_type == 'tag'
    assert det.is_real_tag
    assert det.has_brand and det.has_article


def test_resolve_image_type_ean13_barcode_is_tag():
    det = pipeline.resolve_image_type(
        'tech',
        tag_probe={'raw': ''},
        pyzbar_barcode='4601234567890',  # 13 digits
        qr_data=None,
        total_amount=None,
        force_receipt=False,
    )
    assert det.image_type == 'tag'
    assert det.is_real_tag


def test_resolve_image_type_barcode_with_indicators():
    det = pipeline.resolve_image_type(
        'other',
        tag_probe={'barcode': '12345678', 'raw': 'СОСТАВ: 100% ХЛОПОК'},
        pyzbar_barcode=None,
        qr_data=None,
        total_amount=None,
        force_receipt=False,
    )
    assert det.is_real_tag
    assert det.image_type == 'tag'


def test_resolve_image_type_tag_corrected_to_receipt_by_fns_qr():
    det = pipeline.resolve_image_type(
        'tag',
        tag_probe={'brand': 'X', 'article': 'AB123', 'raw': ''},
        pyzbar_barcode=None,
        qr_data={'t': '20260101T1200', 's': '500.00', 'fn': '999'},
        total_amount=500.0,
        force_receipt=False,
    )
    assert det.image_type == 'receipt'


def test_resolve_image_type_tag_corrected_by_receipt_indicators():
    det = pipeline.resolve_image_type(
        'tag',
        tag_probe={'brand': 'X', 'article': 'AB123', 'raw': 'КАССОВЫЙ ЧЕК ИТОГ 500'},
        pyzbar_barcode=None,
        qr_data=None,
        total_amount=None,
        force_receipt=False,
    )
    assert det.image_type == 'receipt'


def test_resolve_image_type_force_receipt_overrides_tag():
    det = pipeline.resolve_image_type(
        'tag',
        tag_probe={'brand': 'X', 'article': 'AB123', 'raw': ''},
        pyzbar_barcode=None,
        qr_data=None,
        total_amount=None,
        force_receipt=True,
    )
    assert det.image_type == 'receipt'


def test_resolve_image_type_clothing_stays_clothing():
    det = pipeline.resolve_image_type(
        'clothing',
        tag_probe={'raw': ''},
        pyzbar_barcode=None,
        qr_data=None,
        total_amount=None,
        force_receipt=False,
    )
    assert det.image_type == 'clothing'
    assert not det.is_real_tag


def test_resolve_image_type_total_without_tag_signals_corrects_to_receipt():
    # image already 'tag' but a total exists and no real tag signals → receipt
    det = pipeline.resolve_image_type(
        'tag',
        tag_probe={'raw': ''},
        pyzbar_barcode=None,
        qr_data=None,
        total_amount=350.0,
        force_receipt=False,
    )
    assert det.image_type == 'receipt'


# ---------------------------------------------------------------------------
# ocr_attempts logging
# ---------------------------------------------------------------------------

def test_ensure_ocr_attempts_schema_idempotent(tmp_path: Path):
    conn = connect(tmp_path / 'a.db')
    pipeline.ensure_ocr_attempts_schema(conn)
    pipeline.ensure_ocr_attempts_schema(conn)  # second call must not raise
    cols = {r[1] for r in conn.execute('PRAGMA table_info(ocr_attempts)').fetchall()}
    assert {'image_sha', 'engine', 'status', 'elapsed_ms', 'error', 'image_type'} <= cols
    conn.close()


def test_log_ocr_attempt_inserts_row(tmp_path: Path):
    conn = connect(tmp_path / 'b.db')
    pipeline.log_ocr_attempt(
        conn, image_sha='abc', engine='tesseract',
        image_type='receipt', status='ok', elapsed_ms=120,
    )
    rows = conn.execute('SELECT image_sha, engine, status, elapsed_ms FROM ocr_attempts').fetchall()
    assert [tuple(r) for r in rows] == [('abc', 'tesseract', 'ok', 120)]
    conn.close()


def test_log_ocr_attempt_error_truncated(tmp_path: Path):
    conn = connect(tmp_path / 'c.db')
    pipeline.log_ocr_attempt(
        conn, image_sha='x', engine='vision', status='error',
        error='E' * 1000,
    )
    err = conn.execute('SELECT error FROM ocr_attempts').fetchone()[0]
    assert len(err) == 500
    conn.close()


def test_log_ocr_attempt_never_raises_on_bad_conn():
    class BadConn:
        def execute(self, *a, **k):
            raise RuntimeError('boom')

        def commit(self):
            raise RuntimeError('boom')

    # Must swallow the error rather than propagate.
    pipeline.log_ocr_attempt(BadConn(), image_sha='x', engine='e')


# ---------------------------------------------------------------------------
# file_sha256
# ---------------------------------------------------------------------------

def test_file_sha256(tmp_path: Path):
    p = tmp_path / 'f.bin'
    p.write_bytes(b'hello')
    import hashlib
    assert pipeline.file_sha256(str(p)) == hashlib.sha256(b'hello').hexdigest()


def test_file_sha256_missing_returns_none():
    assert pipeline.file_sha256('/nonexistent/path/xyz') is None
