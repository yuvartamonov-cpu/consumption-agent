import asyncio
import sqlite3
from types import SimpleNamespace

import telegram_bot


def _setup_conn():
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
        """
        INSERT INTO items (name, purchase_date, warranty_months, warranty_until, deleted_at)
        VALUES ('Test item', '2024-03-15', 12, NULL, NULL)
        """
    )
    conn.commit()
    return conn


def test_set_warranty_updates_months_and_recomputes_until(monkeypatch):
    conn = _setup_conn()

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
    ctx = SimpleNamespace(args=["1", "24"])

    asyncio.run(telegram_bot.cmd_set_warranty(update, ctx))

    row = conn.execute("SELECT warranty_months, warranty_until FROM items WHERE id=1").fetchone()
    assert row["warranty_months"] == 24
    assert row["warranty_until"] == "2026-03-15"
    assert replies[-1] == "OK: warranty_months=24, warranty_until=2026-03-15"
