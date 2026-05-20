"""Receipt fixture coverage for the photo pipeline (no Telegram, no live OCR).

Fixtures are sanitized OCR text samples consumed via
``process_source(..., input_type="text_file")``. They exercise the delivery /
service-fee separation and the weak-receipt → Vision-fallback decision.
"""

from __future__ import annotations

from pathlib import Path

from services.receipt_pipeline import is_weak_receipt, process_source

FIXTURES = Path(__file__).parent / "fixtures" / "receipt_samples"


def _parse(name: str):
    return process_source(str(FIXTURES / name), input_type="text_file")


def test_samokat_products_and_zero_delivery():
    r = _parse("samokat_ofd.sample")
    assert r.total == 273.9
    assert len(r.product_items) == 3
    # Free delivery (0,00) is not surfaced as a delivery line.
    assert r.delivery_total == 0.0
    assert not is_weak_receipt(r)


def test_yandex_delivery_is_first_class():
    r = _parse("yandex_market.sample")
    # The courier delivery line is separated from product items.
    assert r.delivery_total == 199.0
    assert len(r.delivery_items) == 1
    assert all("доставк" not in (i.name or "").lower() for i in r.product_items)
    assert len(r.product_items) >= 1


def test_blurry_photo_is_weak_and_triggers_fallback():
    r = _parse("blurry_photo.sample")
    # Degraded OCR → weak receipt → caller routes to Vision fallback.
    assert is_weak_receipt(r)
    assert r.total is None
    assert len(r.product_items) == 0
