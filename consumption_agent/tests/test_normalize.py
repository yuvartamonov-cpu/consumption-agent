"""Тесты для matcher.py — нормализация и фильтрация мусора."""
import sys
sys.path.insert(0, '../')

from matcher import normalize, is_garbage


def test_normalize_lowercase():
    assert normalize("Hello World") == "hello world"


def test_normalize_punct():
    assert normalize("Тест, (товар)!") == "тест товар"


def test_normalize_empty():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_normalize_whitespace():
    assert normalize("  много   пробелов  ") == "много пробелов"


def test_is_garbage_empty():
    assert is_garbage("") is True
    assert is_garbage(None) is True


def test_is_garbage_short():
    assert is_garbage("ab") is True


def test_is_garbage_url():
    assert is_garbage("https://example.com") is True


def test_is_garbage_valid():
    assert is_garbage("Паста томатная 500г") is False
    assert is_garbage("Молоко 3.2% 1л") is False


def test_is_garbage_only_digits():
    assert is_garbage("123456") is True


def test_is_garbage_no_alpha():
    assert is_garbage("!!! /// ...") is True


import pytest
if __name__ == '__main__':
    pytest.main([__file__, '-v'])
