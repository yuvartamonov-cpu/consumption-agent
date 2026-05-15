"""Tests for backfill_ozon_items.py — mocked IMAP, no network required."""
import email
import email.mime.multipart
import email.mime.text
import sqlite3
import sys
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backfill_ozon_items import _fetch_html, _insert_items, _purchases_without_items, run_backfill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OZON_HTML = """
<table>
  <tr><td>Гречка ядрица 900 г</td><td>89,90 ₽</td></tr>
  <tr><td>Шампунь Head &amp; Shoulders 400 мл</td><td>349,00 ₽</td></tr>
  <tr><td>Итого</td><td>438,90 ₽</td></tr>
</table>
"""


def _make_multipart_html(html_body):
    """Build a raw IMAP-style multipart email with the given HTML part."""
    msg = email.mime.multipart.MIMEMultipart('alternative')
    msg['Subject'] = 'Ваш чек от Ozon'
    msg.attach(email.mime.text.MIMEText('<p>plain</p>', 'plain'))
    msg.attach(email.mime.text.MIMEText(html_body, 'html'))
    return msg.as_bytes()


def _init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL DEFAULT 'default',
            purchase_date TEXT NOT NULL,
            total_amount REAL,
            currency TEXT DEFAULT 'RUB',
            payment_method TEXT,
            source TEXT,
            store_name TEXT,
            order_number TEXT,
            receipt_url TEXT,
            email_message_id TEXT UNIQUE,
            notes TEXT,
            data_origin TEXT DEFAULT 'local',
            created_at TEXT DEFAULT (datetime('now')),
            deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category_id TEXT,
            status TEXT DEFAULT 'in_use',
            quantity REAL DEFAULT 1,
            unit TEXT DEFAULT 'шт',
            purchase_price REAL,
            purchase_date TEXT,
            purchase_source TEXT,
            purchase_id INTEGER,
            data_origin TEXT DEFAULT 'local',
            deleted_at TEXT
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Test: empty mailbox — purchase has UID but IMAP fetch returns nothing
# ---------------------------------------------------------------------------

def test_empty_mailbox_fetch_returns_no_items():
    """When IMAP returns empty data, zero items should be inserted."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        _init_schema(conn)
        conn.execute(
            "INSERT INTO purchases (purchase_date, source, email_message_id) VALUES ('2025-01-01','ozon','uid_999')"
        )
        conn.commit()

        # Simulate IMAP returning empty fetch data
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = (None, [None])

        imap_cfg = {'host': 'imap.gmail.com', 'port': 993, 'user': 'test@example.com', 'password': 'x'}

        with patch('backfill_ozon_items.imaplib.IMAP4_SSL') as MockIMAP:
            instance = MockIMAP.return_value
            instance.login.return_value = ('OK', [])
            instance.select.return_value = ('OK', [])
            instance.fetch.return_value = (None, [None])
            instance.logout.return_value = ('OK', [])

            processed, added = run_backfill(db_path=db_path, imap_cfg=imap_cfg)

        assert processed == 1
        assert added == 0
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert item_count == 0
        conn.close()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Test: real fetch — IMAP returns Ozon HTML, items get inserted
# ---------------------------------------------------------------------------

def test_real_fetch_inserts_items_from_ozon_html():
    """When IMAP returns a valid Ozon HTML email, items are parsed and inserted."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        _init_schema(conn)
        conn.execute(
            "INSERT INTO purchases (purchase_date, source, email_message_id) VALUES ('2025-06-15','ozon','uid_42')"
        )
        conn.commit()
        purchase_id = conn.execute("SELECT id FROM purchases WHERE email_message_id='uid_42'").fetchone()[0]

        raw_bytes = _make_multipart_html(OZON_HTML)

        imap_cfg = {'host': 'imap.gmail.com', 'port': 993, 'user': 'test@example.com', 'password': 'x'}

        with patch('backfill_ozon_items.imaplib.IMAP4_SSL') as MockIMAP:
            instance = MockIMAP.return_value
            instance.login.return_value = ('OK', [])
            instance.select.return_value = ('OK', [])
            instance.fetch.return_value = ('OK', [(b'42 (BODY[] {123}', raw_bytes)])
            instance.logout.return_value = ('OK', [])

            processed, added = run_backfill(db_path=db_path, imap_cfg=imap_cfg)

        assert processed == 1
        assert added == 2  # two items in OZON_HTML
        items = conn.execute(
            "SELECT name FROM items WHERE purchase_id = ? ORDER BY id", (purchase_id,)
        ).fetchall()
        names = [r[0] for r in items]
        assert 'Гречка ядрица 900 г' in names
        assert 'Шампунь Head & Shoulders 400 мл' in names
        conn.close()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Test: idempotency — running twice does not duplicate items
# ---------------------------------------------------------------------------

def test_backfill_is_idempotent():
    """Running backfill twice on the same purchase inserts items only once."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        _init_schema(conn)
        conn.execute(
            "INSERT INTO purchases (purchase_date, source, email_message_id) VALUES ('2025-06-15','ozon','uid_77')"
        )
        conn.commit()

        raw_bytes = _make_multipart_html(OZON_HTML)

        imap_cfg = {'host': 'imap.gmail.com', 'port': 993, 'user': 'test@example.com', 'password': 'x'}

        def _run():
            with patch('backfill_ozon_items.imaplib.IMAP4_SSL') as MockIMAP:
                instance = MockIMAP.return_value
                instance.login.return_value = ('OK', [])
                instance.select.return_value = ('OK', [])
                instance.fetch.return_value = ('OK', [(b'77 (BODY[])', raw_bytes)])
                instance.logout.return_value = ('OK', [])
                return run_backfill(db_path=db_path, imap_cfg=imap_cfg)

        _run()
        # Second run: purchase now has items, so _purchases_without_items returns empty
        _, added2 = _run()
        assert added2 == 0
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert total == 2
        conn.close()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Test: dry-run does not write to DB
# ---------------------------------------------------------------------------

def test_dry_run_does_not_insert():
    """--dry-run reports counts but makes no DB changes."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        _init_schema(conn)
        conn.execute(
            "INSERT INTO purchases (purchase_date, source, email_message_id) VALUES ('2025-06-15','ozon','uid_55')"
        )
        conn.commit()

        raw_bytes = _make_multipart_html(OZON_HTML)
        imap_cfg = {'host': 'imap.gmail.com', 'port': 993, 'user': 'test@example.com', 'password': 'x'}

        with patch('backfill_ozon_items.imaplib.IMAP4_SSL') as MockIMAP:
            instance = MockIMAP.return_value
            instance.login.return_value = ('OK', [])
            instance.select.return_value = ('OK', [])
            instance.fetch.return_value = ('OK', [(b'55 (BODY[])', raw_bytes)])
            instance.logout.return_value = ('OK', [])
            processed, added = run_backfill(db_path=db_path, dry_run=True, imap_cfg=imap_cfg)

        assert processed == 1
        assert added == 2
        actual = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        assert actual == 0, "dry-run must not write to DB"
        conn.close()
    finally:
        os.unlink(db_path)
