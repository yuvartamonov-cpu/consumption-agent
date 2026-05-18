import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from init_db import create_new_schema
from repositories import alerts as alerts_repo
from repositories import credit as credit_repo


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def test_alerts_repository_create_and_status_flow():
    conn = _conn()
    try:
        create_new_schema(conn)
        alert_id = alerts_repo.create_alert(
            conn,
            alert_type="warranty_expiring",
            title="Warranty",
            message="Warranty expires soon",
        )
        conn.commit()

        pending = alerts_repo.list_pending(conn)
        assert [row["id"] for row in pending] == [alert_id]

        alerts_repo.mark_sent(conn, alert_id)
        conn.commit()
        row = conn.execute("SELECT status, sent_at FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        assert row["status"] == "sent"
        assert row["sent_at"] is not None
    finally:
        conn.close()


def test_credit_repository_schema_and_notification_flow():
    conn = _conn()
    try:
        credit_repo.ensure_credit_schema(conn)
        alert_id = credit_repo.insert_alert(
            conn,
            source="email",
            sender="bank@example.com",
            sender_name="bank",
            subject="payment due",
            body="pay soon",
            payment_date="2026-05-20",
            payment_amount=1234.5,
            raw_message_id="msg-1",
        )
        conn.commit()

        assert alert_id is not None
        assert credit_repo.get_alert_by_id(conn, alert_id)["sender_name"] == "bank"

        credit_repo.record_notification(conn, alert_id, "advance", telegram_message_id="42")
        conn.commit()
        assert credit_repo.was_notification_sent(conn, alert_id, "advance")

        assert credit_repo.confirm_paid(conn, alert_id, via="test")
        conn.commit()
        row = credit_repo.get_alert_by_id(conn, alert_id)
        assert row["is_active"] == 0
        assert row["paid_confirmed_at"] is not None
    finally:
        conn.close()
