"""Тесты для telegram_bot.py — ключевые функции без зависимостей от Telegram API."""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Ставим фиктивные env, чтобы telegram_bot мог импортироваться
os.environ['CONSUMPTION_BOT_TOKEN'] = 'test:token'
os.environ['OWNER_CHAT_ID'] = '12345'

import telegram_bot as tb


# ────────────────────────────────────────────────
# parse_drive_request
# ────────────────────────────────────────────────

def test_parse_drive_request_hours_km():
    h, k = tb.parse_drive_request("3ч 80км")
    assert h == 3.0
    assert k == 80.0


def test_parse_drive_request_hours_only():
    h, k = tb.parse_drive_request("2 часа")
    assert h == 2.0
    assert k is None


def test_parse_drive_request_km_only():
    h, k = tb.parse_drive_request("60 км")
    assert h is None
    assert k == 60.0


def test_parse_drive_request_empty():
    h, k = tb.parse_drive_request("")
    assert h is None
    assert k is None


def test_parse_drive_request_english():
    h, k = tb.parse_drive_request("5h 100km")
    assert h == 5.0
    assert k == 100.0


def test_parse_drive_request_noise():
    h, k = tb.parse_drive_request("Просто текст без цифр")
    assert h is None
    assert k is None


# ────────────────────────────────────────────────
# calculate_drive_cost
# ────────────────────────────────────────────────

def _tariff(rate_type='flat_km', hourly=0, km_rate=10):
    return {'km_rate': km_rate, 'rate_type': rate_type, 'hourly_rate': hourly}


def test_calculate_drive_cost_flat_km():
    t = _tariff('flat_km', hourly=0, km_rate=15)
    cost = tb.calculate_drive_cost(t, 3, 80)
    assert cost >= 500  # min 500
    assert cost == 1200  # 0 + 80*15 = 1200


def test_calculate_drive_cost_per_hour():
    t = _tariff('per_hour', hourly=500, km_rate=10)
    cost = tb.calculate_drive_cost(t, 3, 80)
    assert cost >= 500
    assert cost == 2300  # 500*3 + 80*10 = 2300


def test_calculate_drive_cost_minimum():
    """Проверяет, что стоимость не опускается ниже 500₽."""
    t = _tariff('flat_km', hourly=0, km_rate=0)
    cost = tb.calculate_drive_cost(t, 1, 0)
    assert cost == 500


# ────────────────────────────────────────────────
# _parse_allowed_chat_ids
# ────────────────────────────────────────────────

def test_parse_allowed_chat_ids_single():
    assert tb._parse_allowed_chat_ids("123") == {123}


def test_parse_allowed_chat_ids_comma():
    assert tb._parse_allowed_chat_ids("123,456,789") == {123, 456, 789}


def test_parse_allowed_chat_ids_semicolon():
    assert tb._parse_allowed_chat_ids("123;456;789") == {123, 456, 789}


def test_parse_allowed_chat_ids_mixed():
    assert tb._parse_allowed_chat_ids("123,456;789") == {123, 456, 789}


def test_parse_allowed_chat_ids_empty():
    assert tb._parse_allowed_chat_ids("") == set()
    assert tb._parse_allowed_chat_ids(None) == set()


def test_parse_allowed_chat_ids_with_spaces():
    assert tb._parse_allowed_chat_ids(" 123 , 456 ") == {123, 456}


# ────────────────────────────────────────────────
# _clean_ocr_lines
# ────────────────────────────────────────────────

def test_clean_ocr_lines_basic():
    text = "Строка1\n   \nСтрока2\n\n\nСтрока3"
    assert tb._clean_ocr_lines(text) == "Строка1\nСтрока2\nСтрока3"


def test_clean_ocr_lines_strips_lines():
    text = "   Привет   \nМир\n  Тест  "
    assert tb._clean_ocr_lines(text) == "Привет\nМир\nТест"


def test_clean_ocr_lines_empty():
    assert tb._clean_ocr_lines("") == ""
    assert tb._clean_ocr_lines("  \n  \n") == ""


# ────────────────────────────────────────────────
# _score_ocr_text
# ────────────────────────────────────────────────

def test_score_ocr_text_receipt():
    text = "Кассовый чек Ozon\nИНН 7204217370\n1 x 1954.60\nИТОГО: 2084.00"
    score = tb._score_ocr_text(text)
    assert score > 0


def test_score_ocr_text_empty():
    assert tb._score_ocr_text("") == 0
    assert tb._score_ocr_text(None) == 0


# ────────────────────────────────────────────────
# _parse_receipt_lines
# ────────────────────────────────────────────────

def test_parse_receipt_lines_ozon_format():
    text = "Конструктор Гарри Поттер\n1 x 1954.60\nДоставка\n1 x 130.00"
    items = tb._parse_receipt_lines(text)
    assert len(items) > 0
    # Должен найти хотя бы конструктор
    names = [it['name'] for it in items]
    assert any('Гарри' in n for n in names), f"Не найден 'Конструктор Гарри Поттер' в {names}"


def test_parse_receipt_lines_standard_format():
    text = "Молоко 3.2% 1л 85.00 ₽\nХлеб белый 45.00 ₽"
    items = tb._parse_receipt_lines(text)
    assert len(items) > 0


def test_parse_receipt_lines_empty():
    items = tb._parse_receipt_lines("")
    assert items == []


def test_parse_receipt_lines_garbage():
    text = "ae eee\nсиниии\nNOTIFICATIONS\n12345"
    items = tb._parse_receipt_lines(text)
    assert items == []  # весь мусор должен быть отфильтрован


# ────────────────────────────────────────────────
# _clean_image_url
# ────────────────────────────────────────────────

def test_clean_image_url():
    # URL с экранированными слешами и JSON-хвостом — ожидаемая проблема на Яндексе
    url = tb._clean_image_url(r"https://avatars.mds.yandex.net/get-eda/12345/abcde\/orig&#x2F;&amp;quot;")
    assert url.startswith("https://avatars.mds.yandex.net")
    assert "&quot;" not in url
    assert "&#x2F;" not in url


def test_clean_image_url_no_params():
    assert tb._clean_image_url("http://example.com/img.jpg") == "http://example.com/img.jpg"


# ────────────────────────────────────────────────
# classify_image_type
# ────────────────────────────────────────────────

def test_classify_image_type_receipt():
    text = "Кассовый чек\nИтого: 1234.56 ₽\nФН: 1234"
    assert tb.classify_image_type(text) == "receipt"


def test_classify_image_type_tag():
    text = "SIZE: M\nMADE IN ITALY\nARTICLE: ABC123\nCOMPOSITION: COTTON"
    assert tb.classify_image_type(text) == "tag"


def test_classify_image_type_unknown():
    assert tb.classify_image_type("") == "unknown"
    assert tb.classify_image_type("какой-то случайный текст") == "unknown"


# ────────────────────────────────────────────────
# get_db
# ────────────────────────────────────────────────

def test_get_db_returns_connection():
    conn = tb.get_db()
    try:
        row = conn.execute("SELECT 1 AS val").fetchone()
        assert row is not None
        assert row["val"] == 1
    finally:
        conn.close()


# ────────────────────────────────────────────────
# _write_text_file
# ────────────────────────────────────────────────

def test_write_text_file(tmp_path):
    path = str(tmp_path / "test.txt")
    tb._write_text_file(path, "hello")
    with open(path) as f:
        assert f.read() == "hello"
