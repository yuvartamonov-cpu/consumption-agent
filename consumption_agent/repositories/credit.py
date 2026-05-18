from __future__ import annotations

from typing import Any

from consumption.db import execute_with_retry


def ensure_credit_schema(conn) -> None:
    """Create credit alert tables and compatibility columns."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS credit_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            sender TEXT,
            sender_name TEXT,
            subject TEXT,
            body TEXT,
            payment_date TEXT,
            payment_amount REAL,
            currency TEXT DEFAULT 'RUB',
            detected_at TEXT DEFAULT (datetime('now')),
            notified_at TEXT,
            days_until_payment INTEGER,
            raw_message_id TEXT UNIQUE,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS credit_alert_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL REFERENCES credit_alerts(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            telegram_chat_id TEXT,
            telegram_message_id TEXT,
            is_test INTEGER DEFAULT 0,
            sent_at TEXT DEFAULT (datetime('now')),
            UNIQUE(alert_id, kind, is_test)
        );

        CREATE INDEX IF NOT EXISTS idx_credit_alerts_date
            ON credit_alerts(payment_date);
        CREATE INDEX IF NOT EXISTS idx_credit_alerts_notified
            ON credit_alerts(notified_at);
        CREATE INDEX IF NOT EXISTS idx_credit_alerts_active
            ON credit_alerts(is_active);
        CREATE INDEX IF NOT EXISTS idx_credit_alert_notifications_alert
            ON credit_alert_notifications(alert_id, kind, is_test);
        """
    )
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(credit_alerts)").fetchall()}
    for column, ddl in {
        "paid_confirmed_at": "ALTER TABLE credit_alerts ADD COLUMN paid_confirmed_at TEXT",
        "paid_confirmed_via": "ALTER TABLE credit_alerts ADD COLUMN paid_confirmed_via TEXT",
        "paid_note": "ALTER TABLE credit_alerts ADD COLUMN paid_note TEXT",
    }.items():
        if column not in existing_columns:
            conn.execute(ddl)
    conn.commit()


def insert_alert(
    conn,
    *,
    source: str,
    sender: str | None,
    sender_name: str | None,
    subject: str | None,
    body: str | None,
    payment_date: str | None,
    payment_amount: float | None,
    currency: str = "RUB",
    days_until_payment: int | None = None,
    raw_message_id: str = "",
) -> int | None:
    cur = execute_with_retry(
        conn,
        """
        INSERT OR IGNORE INTO credit_alerts (
            source, sender, sender_name, subject, body, payment_date,
            payment_amount, currency, detected_at, days_until_payment,
            raw_message_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
        """,
        (
            source,
            sender,
            sender_name,
            subject,
            body,
            payment_date,
            payment_amount,
            currency,
            days_until_payment,
            raw_message_id,
        ),
    )
    return cur.lastrowid or None


def get_alert_by_id(conn, alert_id: int) -> Any | None:
    return conn.execute("SELECT * FROM credit_alerts WHERE id = ?", (alert_id,)).fetchone()


def list_active(conn) -> list[Any]:
    return conn.execute(
        """
        SELECT *
        FROM credit_alerts
        WHERE is_active = 1
          AND payment_date IS NOT NULL
          AND paid_confirmed_at IS NULL
        ORDER BY payment_date ASC
        """
    ).fetchall()


def list_nearest(conn, *, limit: int = 3) -> list[Any]:
    return conn.execute(
        """
        SELECT *
        FROM credit_alerts
        WHERE payment_date IS NOT NULL
          AND is_active = 1
          AND paid_confirmed_at IS NULL
        ORDER BY ABS(julianday(payment_date) - julianday('now')) ASC, payment_date ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def record_notification(
    conn,
    alert_id: int,
    kind: str,
    *,
    telegram_chat_id: str | None = None,
    telegram_message_id: str | None = None,
    is_test: bool = False,
) -> None:
    execute_with_retry(
        conn,
        """
        INSERT OR IGNORE INTO credit_alert_notifications
        (alert_id, kind, telegram_chat_id, telegram_message_id, is_test)
        VALUES (?, ?, ?, ?, ?)
        """,
        (alert_id, kind, telegram_chat_id, telegram_message_id, 1 if is_test else 0),
    )
    if not is_test:
        execute_with_retry(
            conn,
            'UPDATE credit_alerts SET notified_at = datetime("now") WHERE id = ?',
            (alert_id,),
        )


def was_notification_sent(conn, alert_id: int, kind: str, *, is_test: bool = False) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM credit_alert_notifications
        WHERE alert_id = ? AND kind = ? AND is_test = ?
        LIMIT 1
        """,
        (alert_id, kind, 1 if is_test else 0),
    ).fetchone()
    return bool(row)


def confirm_paid(conn, alert_id: int, *, via: str = "telegram_button", note: str = "") -> bool:
    cur = execute_with_retry(
        conn,
        """
        UPDATE credit_alerts
        SET paid_confirmed_at = datetime('now'),
            paid_confirmed_via = ?,
            paid_note = ?,
            is_active = 0
        WHERE id = ? AND paid_confirmed_at IS NULL
        """,
        (via, note, alert_id),
    )
    return cur.rowcount > 0


def mark_notified(conn, alert_id: int) -> None:
    execute_with_retry(
        conn,
        'UPDATE credit_alerts SET notified_at = datetime("now") WHERE id = ?',
        (alert_id,),
    )
