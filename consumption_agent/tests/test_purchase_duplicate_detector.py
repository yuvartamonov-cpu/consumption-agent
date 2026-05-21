import purchase_duplicate_detector as pdd
import sqlite3


def test_format_duplicate_question_escapes_markdown_sensitive_text():
    group = {
        'store_name': 'FUNGRAD_KHODYNSK_P_QR',
        'purchase_date': '2026-05-16',
        'purchases': [
            {'id': 1, 'source': 'sms_sber', 'amount': 1800.0},
            {'id': 2, 'source': 'Mail.ru_Zorea', 'amount': 1800.0},
        ],
    }

    text = pdd.format_duplicate_question(group)

    assert 'FUNGRAD\\_KHODYNSK\\_P\\_QR - 2026-05-16' in text
    assert '📱 SMS\\(Сбер\\): *1800 ₽*' in text


def test_auto_resolve_prefers_named_store_over_unknown_for_same_source_amount():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL,
            total_amount REAL,
            store_name TEXT,
            source TEXT,
            notes TEXT,
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO purchases (purchase_date,total_amount,store_name,source,notes) VALUES (?,?,?,?,?)",
        ("2026-05-20", 852.0, "Интернет Решения, ООО", "telegram_photo", "vision result"),
    )
    conn.execute(
        "INSERT INTO purchases (purchase_date,total_amount,store_name,source,notes) VALUES (?,?,?,?,?)",
        ("2026-05-20", 852.0, "Неизвестный", "telegram_photo", "vision result"),
    )

    groups = pdd.find_suspected_duplicates(conn, days_back=7)
    target = next(group for group in groups if group.get("prefer_named_store"))
    assert pdd.auto_resolve_if_email_dedup(conn, target) is None

    deleted = conn.execute(
        "SELECT deleted_at FROM purchases WHERE store_name = 'Неизвестный'"
    ).fetchone()[0]
    kept = conn.execute(
        "SELECT deleted_at FROM purchases WHERE store_name = 'Интернет Решения, ООО'"
    ).fetchone()[0]
    assert deleted is not None
    assert kept is None
