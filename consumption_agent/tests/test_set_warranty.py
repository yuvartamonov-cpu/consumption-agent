import asyncio
import sqlite3
from types import SimpleNamespace

import telegram_bot


def _setup_conn(warranty_until=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            purchase_date TEXT,
            warranty_months INTEGER,
            warranty_until TEXT,
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO items (name, purchase_date, warranty_months, warranty_until, deleted_at) "
        "VALUES ('Test item', '2024-03-15', 12, ?, NULL)",
        (warranty_until,),
    )
    conn.commit()
    return conn


def _run_set_warranty(monkeypatch, conn, args):
    class ConnProxy:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def close(self):
            return None

    monkeypatch.setattr(telegram_bot, "get_db", lambda: ConnProxy(conn))

    replies = []

    async def reply_text(message, **kwargs):
        replies.append(message)

    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    ctx = SimpleNamespace(args=args)
    asyncio.run(telegram_bot.cmd_set_warranty(update, ctx))
    return replies


def test_set_warranty_updates_months_and_recomputes_until(monkeypatch):
    conn = _setup_conn()
    replies = _run_set_warranty(monkeypatch, conn, ["1", "24"])
    row = conn.execute("SELECT warranty_months, warranty_until FROM items WHERE id=1").fetchone()
    assert row["warranty_months"] == 24
    assert row["warranty_until"] == "2026-03-15"
    assert replies[-1] == "OK: warranty_months=24, warranty_until=2026-03-15"


def test_set_warranty_recomputes_existing_until(monkeypatch):
    """Bug B regression: /set_warranty must recompute warranty_until even
    when it was already set (update_warranty_until skips non-NULL rows)."""
    conn = _setup_conn(warranty_until="2025-03-15")  # stale value from old 12mo
    replies = _run_set_warranty(monkeypatch, conn, ["1", "36"])
    row = conn.execute("SELECT warranty_months, warranty_until FROM items WHERE id=1").fetchone()
    assert row["warranty_months"] == 36
    assert row["warranty_until"] == "2027-03-15", (
        f"stale warranty_until not recomputed: got {row['warranty_until']!r}"
    )
    assert replies[-1] == "OK: warranty_months=36, warranty_until=2027-03-15"
