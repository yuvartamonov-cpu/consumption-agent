"""
db.py — централизованный доступ к БД consumption.db.
Использование:
    from consumption.db import connect, DB_PATH
    conn = connect()
"""
import os
import sqlite3
import time
from collections.abc import Sequence
from os import PathLike

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'consumption.db')


def connect(
    db_path: str | PathLike[str] = DB_PATH,
    timeout: float = 10.0,
    max_retries: int = 3,
    delay: float = 1.0,
    busy_timeout_ms: int = 10000,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """
    Подключение к БД с retry при блокировке.
    Возвращает sqlite3.Connection с row_factory = Row.
    Включает оптимальные PRAGMA для производительности и надёжности.
    """
    last_err = None
    for i in range(max_retries):
        try:
            conn = sqlite3.connect(
                str(db_path),
                timeout=timeout,
                check_same_thread=check_same_thread,
            )
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute(f'PRAGMA busy_timeout={busy_timeout_ms}')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA cache_size=-8000')  # 8 MB cache
            conn.execute('PRAGMA temp_store=MEMORY')
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" in str(e) and i < max_retries - 1:
                time.sleep(delay * (2 ** i))
                continue
            raise
    raise last_err  # type: ignore


def execute_with_retry(
    conn: sqlite3.Connection,
    query: str,
    params: Sequence[object] = (),
    max_retries: int = 3,
    delay: float = 0.2,
) -> sqlite3.Cursor:
    """Execute a statement with retry for transient SQLite lock errors."""
    last_err: sqlite3.OperationalError | None = None
    for attempt in range(max_retries):
        try:
            return conn.execute(query, params)
        except sqlite3.OperationalError as exc:
            last_err = exc
            if "locked" not in str(exc).lower() or attempt >= max_retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))
    raise last_err  # type: ignore[misc]


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
