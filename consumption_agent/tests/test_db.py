"""Тесты для consumption/db.py — подключение, PRAGMA, настройки."""

import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from consumption.db import connect, get_setting, set_setting


def test_connect_returns_connection():
    """Проверяет, что connect() возвращает рабочее подключение."""
    conn = connect()
    try:
        row = conn.execute("SELECT 1 AS val").fetchone()
        assert row is not None
        assert row["val"] == 1
    finally:
        conn.close()


def test_connect_enables_foreign_keys():
    """Проверяет, что PRAGMA foreign_keys=ON включён."""
    conn = connect()
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1, f"expected 1, got {row[0]}"
    finally:
        conn.close()


def test_connect_wal_mode():
    """Проверяет, что journal_mode=WAL."""
    conn = connect()
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].upper() == "WAL", f"expected WAL, got {row[0]}"
    finally:
        conn.close()


def test_connect_busy_timeout():
    """Проверяет, что busy_timeout = 5000."""
    conn = connect()
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        # Ожидаем busy_timeout >= 5000 (может быть больше дефолтного, если уже выставлен)
        assert row[0] >= 5000, f"expected >=5000, got {row[0]}"
    finally:
        conn.close()


def test_connect_row_factory():
    """Проверяет, что row_factory = sqlite3.Row (доступ по ключу)."""
    conn = connect()
    try:
        row = conn.execute("SELECT 42 AS answer").fetchone()
        assert row["answer"] == 42
    finally:
        conn.close()


def test_get_setting_creates_table():
    """Проверяет, что get_setting создаёт таблицу settings, если её нет."""
    conn = connect()
    try:
        # Убедимся, что таблица создаётся
        val = get_setting(conn, "test_key", default="nope")
        assert val == "nope"

        # Проверяем, что таблица существует
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'").fetchall()]
        assert "settings" in tables
    finally:
        conn.close()


def test_set_and_get_setting():
    """Проверяет запись и чтение настроек."""
    conn = connect()
    try:
        set_setting(conn, "test_key", "hello")
        assert get_setting(conn, "test_key") == "hello"
    finally:
        conn.close()
