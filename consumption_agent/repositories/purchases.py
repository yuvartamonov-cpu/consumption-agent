from __future__ import annotations


def insert_receipt_purchase(
    conn,
    *,
    purchase_date: str,
    total_amount: float | None,
    source: str,
    data_origin: str,
    store_name: str | None = None,
    order_number: str | None = None,
    receipt_url: str | None = None,
    email_message_id: str | None = None,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO purchases (
            purchase_date, total_amount, source, store_name, order_number,
            receipt_url, email_message_id, notes, data_origin
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            purchase_date,
            total_amount,
            source,
            store_name,
            order_number,
            receipt_url,
            email_message_id,
            notes,
            data_origin,
        ),
    )
    if cur.lastrowid:
        return cur.lastrowid
    if email_message_id:
        row = conn.execute(
            "SELECT id FROM purchases WHERE email_message_id = ? AND deleted_at IS NULL",
            (email_message_id,),
        ).fetchone()
        if row:
            return row[0]
    raise RuntimeError("receipt purchase insert was ignored and no existing purchase was found")


def insert_telegram_photo_purchase(conn, *, purchase_date: str, total_amount: float | None) -> int:
    return insert_receipt_purchase(
        conn,
        purchase_date=purchase_date,
        total_amount=total_amount,
        source="telegram_photo",
        data_origin="telegram_photo",
    )
