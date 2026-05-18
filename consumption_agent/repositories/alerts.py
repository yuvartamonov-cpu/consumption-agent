from __future__ import annotations

from typing import Any

from consumption.db import execute_with_retry


def create_alert(
    conn,
    *,
    alert_type: str,
    title: str,
    message: str | None = None,
    item_id: int | None = None,
    purchase_id: int | None = None,
    scheduled_at: str | None = None,
    status: str = "pending",
    profile_id: str = "default",
) -> int:
    cur = execute_with_retry(
        conn,
        """
        INSERT INTO alerts (
            profile_id, item_id, purchase_id, alert_type, title,
            message, scheduled_at, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (profile_id, item_id, purchase_id, alert_type, title, message, scheduled_at, status),
    )
    return cur.lastrowid


def list_alerts(conn, *, status: str = "pending", limit: int = 50) -> list[Any]:
    if status == "all":
        return conn.execute(
            """
            SELECT *
            FROM alerts
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return conn.execute(
        """
        SELECT *
        FROM alerts
        WHERE status = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()


def list_pending(conn, *, limit: int = 50) -> list[Any]:
    return list_alerts(conn, status="pending", limit=limit)


def mark_sent(conn, alert_id: int) -> None:
    execute_with_retry(
        conn,
        """
        UPDATE alerts
        SET status = 'sent',
            sent_at = datetime('now')
        WHERE id = ?
        """,
        (alert_id,),
    )


def update_status(conn, alert_id: int, status: str) -> None:
    execute_with_retry(
        conn,
        "UPDATE alerts SET status = ? WHERE id = ?",
        (status, alert_id),
    )


def dismiss(conn, alert_id: int) -> None:
    update_status(conn, alert_id, "dismissed")


def mark_actioned(conn, alert_id: int) -> None:
    update_status(conn, alert_id, "actioned")
