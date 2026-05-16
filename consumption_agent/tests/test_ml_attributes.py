"""Tests for ml_attributes — Vision attribute extraction + storage."""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_attributes as ma


# ────────────────────────────────────────────────
# validate_attributes
# ────────────────────────────────────────────────

def test_validate_empty_input_returns_defaults():
    out = ma.validate_attributes({})
    assert out['category'] is None
    assert out['secondary_colors'] == []
    assert out['style'] == []
    assert out['confidence'] == 0.0


def test_validate_non_dict_returns_defaults():
    assert ma.validate_attributes(None)['category'] is None
    assert ma.validate_attributes("string")['confidence'] == 0.0
    assert ma.validate_attributes([])['style'] == []


def test_validate_valid_category():
    out = ma.validate_attributes({'category': 'одежда'})
    assert out['category'] == 'одежда'


def test_validate_invalid_category_dropped():
    out = ma.validate_attributes({'category': 'space-suit'})
    assert out['category'] is None


def test_validate_case_normalisation():
    out = ma.validate_attributes({'category': 'ОДЕЖДА', 'fit': 'OVERSIZE'})
    assert out['category'] == 'одежда'
    assert out['fit'] == 'oversize'


def test_validate_strips_null_strings():
    out = ma.validate_attributes({'brand': 'null', 'model': 'None', 'article': ''})
    assert out['brand'] is None
    assert out['model'] is None
    assert out['article'] is None


def test_validate_clamps_confidence():
    assert ma.validate_attributes({'confidence': 1.5})['confidence'] == 1.0
    assert ma.validate_attributes({'confidence': -0.2})['confidence'] == 0.0
    assert ma.validate_attributes({'confidence': 0.7})['confidence'] == 0.7


def test_validate_price_must_be_positive():
    assert ma.validate_attributes({'estimated_price_rub': 0})['estimated_price_rub'] is None
    assert ma.validate_attributes({'estimated_price_rub': -100})['estimated_price_rub'] is None
    assert ma.validate_attributes({'estimated_price_rub': 8990})['estimated_price_rub'] == 8990
    assert ma.validate_attributes({'estimated_price_rub': 'cheap'})['estimated_price_rub'] is None


def test_validate_truncates_style_list():
    out = ma.validate_attributes({'style': ['a', 'b', 'c', 'd', 'e', 'f', 'g']})
    assert len(out['style']) == 5


def test_validate_drops_non_string_in_lists():
    out = ma.validate_attributes({'secondary_colors': ['серый', 123, None, 'белый']})
    assert out['secondary_colors'] == ['серый', 'белый']


def test_validate_full_payload():
    raw = {
        'category': 'одежда',
        'subcategory': 'пальто',
        'brand': 'Massimo Dutti',
        'model': None,
        'article': 'A-12345',
        'primary_color': 'серый',
        'secondary_colors': ['графит'],
        'material': 'шерсть',
        'fit': 'oversize',
        'length': 'midi',
        'season': 'winter',
        'style': ['minimalism', 'casual'],
        'gender': 'unisex',
        'estimated_price_rub': 12990,
        'confidence': 0.85,
    }
    out = ma.validate_attributes(raw)
    assert out['brand'] == 'Massimo Dutti'
    assert out['fit'] == 'oversize'
    assert out['length'] == 'midi'
    assert out['estimated_price_rub'] == 12990
    assert out['confidence'] == 0.85


# ────────────────────────────────────────────────
# _parse_vision_json
# ────────────────────────────────────────────────

def test_parse_plain_json():
    assert ma._parse_vision_json('{"a": 1}') == {'a': 1}


def test_parse_markdown_wrapped():
    assert ma._parse_vision_json('```json\n{"a": 2}\n```') == {'a': 2}


def test_parse_with_preamble():
    text = 'Вот результат:\n{"category": "одежда"}\nконец'
    assert ma._parse_vision_json(text) == {'category': 'одежда'}


def test_parse_empty():
    assert ma._parse_vision_json('') == {}
    assert ma._parse_vision_json('not json at all') == {}


# ────────────────────────────────────────────────
# extract_attributes (injected mock)
# ────────────────────────────────────────────────

def test_extract_with_mock_caller():
    def fake_caller(path, prompt, model=None, max_tokens=600, timeout=30.0):
        return ('{"category": "одежда", "subcategory": "пальто", "confidence": 0.9}', False)
    out = ma.extract_attributes('/no/such/file.jpg', vision_caller=fake_caller)
    assert out['category'] == 'одежда'
    assert out['subcategory'] == 'пальто'
    assert out['confidence'] == 0.9


def test_extract_timeout_returns_defaults():
    def timeout_caller(path, prompt, model=None, max_tokens=600, timeout=30.0):
        return ('', True)
    out = ma.extract_attributes('/no/such/file.jpg', vision_caller=timeout_caller)
    assert out['category'] is None
    assert out['confidence'] == 0.0


def test_extract_caller_exception_returns_defaults():
    def broken_caller(path, prompt, model=None, max_tokens=600, timeout=30.0):
        raise RuntimeError("network")
    out = ma.extract_attributes('/x.jpg', vision_caller=broken_caller)
    assert out['category'] is None


def test_extract_caption_passed_in_prompt():
    captured = {}
    def capture_caller(path, prompt, model=None, max_tokens=600, timeout=30.0):
        captured['prompt'] = prompt
        return ('{}', False)
    ma.extract_attributes('/x.jpg', caption='нравится #пальто серое', vision_caller=capture_caller)
    assert 'нравится #пальто серое' in captured['prompt']


# ────────────────────────────────────────────────
# Storage round-trip
# ────────────────────────────────────────────────

def test_ensure_attributes_column_idempotent():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE memory_lane_items (id INTEGER PRIMARY KEY, caption TEXT)")
    ma.ensure_attributes_column(conn)
    ma.ensure_attributes_column(conn)  # idempotent
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_lane_items)").fetchall()}
    assert 'attributes_json' in cols
    conn.close()


def test_save_and_load_attributes_roundtrip():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE memory_lane_items (id INTEGER PRIMARY KEY, caption TEXT)")
    conn.execute("INSERT INTO memory_lane_items (id, caption) VALUES (1, 'test')")

    attrs = ma.validate_attributes({
        'category': 'обувь',
        'brand': 'Nike',
        'model': 'Air Force 1',
        'primary_color': 'белый',
        'confidence': 0.92,
    })
    ma.save_attributes(conn, 1, attrs)

    loaded = ma.load_attributes(conn, 1)
    assert loaded is not None
    assert loaded['brand'] == 'Nike'
    assert loaded['model'] == 'Air Force 1'
    assert loaded['confidence'] == 0.92
    conn.close()


def test_load_attributes_missing_row():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE memory_lane_items (id INTEGER PRIMARY KEY)")
    ma.ensure_attributes_column(conn)
    assert ma.load_attributes(conn, 999) is None
    conn.close()


def test_load_attributes_corrupt_json_returns_none():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE memory_lane_items (id INTEGER PRIMARY KEY, caption TEXT)")
    conn.execute("INSERT INTO memory_lane_items (id, caption) VALUES (1, 'x')")
    ma.ensure_attributes_column(conn)
    conn.execute("UPDATE memory_lane_items SET attributes_json = '{not json' WHERE id = 1")
    assert ma.load_attributes(conn, 1) is None
    conn.close()
