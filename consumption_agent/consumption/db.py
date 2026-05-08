"""
db.py — централизованный доступ к БД consumption.db.
Использование:
    from consumption.db import connect, DB_PATH
    conn = connect()
"""
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'consumption.db')


def connect(timeout: float = 10.0, max_retries: int = 3, delay: float = 1.0) -> sqlite3.Connection:
    """
    Подключение к БД с retry при блокировке.
    Возвращает sqlite3.Connection с row_factory = Row.
    """
    last_err = None
    for i in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=timeout)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" in str(e) and i < max_retries - 1:
                time.sleep(delay * (2 ** i))
                continue
            raise
    raise last_err  # type: ignore


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    """Получить значение из таблицы settings (создаст таблицу если её нет)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return row[0]


def set_setting(conn: sqlite3.Connection, key: str, value: str):
    """Сохранить значение в settings."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, str(value))
    )
