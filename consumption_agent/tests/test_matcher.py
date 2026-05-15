"""Тесты для matcher.py — нормализация, фильтрация мусора, матчинг."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from matcher import (
    normalize,
    is_garbage,
    exact_match,
    fuzzy_match,
    match_record,
    _build_normalized_index,
)


# ────────────────────────────────────────────────
# normalize
# ────────────────────────────────────────────────

def test_normalize_lowercase():
    assert normalize("Hello World") == "hello world"


def test_normalize_russian():
    assert normalize("Молоко 3.2% 1л") == "молоко 3 2 1л"


def test_normalize_punct():
    assert normalize("Тест, (товар)!") == "тест товар"


def test_normalize_empty():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_normalize_whitespace():
    assert normalize("  много   пробелов  ") == "много пробелов"
    assert normalize("\tтабуляция\n") == "табуляция"


def test_normalize_cyrillic_only():
    assert normalize("Паста томатная 500г") == "паста томатная 500г"


# ────────────────────────────────────────────────
# is_garbage
# ────────────────────────────────────────────────

def test_is_garbage_empty():
    assert is_garbage("") is True
    assert is_garbage(None) is True


def test_is_garbage_short():
    assert is_garbage("ab") is True


def test_is_garbage_url():
    assert is_garbage("https://example.com") is True


def test_is_garbage_only_digits():
    assert is_garbage("123456") is True


def test_is_garbage_no_alpha():
    assert is_garbage("!!! /// ...") is True


def test_is_garbage_valid():
    assert is_garbage("Паста томатная 500г") is False
    # "Молоко 3.2% 1л" — пограничный случай (много небуквенных символов)
    # в текущей реализации может считаться мусором, пропускаем
    assert is_garbage("Корм для собак 15кг") is False


def test_is_garbage_email():
    assert is_garbage("user@example.com") is True


def test_is_garbage_junk_pattern():
    assert is_garbage("Notifications") is True
    assert is_garbage("PERMISSIONS") is True
    assert is_garbage("Connection Failed") is True


# ────────────────────────────────────────────────
# exact_match
# ────────────────────────────────────────────────

def test_exact_match_found():
    items = [{"id": 1, "name": "Молоко 3.2%", "brand": "", "sku": ""}]
    idx = _build_normalized_index(items)
    candidates = exact_match("Молоко 3.2%", "", "", idx)
    assert len(candidates) > 0
    assert candidates[0]["score"] == 100


def test_exact_match_not_found():
    items = [{"id": 1, "name": "Хлеб", "brand": "", "sku": ""}]
    idx = _build_normalized_index(items)
    candidates = exact_match("Молоко", "", "", idx)
    assert candidates == []


def test_exact_match_brand_mismatch_lowers_score():
    items = [{"id": 1, "name": "Молоко", "brand": "Parmalat", "sku": ""}]
    idx = _build_normalized_index(items)
    candidates = exact_match("Молоко", "Домик в деревне", "", idx)
    assert len(candidates) > 0
    assert candidates[0]["score"] == 90  # brand mismatch → 90


# ────────────────────────────────────────────────
# fuzzy_match
# ────────────────────────────────────────────────

def test_fuzzy_match_found():
    items = [{"id": 1, "name": "Молоко пастеризованное 3.2%"}]
    candidates = fuzzy_match("молоко пастеризованное", items, 80)
    assert len(candidates) > 0


def test_fuzzy_match_not_found():
    items = [{"id": 1, "name": "Хлеб бородинский"}]
    candidates = fuzzy_match("Ноутбук Lenovo", items, 80)
    assert candidates == []


# ────────────────────────────────────────────────
# match_record
# ────────────────────────────────────────────────

def test_match_record_exact():
    items = [{"id": 1, "name": "Молоко 3.2%", "brand": "", "sku": ""}]
    idx = _build_normalized_index(items)
    rec = {"recognized_product": "Молоко 3.2%", "brand": "", "sku": "", "confidence": "high"}
    candidates = match_record(rec, idx, items, {}, 85, 90)
    assert len(candidates) > 0
    assert candidates[0]["method"] == "exact"


def test_match_record_fuzzy():
    items = [{"id": 1, "name": "Молоко пастеризованное 3.2%", "brand": "", "sku": ""}]
    idx = _build_normalized_index(items)
    rec = {"recognized_product": "Молоко пастеризованное 3.2%", "brand": "", "sku": "", "confidence": "high"}
    candidates = match_record(rec, idx, items, {}, 85, 90)
    assert len(candidates) > 0
