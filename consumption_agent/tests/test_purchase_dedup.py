import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from purchase_dedup import (
    canonical_store_name,
    email_event_details,
    extract_delivery_fee,
    extract_event_time,
    is_duplicate_purchase,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL,
            total_amount REAL,
            store_name TEXT,
            source TEXT,
            email_message_id TEXT,
            notes TEXT,
            deleted_at TEXT
        )
        """
    )
    return conn


def test_extract_event_time_reads_hhmm():
    assert extract_event_time("Покупка 20:20 Самокат") == "20:20"


def test_canonical_store_name_maps_umny_reteyl_to_samokat():
    assert canonical_store_name("ООО УМНЫЙ РИТЕЙЛ") == "Самокат"


def test_extract_delivery_fee_reads_delivery_note():
    assert extract_delivery_fee("Самокат: чек (доставка 92 ₽; время 20:20)") == 92.0


def test_email_event_details_prefers_parsed_cheque_datetime():
    date_str, event_time = email_event_details("16.05.2026 20:20", "Sat, 16 May 2026 20:25:00 +0300")
    assert date_str == "2026-05-16"
    assert event_time == "20:20"


def test_duplicate_purchase_same_store_amount_and_time():
    conn = _db()
    conn.execute(
        "INSERT INTO purchases (purchase_date,total_amount,store_name,source,notes) VALUES (?,?,?,?,?)",
        ("2026-05-16", 2792.0, "Самокат", "sms_sber", "SMS: ... 20:20 ..."),
    )
    assert is_duplicate_purchase(conn, "2026-05-16", 2792.0, "Самокат", event_time="20:20")


def test_same_store_amount_without_time_is_not_auto_duplicate():
    conn = _db()
    conn.execute(
        "INSERT INTO purchases (purchase_date,total_amount,store_name,source,notes) VALUES (?,?,?,?,?)",
        ("2026-05-16", 2792.0, "Самокат", "sms_sber", "SMS without time"),
    )
    assert not is_duplicate_purchase(conn, "2026-05-16", 2792.0, "Самокат", event_time=None)


def test_duplicate_purchase_by_email_message_id():
    conn = _db()
    conn.execute(
        "INSERT INTO purchases (purchase_date,total_amount,store_name,source,email_message_id,notes) VALUES (?,?,?,?,?,?)",
        ("2026-05-16", 2884.0, "Самокат", "Mail.ru Zorea", "<msg-1>", "Самокат: чек"),
    )
    assert is_duplicate_purchase(
        conn,
        "2026-05-16",
        2884.0,
        "Самокат",
        event_time="21:00",
        email_msg_id="<msg-1>",
    )


def test_duplicate_purchase_matches_email_total_with_delivery_against_sms_amount():
    conn = _db()
    conn.execute(
        "INSERT INTO purchases (purchase_date,total_amount,store_name,source,notes) VALUES (?,?,?,?,?)",
        ("2026-05-16", 2884.0, "Самокат", "Mail.ru Zorea", "Самокат: чек (доставка 92 ₽; время 20:20)"),
    )
    assert is_duplicate_purchase(
        conn,
        "2026-05-16",
        2792.0,
        "Самокат",
        event_time="20:20",
    )


def test_duplicate_purchase_matches_sms_amount_when_new_email_has_delivery_fee():
    conn = _db()
    conn.execute(
        "INSERT INTO purchases (purchase_date,total_amount,store_name,source,notes) VALUES (?,?,?,?,?)",
        ("2026-05-16", 2792.0, "Самокат", "sms_sber", "SMS: ... 20:20 ..."),
    )
    assert is_duplicate_purchase(
        conn,
        "2026-05-16",
        2884.0,
        "Самокат",
        event_time="20:20",
        delivery_fee=92.0,
    )
