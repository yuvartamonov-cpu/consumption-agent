from __future__ import annotations


def insert_telegram_photo_purchase(conn, *, purchase_date: str, total_amount: float | None) -> int:
    cur = conn.execute(
        "INSERT INTO purchases (purchase_date, total_amount, source, data_origin) "
        "VALUES (?, ?, 'telegram_photo', 'telegram_photo')",
        (purchase_date, total_amount),
    )
    return cur.lastrowid
