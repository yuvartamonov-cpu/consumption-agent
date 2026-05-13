"""Тесты для init_db.py — создание схемы, индексы, миграция."""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import sqlite3
from init_db import create_new_schema, check_is_initialized, seed_categories, ensure_default_profile, ensure_indexes


def _in_memory_db():
    """Создаёт in-memory БД и применяет схему. Возвращает conn."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=OFF")    # быстрее для тестов
    conn.execute("PRAGMA synchronous=OFF")
    return conn


def test_create_new_schema_creates_tables():
    """Проверяет, что все таблицы созданы."""
    conn = _in_memory_db()
    try:
        create_new_schema(conn)
        expected_tables = {
            "profiles", "categories", "purchases", "items",
            "recognized_items_log", "cheques_log", "alerts", "subscriptions",
        }
        actual = set(
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
        for tbl in expected_tables:
            assert tbl in actual, f"Таблица {tbl} не создана"
    finally:
        conn.close()


def test_create_new_schema_creates_indexes():
    """Проверяет, что индексы созданы (Codex п.4)."""
    conn = _in_memory_db()
    try:
        create_new_schema(conn)
        ensure_indexes(conn)
        indexes = set(
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        # Индексы, которые есть в init_db.create_new_schema()
        expected_indexes = {
            "idx_items_deleted_at",
            "idx_items_category_id",
            "idx_items_purchase_id",
            "idx_purchases_deleted_at",
            "idx_alerts_status",
            "idx_recognized_items_log_match_source",
        }
        for idx in expected_indexes:
            assert idx in indexes, f"Индекс {idx} не создан"
    finally:
        conn.close()


def test_check_is_initialized_returns_true():
    """Проверяет, что после создания схемы check_is_initialized() возвращает True."""
    conn = _in_memory_db()
    try:
        create_new_schema(conn)
        assert check_is_initialized(conn) is True
    finally:
        conn.close()


def test_check_is_initialized_returns_false():
    """Проверяет, что без схемы check_is_initialized() возвращает False."""
    conn = _in_memory_db()
    try:
        # Создаём только одну таблицу, не profiles
        conn.execute("CREATE TABLE test (id INTEGER)")
        assert check_is_initialized(conn) is False
    finally:
        conn.close()


def test_seed_categories():
    """Проверяет, что категории добавляются."""
    conn = _in_memory_db()
    try:
        create_new_schema(conn)
        seed_categories(conn)
        count = conn.execute("SELECT COUNT(*) AS cnt FROM categories").fetchone()["cnt"]
        assert count > 0, "Категории не добавлены"
        # Проверяем корневые категории
        root = conn.execute(
            "SELECT COUNT(*) AS cnt FROM categories WHERE parent_id IS NULL"
        ).fetchone()["cnt"]
        assert root > 0, "Корневые категории не добавлены"
    finally:
        conn.close()


def test_foreign_keys_enforced():
    """Проверяет, что foreign_keys работают (нельзя вставить мусорный category_id)."""
    conn = _in_memory_db()
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        create_new_schema(conn)
        # Пытаемся вставить товар с несуществующей категорией
        try:
            conn.execute(
                "INSERT INTO items (name, category_id) VALUES (?, ?)",
                ("Тест", "cat_nonexistent"),
            )
            conn.commit()
            assert False, "Должна была быть ошибка foreign key"
        except sqlite3.IntegrityError:
            pass  # Ожидаемо
    finally:
        conn.close()
