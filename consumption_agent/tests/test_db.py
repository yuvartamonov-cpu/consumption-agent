"""Тесты для consumption.db."""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from consumption.db import DB_PATH, connect, get_setting, set_setting


def test_db_path_exists():
    """Путь до БД определён."""
    assert DB_PATH.endswith("consumption.db")


def test_connect_returns_connection():
    """connect() возвращает рабочее подключение."""
    conn = connect()
    assert conn is not None
    # Проверим row_factory
    row = conn.execute("SELECT 1 as x").fetchone()
    assert row["x"] == 1
    conn.close()


def test_settings_roundtrip():
    """set_setting / get_setting возвращают то, что записали."""
    conn = connect()
    set_setting(conn, "test_key", "test_value")
    conn.commit()
    val = get_setting(conn, "test_key")
    assert val == "test_value"
    # Удаляем тестовый ключ
    conn.execute("DELETE FROM settings WHERE key='test_key'")
    conn.commit()
    conn.close()


def test_settings_default():
    conn = connect()
    val = get_setting(conn, "nonexistent_key", "default")
    assert val == "default"
    conn.close()


def test_settings_update():
    conn = connect()
    set_setting(conn, "test_update", "v1")
    conn.commit()
    set_setting(conn, "test_update", "v2")
    conn.commit()
    assert get_setting(conn, "test_update") == "v2"
    conn.execute("DELETE FROM settings WHERE key='test_update'")
    conn.commit()
    conn.close()
