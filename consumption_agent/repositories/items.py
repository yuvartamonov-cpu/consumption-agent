from __future__ import annotations

import json
from datetime import date
from typing import Any


def ensure_delivery_column(conn) -> None:
    try:
        conn.execute("ALTER TABLE items ADD COLUMN is_delivery INTEGER DEFAULT 0")
    except Exception as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def get_category_id(conn, slug: str, fallback_slug: str | None = "other") -> Any:
    row = conn.execute("SELECT id FROM categories WHERE slug=? LIMIT 1", (slug,)).fetchone()
    if row:
        return row[0]
    if fallback_slug:
        row = conn.execute("SELECT id FROM categories WHERE slug=? LIMIT 1", (fallback_slug,)).fetchone()
        if row:
            return row[0]
    return None


def insert_item(conn, **fields) -> int:
    columns = list(fields)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO items ({', '.join(columns)}) VALUES ({placeholders})"
    cur = conn.execute(sql, tuple(fields[column] for column in columns))
    return cur.lastrowid


def insert_vision_photo_item(
    conn,
    *,
    name: str,
    brand: str | None,
    purchase_price: float | None,
    category_id,
    attributes: str,
    notes: str,
    purchase_date: str | None = None,
) -> int:
    return insert_item(
        conn,
        name=name,
        brand=brand,
        purchase_price=purchase_price,
        category_id=category_id,
        attributes=attributes,
        notes=notes,
        data_origin="vision_photo",
        purchase_date=purchase_date or date.today().isoformat(),
    )


def insert_tag_item(conn, *, tag: dict, item_name: str, price_rub: float | None, category_id, purchase_date: str) -> int:
    attrs = json.dumps(
        {
            "size": tag.get("size"),
            "color": tag.get("color"),
            "barcode": tag.get("barcode"),
            "ocr_raw": tag.get("raw", "")[:250],
        },
        ensure_ascii=False,
    )
    return insert_item(
        conn,
        name=item_name,
        brand=tag.get("brand"),
        model=tag.get("model"),
        sku=tag.get("article"),
        purchase_price=price_rub,
        purchase_currency=tag.get("currency", "RUB"),
        purchase_date=purchase_date,
        attributes=attrs,
        category_id=category_id,
        data_origin="telegram_tag",
    )


def insert_receipt_item(
    conn,
    *,
    name: str,
    price: float | None,
    purchase_date: str,
    category_id,
    purchase_id: int | None,
    is_delivery: bool = False,
    data_origin: str = "telegram_photo",
    status: str = "in_use",
) -> int:
    return insert_item(
        conn,
        name=name,
        purchase_price=price,
        purchase_date=purchase_date,
        category_id=category_id,
        data_origin=data_origin,
        purchase_id=purchase_id,
        is_delivery=1 if is_delivery else 0,
        status=status,
    )


def update_purchase_details(
    conn,
    *,
    item_id: int,
    purchase_id: int,
    purchase_price: float | None,
    purchase_date: str,
    quantity: float | int = 1,
    purchase_currency: str = "RUB",
) -> None:
    conn.execute(
        """
        UPDATE items
        SET purchase_id = ?,
            purchase_price = ?,
            purchase_date = ?,
            purchase_currency = ?,
            quantity = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (purchase_id, purchase_price, purchase_date, purchase_currency, quantity, item_id),
    )


def insert_manual_item(
    conn,
    *,
    name: str,
    brand: str | None,
    category_id,
    replace_months: int | None,
    replace_days: int | None,
    notes: str,
) -> int:
    return insert_item(
        conn,
        name=name,
        brand=brand,
        category_id=category_id,
        status="in_use",
        replace_after_months=replace_months,
        replace_after_days=replace_days,
        purchase_date=date.today().isoformat(),
        notes=notes,
        data_origin="manual",
    )


def update_item_vision_metadata(conn, *, item_id: int, brand: str | None, attributes: str, notes: str | None = None) -> None:
    if notes is None:
        conn.execute(
            "UPDATE items SET brand=COALESCE(?, brand), attributes=? WHERE id=?",
            (brand, attributes, item_id),
        )
        return
    conn.execute(
        "UPDATE items SET brand=COALESCE(?, brand), attributes=?, notes=? WHERE id=?",
        (brand, attributes, notes, item_id),
    )


def mark_replaced(conn, item_id: int) -> None:
    conn.execute(
        "UPDATE items SET status = 'replaced', updated_at = datetime('now') WHERE id = ?",
        (item_id,),
    )


def soft_delete(conn, item_id: int) -> None:
    conn.execute(
        "UPDATE items SET deleted_at = datetime('now'), status = 'disposed' WHERE id = ?",
        (item_id,),
    )
