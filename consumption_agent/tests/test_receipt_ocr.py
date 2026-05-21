"""Тесты для scripts/receipt_ocr.py — парсинг, detect_shop, parse_total, parse_date."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


# Тестируем напрямую import из receipt_ocr
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from scripts.receipt_ocr import (
    _detect_shop,
    _is_junk_line,
)


# ────────────────────────────────────────────────
# _detect_shop
# ────────────────────────────────────────────────

def test_detect_shop_ozon():
    assert _detect_shop("Заказ на OZON") == "Ozon"
    assert _detect_shop("Оплата Ozon") == "Ozon"
    assert _detect_shop("ozon.ru") == "Ozon"


def test_detect_shop_yandex():
    assert _detect_shop("Яндекс Еда") == "Яндекс"
    assert _detect_shop("Yandex Plus") == "Яндекс"


def test_detect_shop_wildberries():
    assert _detect_shop("Wildberries") == "Wildberries"
    assert _detect_shop("WB заказ") == "Wildberries"


def test_detect_shop_samokat():
    assert _detect_shop("Самокат доставка") == "Самокат"
    assert _detect_shop("samokat.ru") == "Самокат"


def test_detect_shop_magnit():
    assert _detect_shop("Магнит у дома") == "Магнит"


def test_detect_shop_pyaterochka():
    assert _detect_shop("Пятёрочка") == "Пятёрочка"
    assert _detect_shop("Пятерочка 123") == "Пятёрочка"


def test_detect_shop_unknown():
    assert _detect_shop("Какой-то непонятный чек") == ""


def test_detect_shop_guest_bill_header_candidate():
    text = """
    ***********************
    Ленинградка ----
    ***********************
    ГОСТЕВОЙ СЧЕТ
    Зал: Зал 1 этаж
    Открыт: 20.05.2026 18:40
    Наименование
    """
    assert _detect_shop(text) == "Ленинградка"


# ────────────────────────────────────────────────
# _is_junk_line
# ────────────────────────────────────────────────

def test_is_junk_line_empty():
    assert _is_junk_line("") is True
    assert _is_junk_line("  ") is True


def test_is_junk_line_short():
    assert _is_junk_line("аб") is True


def test_is_junk_line_digits_only():
    assert _is_junk_line("12345") is True
    assert _is_junk_line("1 234,56") is True


def test_is_junk_line_nds():
    assert _is_junk_line("вт.ч НДС 22/122 23.44") is True
    assert _is_junk_line("НДС 20% 593.08") is True


def test_is_junk_line_inn():
    assert _is_junk_line("ИНН 7204217370") is True
    assert _is_junk_line("ИНН продавца: 142101082058") is True


def test_is_junk_line_ip():
    assert _is_junk_line("Кальянова Валентина Борисовна, ИП") is True


def test_is_junk_line_valid_item():
    assert _is_junk_line("Конструктор Гарри Поттер 1176 деталей") is False
