from __future__ import annotations

from pathlib import Path

from consumption.db import connect
from services.receipt_pipeline import persist_receipt, process_source


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
