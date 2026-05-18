"""Тесты для consumption/db.py — подключение, PRAGMA, настройки."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from consumption import db as db_module
from consumption.db import connect, execute_with_retry, get_setting, set_setting


def _test_db_path(tmp_path):
    return tmp_path / "test_consumption.db"


def test_connect_returns_connection(tmp_path):
    """Проверяет, что connect() возвращает рабочее подключение."""
    conn = connect(_test_db_path(tmp_path))
    try:
        row = conn.execute("SELECT 1 AS val").fetchone()
        assert row is not None
        assert row["val"] == 1
    finally:
        conn.close()


def test_connect_enables_foreign_keys(tmp_path):
    """Проверяет, что PRAGMA foreign_keys=ON включён."""
    conn = connect(_test_db_path(tmp_path))
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1, f"expected 1, got {row[0]}"
    finally:
        conn.close()


def test_connect_wal_mode(tmp_path):
    """Проверяет, что journal_mode=WAL."""
    conn = connect(_test_db_path(tmp_path))
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].upper() == "WAL", f"expected WAL, got {row[0]}"
    finally:
        conn.close()


def test_connect_busy_timeout(tmp_path):
    """Проверяет, что busy_timeout выставлен через общий helper."""
    conn = connect(_test_db_path(tmp_path), busy_timeout_ms=12345)
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        assert row[0] == 12345, f"expected 12345, got {row[0]}"
    finally:
        conn.close()


def test_connect_row_factory(tmp_path):
    """Проверяет, что row_factory = sqlite3.Row (доступ по ключу)."""
    conn = connect(_test_db_path(tmp_path))
    try:
        row = conn.execute("SELECT 42 AS answer").fetchone()
        assert row["answer"] == 42
    finally:
        conn.close()


def test_get_setting_creates_table(tmp_path):
    """Проверяет, что get_setting создаёт таблицу settings, если её нет."""
    conn = connect(_test_db_path(tmp_path))
    try:
        # Убедимся, что таблица создаётся
        val = get_setting(conn, "test_key", default="nope")
        assert val == "nope"

        # Проверяем, что таблица существует
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'").fetchall()]
        assert "settings" in tables
    finally:
        conn.close()


def test_set_and_get_setting(tmp_path):
    """Проверяет запись и чтение настроек."""
    conn = connect(_test_db_path(tmp_path))
    try:
        set_setting(conn, "test_key", "hello")
        assert get_setting(conn, "test_key") == "hello"
    finally:
        conn.close()


def test_connect_retries_transient_locked_errors(monkeypatch, tmp_path):
    """Проверяет retry при временном database is locked на этапе подключения."""
    real_connect = sqlite3.connect
    calls = {"count": 0}

    def flaky_connect(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(db_module.sqlite3, "connect", flaky_connect)
    monkeypatch.setattr(db_module.time, "sleep", lambda _: None)

    conn = connect(_test_db_path(tmp_path), max_retries=2, delay=0)
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()
    assert calls["count"] == 2


def test_execute_with_retry_retries_locked_statement(monkeypatch):
    """Проверяет retry wrapper для write/read операций с transient lock."""
    class FakeConn:
        def __init__(self):
            self.calls = 0

        def execute(self, query, params=()):
            self.calls += 1
            if self.calls == 1:
                raise sqlite3.OperationalError("database is locked")
            return "cursor"

    fake = FakeConn()
    monkeypatch.setattr(db_module.time, "sleep", lambda _: None)

    assert execute_with_retry(fake, "UPDATE x SET y=?", (1,), max_retries=2, delay=0) == "cursor"
    assert fake.calls == 2
