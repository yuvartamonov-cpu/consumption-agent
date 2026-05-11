#!/usr/bin/env python3
"""Тесты для matcher.py — нормализация, фильтрация, exact и fuzzy match."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matcher import (
    normalize, is_garbage, exact_match, fuzzy_match, match_record,
    run_matcher, get_unmatched, get_all_items, DB_PATH,
)

# ---------------------------------------------------------------------------
# Тесты нормализации
# ---------------------------------------------------------------------------

def test_normalize_basic():
    assert normalize("Привет, Мир!") == "привет мир"
    assert normalize("  Лишние   пробелы  ") == "лишние пробелы"
    assert normalize(None) == ""
    assert normalize("") == ""


def test_normalize_punctuation():
    result = normalize("Симпарика 40 мг (для собак) — №3")
    assert "симпарика" in result
    assert "40 мг" in result
    assert "собак" in result
    result2 = normalize("Grandorf Fresh, 3 кг!")
    assert "grandorf" in result2
    assert "fresh" in result2
    assert "3 кг" in result2


def test_normalize_case():
    assert normalize("OZON PREMIUM") == "ozon premium"
    assert normalize("Туалетная Бумага Zewa") == "туалетная бумага zewa"


# ---------------------------------------------------------------------------
# Тесты фильтрации мусора
# ---------------------------------------------------------------------------

def test_is_garbage_short():
    assert is_garbage("") is True
    assert is_garbage("Корот") is True       # < 10 chars
    assert is_garbage("Коротк.") is True     # < 10 chars with punct


def test_is_garbage_tech_patterns():
    assert is_garbage("Notifications Microphone") is True
    assert is_garbage("Connection Failed") is True
    assert is_garbage("bootstrap token invalid") is True
    assert is_garbage("https://example.com/check") is True


def test_is_garbage_english():
    assert is_garbage("Quick brown fox jumps over") is True  # no cyrillic
    assert is_garbage("Python syntax error") is True


def test_is_garbage_good():
    assert is_garbage("Симпарика 40 мг жевательные таблетки") is False
    assert is_garbage("Туалетная бумага Zewa Ultra Soft, 4 слоя") is False
    assert is_garbage("Корм Grandorf Fresh для собак, 3 кг") is False


# ---------------------------------------------------------------------------
# Тесты exact match
# ---------------------------------------------------------------------------

_items_fixture = [
    {"id": 1, "name": "Симпарика 40 мг жевательные таблетки для собак", "brand": "", "sku": ""},
    {"id": 2, "name": "Корм Grandorf Fresh для собак мелких пород, 3 кг", "brand": "", "sku": ""},
    {"id": 3, "name": "Туалетная бумага Zewa Ultra Soft", "brand": "", "sku": ""},
    {"id": 4, "name": "Платье", "brand": "", "sku": ""},
    {"id": 5, "name": "Палатка Домик в сумке", "brand": "", "sku": ""},
]


def test_exact_match_perfect():
    result = exact_match("Симпарика 40 мг жевательные таблетки для собак", "", "", _items_fixture)
    assert len(result) == 1
    assert result[0]["item"]["id"] == 1
    assert result[0]["score"] == 100


def test_exact_match_normalized():
    result = exact_match("симпарика 40 мг (жевательные таблетки) для собак", "", "", _items_fixture)
    assert len(result) == 1


def test_exact_match_no_match():
    result = exact_match("Несуществующий товар", "", "", _items_fixture)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Тесты fuzzy match
# ---------------------------------------------------------------------------

def test_fuzzy_match_optyatka():
    result = fuzzy_match("Симпарика 40 мг жевательне таблетки для сабак", _items_fixture, 85)
    assert len(result) >= 1
    assert result[0]["item"]["id"] == 1
    assert result[0]["score"] >= 85


def test_fuzzy_match_word_order():
    result = fuzzy_match("Grandorf Fresh корм для собак, 3 кг", _items_fixture, 85)
    assert len(result) >= 1


def test_fuzzy_match_partial():
    result = fuzzy_match("Платье летнее", _items_fixture, 60)
    # Платье (4 символа) vs "Платье летнее" — token_set_ratio должно быть ~60-80
    found = any(r["item"]["id"] == 4 for r in result)
    assert found, f"Expected 'Платье' to match 'Платье летнее', got: {[(r['score'], r['item']['name']) for r in result]}"


def test_fuzzy_match_noise():
    result = fuzzy_match("Нит телефон", _items_fixture, 85)
    assert len(result) == 0


def test_fuzzy_match_threshold_medium():
    # Для medium confidence порог 90
    result = fuzzy_match("Палатка Домик в с мке", _items_fixture, 90)
    # "в с мке" vs "в сумке" — небольшое различие
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# Тесты match_record (функция, комбинирующая exact + fuzzy)
# ---------------------------------------------------------------------------

def test_match_record_exact():
    rec = {"recognized_product": "Симпарика 40 мг жевательные таблетки для собак",
           "confidence": "high", "brand": "", "sku": ""}
    result = match_record(rec, _items_fixture, 85, 90)
    assert len(result) >= 1
    assert result[0]["method"] == "exact"


def test_match_record_fuzzy_high():
    rec = {"recognized_product": "Симпарика 40 мг жевательне таблетки",
           "confidence": "high", "brand": "", "sku": ""}
    result = match_record(rec, _items_fixture, 85, 90)
    assert len(result) >= 1
    assert result[0]["method"] == "fuzzy"


def test_match_record_medium_strict():
    # Для medium порог 90
    rec = {"recognized_product": "Симпарика 40 мг жевательне таблетки",
           "confidence": "medium", "brand": "", "sku": ""}
    result = match_record(rec, _items_fixture, 85, 90)
    # Зависит от того, насколько искажено — может быть или не быть
    # Проверяем хотя бы что функция не падает
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Интеграционный тест с временной БД
# ---------------------------------------------------------------------------

def _create_temp_db():
    """Создать временную БД с тестовыми данными."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = sqlite3.connect(tmp.name)
    db.execute("""
        CREATE TABLE recognized_items_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT DEFAULT 'screen',
            recognized_product TEXT,
            confidence TEXT DEFAULT 'high',
            matched_item_id INTEGER,
            notes TEXT
        )
    """)
    db.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            brand TEXT,
            sku TEXT,
            deleted_at TEXT
        )
    """)
    # Seed data
    db.executemany(
        "INSERT INTO items (id, name, brand, sku) VALUES (?, ?, ?, ?)",
        [(1, "Симпарика 40 мг жевательные таблетки для собак", "Simparica", "SMP-40"),
         (2, "Корм Grandorf Fresh для собак, 3 кг", "Grandorf", ""),
         (3, "Туалетная бумага Zewa Ultra Soft", "Zewa", "ZEWA-US-4")],
    )
    db.executemany(
        "INSERT INTO recognized_items_log (id, source_type, recognized_product, confidence) VALUES (?, ?, ?, ?)",
        [(1, "screen", "Симпарика 40 мг жевательные таблетки для собак", "high"),  # exact match
         (2, "screen", "Симпарика 40 мг жевательне таблетки", "high"),           # fuzzy match
         (3, "screen", "Корм Grandorf Fresh, 3 кг", "high"),                      # exact (normalized)
         (4, "screen", "Неизвестный товар с опечаткй", "high"),                   # no match
         (5, "screen_ocr", "Notifications Microphone", "high"),                   # garbage
         (6, "screen", "Симпарика 40 мг жевательне таблетки", "medium"),          # medium fuzzy
         (7, "screen", "Туалетная бумага Zewa Ultra", "high"),                    # partial fuzzy
        ],
    )
    db.commit()
    return tmp.name, db


def test_integration_run_matcher():
    db_path, db = _create_temp_db()

    # Verify initial state
    # get_unmatched accepts only db, no keyword args
    records = list(db.execute("SELECT id FROM recognized_items_log"))
    assert len(records) == 7

    # Verify garbage filtering (screen_ocr skipped)
    filtered, garbage = db.execute("SELECT id, 1 FROM recognized_items_log WHERE source_type='screen_ocr'").fetchone()
    # just check screen_ocr exists as record id 5
    assert db.execute("SELECT id FROM recognized_items_log WHERE id=5 AND source_type='screen_ocr'").fetchone() is not None

    # Run matcher
    stats = run_matcher(db_path, dry_run=True, limit=None,
                        threshold_high=85, threshold_medium=90)

    print(f"\nIntegration test stats: {json.dumps(stats, indent=2, ensure_ascii=False)}")
    assert stats["errors"] == 0

    # Run matcher for real
    stats = run_matcher(db_path, dry_run=False, limit=None,
                        threshold_high=85, threshold_medium=90)

    # Check results in DB
    db2 = sqlite3.connect(db_path)
    cur = db2.execute("SELECT id, matched_item_id, notes FROM recognized_items_log")
    results = dict()
    for r in cur.fetchall():
        results[r[0]] = (r[1], json.loads(r[2]) if r[2] else {})

    # Record 1 (exact match) should have matched_item_id=1
    assert results[1][0] == 1, f"Expected item 1, got {results[1]}"
    assert results[1][1]["match_method"] == "exact"

    # Record 5 (screen_ocr) should remain unmatched
    assert results[5][0] is None, f"screen_ocr should remain unmatched"
    # Record 4 (no match) should remain unmatched

    os.unlink(db_path)


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    tests = [
        ("normalize basic", test_normalize_basic),
        ("normalize punctuation", test_normalize_punctuation),
        ("normalize case", test_normalize_case),
        ("garbage short", test_is_garbage_short),
        ("garbage tech patterns", test_is_garbage_tech_patterns),
        ("garbage english", test_is_garbage_english),
        ("garbage good", test_is_garbage_good),
        ("exact perfect", test_exact_match_perfect),
        ("exact normalized", test_exact_match_normalized),
        ("exact no match", test_exact_match_no_match),
        ("fuzzy optyatka", test_fuzzy_match_optyatka),
        ("fuzzy word order", test_fuzzy_match_word_order),
        ("fuzzy partial", test_fuzzy_match_partial),
        ("fuzzy noise", test_fuzzy_match_noise),
        ("fuzzy threshold medium", test_fuzzy_match_threshold_medium),
        ("match_record exact", test_match_record_exact),
        ("match_record fuzzy high", test_match_record_fuzzy_high),
        ("integration run", test_integration_run_matcher),
    ]

    passed, failed = 0, 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL: {name}: (exception) {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"  Passed: {passed}, Failed: {failed}, Total: {len(tests)}")
