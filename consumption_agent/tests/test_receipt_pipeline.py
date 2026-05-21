from __future__ import annotations

from pathlib import Path

from consumption.db import connect
from scripts.receipt_ocr import ReceiptResult
from services.receipt_pipeline import (
    StructuredReceipt,
    StructuredReceiptItem,
    persist_receipt,
    process_source,
)


FIXTURES = Path(__file__).parent / "fixtures" / "receipt_samples"


def _make_db(path: Path):
    conn = connect(path)
    conn.executescript(
        """
        CREATE TABLE categories (
            id TEXT PRIMARY KEY,
            slug TEXT UNIQUE,
            name TEXT
        );
        CREATE TABLE purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL,
            total_amount REAL,
            source TEXT,
            store_name TEXT,
            order_number TEXT,
            receipt_url TEXT,
            email_message_id TEXT UNIQUE,
            notes TEXT,
            data_origin TEXT DEFAULT 'local',
            deleted_at TEXT
        );
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id TEXT,
            name TEXT NOT NULL,
            brand TEXT,
            sku TEXT,
            status TEXT DEFAULT 'in_use',
            quantity REAL DEFAULT 1,
            purchase_date TEXT,
            purchase_price REAL,
            purchase_currency TEXT DEFAULT 'RUB',
            purchase_id INTEGER,
            is_delivery INTEGER DEFAULT 0,
            data_origin TEXT DEFAULT 'local',
            updated_at TEXT DEFAULT (datetime('now')),
            deleted_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO categories (id, slug, name) VALUES ('other', 'other', 'Other')")
    conn.execute("INSERT INTO categories (id, slug, name) VALUES ('service', 'service', 'Service')")
    conn.execute("INSERT INTO categories (id, slug, name) VALUES ('cat_food', 'food', 'Food')")
    conn.commit()
    return conn


def test_text_pipeline_normalizes_delivery_fixture():
    receipt = process_source(str(FIXTURES / "ozon_delivery_ocr.sample"), input_type="text_file")

    assert receipt.store == "Ozon"
    assert receipt.date == "2026-05-12"
    assert receipt.total == 959.90
    assert [item.name for item in receipt.product_items] == [
        "Молоко пастеризованное 3.2%",
        "Конструктор Гарри Поттер 1176 деталей",
    ]
    assert receipt.delivery_total == 149.00
    assert receipt.delivery_items[0].name == "Курьерская доставка"


def test_text_pipeline_marks_service_fee_as_delivery():
    receipt = process_source(str(FIXTURES / "service_fee_text.sample"), input_type="text_file")

    assert receipt.store == "Самокат"
    assert len(receipt.product_items) == 2
    assert receipt.delivery_total == 29.00
    assert receipt.delivery_items[0].name == "Сервисный сбор"


def test_dry_run_matches_without_writing(tmp_path):
    db_path = tmp_path / "consumption.db"
    conn = _make_db(db_path)
    conn.execute(
        "INSERT INTO items (name, brand, sku, category_id) VALUES (?, '', '', 'other')",
        ("Молоко пастеризованное 3.2%",),
    )
    conn.commit()

    receipt = process_source(str(FIXTURES / "ozon_delivery_ocr.sample"), input_type="text_file")
    result = persist_receipt(conn, receipt, dry_run=True)

    assert result.dry_run is True
    assert receipt.product_items[0].matched_item_id == 1
    assert conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM items WHERE purchase_id IS NOT NULL").fetchone()[0] == 0
    conn.close()


def test_apply_creates_purchase_items_and_delivery(tmp_path):
    db_path = tmp_path / "consumption.db"
    conn = _make_db(db_path)
    conn.execute(
        "INSERT INTO items (name, brand, sku, category_id) VALUES (?, '', '', 'other')",
        ("Молоко пастеризованное 3.2%",),
    )
    conn.commit()

    receipt = process_source(str(FIXTURES / "ozon_delivery_ocr.sample"), input_type="text_file")
    result = persist_receipt(conn, receipt, dry_run=False)

    assert result.purchase_id is not None
    assert result.matched_item_ids == [1]
    assert len(result.created_item_ids) == 2
    delivery = conn.execute("SELECT name, purchase_price FROM items WHERE is_delivery = 1").fetchone()
    assert delivery["name"] == "Курьерская доставка"
    assert delivery["purchase_price"] == 149.00
    linked = conn.execute("SELECT purchase_id, purchase_price FROM items WHERE id = 1").fetchone()
    assert linked["purchase_id"] == result.purchase_id
    assert linked["purchase_price"] == 89.90
    conn.close()


def test_apply_uses_llm_category_for_new_receipt_items_when_confident(tmp_path, monkeypatch):
    db_path = tmp_path / "consumption.db"
    conn = _make_db(db_path)

    def fake_suggest(conn, item_name, *, fallback_category_id, min_confidence=60):
        assert item_name == "Бананы"
        assert fallback_category_id == "other"
        return {
            "category_id": "cat_food",
            "confidence": 88,
            "needs_review": False,
            "options": [{"category_id": "cat_food", "category_name": "Food", "confidence": 88}],
        }

    monkeypatch.setattr("services.receipt_pipeline._classify_receipt_item_category", fake_suggest)

    receipt = StructuredReceipt(
        store="Тест",
        date="2026-05-21",
        total=120.0,
        items=[StructuredReceiptItem(name="Бананы", price=120.0)],
    )
    result = persist_receipt(conn, receipt, dry_run=False)

    row = conn.execute(
        "SELECT name, category_id FROM items WHERE id = ?",
        (result.created_item_ids[0],),
    ).fetchone()
    assert row["name"] == "Бананы"
    assert row["category_id"] == "cat_food"
    conn.close()


def test_apply_keeps_other_when_llm_category_is_low_confidence(tmp_path, monkeypatch):
    db_path = tmp_path / "consumption.db"
    conn = _make_db(db_path)

    def fake_suggest(conn, item_name, *, fallback_category_id, min_confidence=60):
        return {
            "category_id": fallback_category_id,
            "confidence": 41,
            "needs_review": True,
            "options": [
                {"category_id": "cat_food", "category_name": "Food", "confidence": 41},
                {"category_id": "other", "category_name": "Other", "confidence": 35},
            ],
        }

    monkeypatch.setattr("services.receipt_pipeline._classify_receipt_item_category", fake_suggest)

    receipt = StructuredReceipt(
        store="Тест",
        date="2026-05-21",
        total=321.0,
        items=[StructuredReceiptItem(name="Неизвестный товар", price=321.0)],
    )
    result = persist_receipt(conn, receipt, dry_run=False)

    row = conn.execute(
        "SELECT name, category_id FROM items WHERE id = ?",
        (result.created_item_ids[0],),
    ).fetchone()
    assert row["name"] == "Неизвестный товар"
    assert row["category_id"] == "other"
    assert len(result.category_reviews) == 1
    assert result.category_reviews[0]["item_name"] == "Неизвестный товар"
    conn.close()


def test_image_pipeline_recovers_store_from_ocr_when_vision_returns_unknown(tmp_path, monkeypatch):
    image_path = tmp_path / "receipt.jpg"
    image_path.write_bytes(b"fake")

    weak_ocr = ReceiptResult(
        shop="",
        date="2026-05-20",
        total=None,
        items=[],
        delivery_cost=0.0,
        raw_text="Ленинградка\nГОСТЕВОЙ СЧЕТ\nЗал: 1 этаж\nНаименование\nИТОГО К ОПЛАТЕ: 4 690,00",
        ocr_score=10,
    )

    monkeypatch.setattr("services.receipt_pipeline.receipt_ocr.process_receipt", lambda _path: weak_ocr)
    monkeypatch.setattr("services.receipt_pipeline.run_easyocr_text", lambda _path: ("", 0))
    monkeypatch.setattr(
        "vision_receipt.recognize_receipt",
        lambda _path: {
            "store": "Неизвестный",
            "date": "2026-05-20",
            "total": 4690.0,
            "items": [{"name": "Шурпа", "qty": 1, "price": 790.0}],
        },
    )

    receipt = process_source(str(image_path), easyocr_fallback=False, vision_fallback=True)

    assert receipt.store == "Ленинградка"
    assert receipt.total == 4690.0
