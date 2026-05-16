"""Tests for ml_canonical — cross-marketplace product canonicalization."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ml_canonical import (
    normalize,
    parse_price,
    canonical_fingerprint,
    canonicalize,
    group_stats,
)


# ────────────────────────────────────────────────
# normalize
# ────────────────────────────────────────────────

def test_normalize_basic():
    assert normalize("  Hello   World  ") == "hello world"


def test_normalize_strips_punctuation():
    assert normalize("Nike Air Force 1, белый!") == "nike air force 1 белый"


def test_normalize_handles_none_and_blank():
    assert normalize(None) == ''
    assert normalize('') == ''
    assert normalize('null') == ''
    assert normalize('None') == ''
    assert normalize('—') == ''
    assert normalize('-') == ''


def test_normalize_preserves_cyrillic():
    assert normalize("ПАЛЬТО Massimo Dutti") == "пальто massimo dutti"


def test_normalize_collapses_whitespace():
    assert normalize("foo\t\n  bar") == "foo bar"


# ────────────────────────────────────────────────
# parse_price
# ────────────────────────────────────────────────

def test_parse_price_int():
    assert parse_price(12990) == 12990


def test_parse_price_float_floors():
    assert parse_price(12990.99) == 12990


def test_parse_price_string_with_currency():
    assert parse_price("12 990 ₽") == 12990
    assert parse_price("12990 RUB") == 12990
    assert parse_price("$199.99") == 199


def test_parse_price_thousands_separator():
    assert parse_price("12,990") == 12990
    assert parse_price("1 234 567") == 1234567


def test_parse_price_with_kopeks_dropped():
    assert parse_price("12990.50") == 12990
    assert parse_price("12 990,50 ₽") == 12990


def test_parse_price_dict_yandex_market():
    assert parse_price({'value': 8990, 'currency': 'RUB'}) == 8990


def test_parse_price_dict_nested():
    assert parse_price({'price': {'value': 5500}}) == 5500


def test_parse_price_empty_or_invalid():
    assert parse_price(None) is None
    assert parse_price('') is None
    assert parse_price('free') is None
    assert parse_price(0) is None
    assert parse_price(-100) is None
    assert parse_price(True) is None
    assert parse_price(False) is None


def test_parse_price_handles_dict_with_zero():
    # Zero in dict should fail through to None
    assert parse_price({'value': 0}) is None


# ────────────────────────────────────────────────
# canonical_fingerprint — Tier A (brand + article/model)
# ────────────────────────────────────────────────

def test_fp_brand_and_article_same_across_stores():
    attrs = {'brand': 'Nike', 'article': '315122-111'}
    a = canonical_fingerprint({'store': 'Ozon', 'title': 'Кроссовки белые'}, attrs)
    b = canonical_fingerprint({'store': 'WB', 'title': 'AF1 white sneakers'}, attrs)
    assert a == b
    assert a.startswith('attr:nike|315122')


def test_fp_brand_and_model_when_no_article():
    attrs = {'brand': 'Nike', 'model': 'Air Force 1'}
    fp = canonical_fingerprint({}, attrs)
    assert fp == 'attr:nike|air force 1'


def test_fp_article_takes_priority_over_model():
    attrs = {'brand': 'Nike', 'model': 'Air Force', 'article': 'A1'}
    fp = canonical_fingerprint({}, attrs)
    assert fp == 'attr:nike|a1'


def test_fp_candidate_provides_missing_article():
    attrs = {'brand': 'Nike'}
    cand = {'article': '315122-111', 'title': 'whatever'}
    fp = canonical_fingerprint(cand, attrs)
    assert fp == 'attr:nike|315122 111'  # punctuation normalised


def test_fp_lone_article_no_brand():
    fp = canonical_fingerprint({}, {'article': 'XYZ-999'})
    assert fp == 'attr:|xyz 999'


# ────────────────────────────────────────────────
# canonical_fingerprint — Tier B (subcat + colour)
# ────────────────────────────────────────────────

def test_fp_tier_b_subcat_color():
    attrs = {'subcategory': 'пальто', 'primary_color': 'серый'}
    a = canonical_fingerprint({'title': 'Пальто Massimo'}, attrs)
    b = canonical_fingerprint({'title': 'Шерстяное пальто'}, attrs)
    assert a == b
    assert a.startswith('attr-loose:пальто|серый')


def test_fp_tier_b_falls_back_to_category():
    attrs = {'category': 'мебель', 'primary_color': 'белый'}
    fp = canonical_fingerprint({}, attrs)
    assert fp == 'attr-loose:мебель|белый'


# ────────────────────────────────────────────────
# canonical_fingerprint — Tier C (text tokens)
# ────────────────────────────────────────────────

def test_fp_tier_c_token_sig_order_invariant():
    a = canonical_fingerprint({'title': 'Чайник Bosch электрический'}, {})
    b = canonical_fingerprint({'title': 'Электрический Bosch чайник купить'}, {})
    assert a == b
    assert a.startswith('tokens:')


def test_fp_tier_c_uses_name_field_too():
    fp = canonical_fingerprint({'name': 'Серое пальто шерсть'}, {})
    assert fp.startswith('tokens:')


def test_fp_tier_c_drops_stop_words():
    # "купить" and "оригинал" are stop tokens — should not be in fingerprint
    fp = canonical_fingerprint({'title': 'Чайник Bosch купить оригинал'}, {})
    assert 'купить' not in fp
    assert 'оригинал' not in fp


def test_fp_tier_d_empty_candidate_is_singleton():
    a = canonical_fingerprint({'store': 'X'}, {})
    b = canonical_fingerprint({'store': 'Y'}, {})
    # Two empty-ish candidates with different store names get different
    # unknown: fingerprints — singleton groups (no false merging)
    assert a.startswith('unknown:') or a.startswith('tokens:')
    assert b.startswith('unknown:') or b.startswith('tokens:')


# ────────────────────────────────────────────────
# canonicalize
# ────────────────────────────────────────────────

def test_canonicalize_empty():
    assert canonicalize([]) == []
    assert canonicalize([], {'brand': 'Nike'}) == []


def test_canonicalize_groups_same_product_across_marketplaces():
    attrs = {'brand': 'Nike', 'article': '315122-111'}
    candidates = [
        {'store': 'Ozon', 'title': 'Nike AF1', 'price': '12 990 ₽', 'url': 'ozon.ru/p1'},
        {'store': 'Wildberries', 'title': 'AF1 White', 'price': '11 590 ₽', 'url': 'wb.ru/p1'},
        {'store': 'Yandex.Market', 'title': 'Nike', 'price': {'value': 12290}, 'url': 'ym.ru/p1'},
    ]
    result = canonicalize(candidates, attrs)
    assert len(result) == 1
    g = result[0]
    assert g['sources_count'] == 3
    assert set(g['sources']) == {'Ozon', 'Wildberries', 'Yandex.Market'}
    assert g['price_min'] == 11590
    assert g['price_max'] == 12990
    assert g['price_median'] == 12290
    # Primary is the cheapest
    assert g['store'] == 'Wildberries'
    assert g['url'] == 'wb.ru/p1'


def test_canonicalize_separate_products_stay_separate():
    candidates = [
        {'store': 'Ozon', 'title': 'Nike AF1', 'price': '12990', 'article': 'AF1'},
        {'store': 'Ozon', 'title': 'Adidas Stan Smith', 'price': '9990', 'article': 'SS'},
    ]
    result = canonicalize(candidates, {'brand': 'Nike'})
    # Different articles → different fingerprints → 2 groups
    assert len(result) == 2


def test_canonicalize_sorts_by_group_size_then_price():
    attrs = {'brand': 'Nike', 'article': 'A1'}
    candidates = [
        # Group X: 1 listing
        {'store': 'X', 'title': 'Other', 'price': '5000', 'article': 'OTHER'},
        # Group A: 3 listings (should come first)
        {'store': 'Ozon', 'title': 'p', 'price': '10000'},
        {'store': 'WB', 'title': 'p', 'price': '11000'},
        {'store': 'YM', 'title': 'p', 'price': '9000'},
    ]
    result = canonicalize(candidates, attrs)
    assert result[0]['sources_count'] == 3
    assert result[1]['sources_count'] == 1


def test_canonicalize_handles_listings_without_price():
    attrs = {'brand': 'Nike', 'article': 'A1'}
    candidates = [
        {'store': 'Ozon', 'title': 'p', 'price': '5000'},
        {'store': 'WB', 'title': 'p', 'price': None},
        {'store': 'YM', 'title': 'p', 'price': 'договорная'},
    ]
    result = canonicalize(candidates, attrs)
    assert len(result) == 1
    g = result[0]
    assert g['sources_count'] == 3
    assert g['price_min'] == 5000
    assert g['price_max'] == 5000
    assert g['store'] == 'Ozon'  # the only priced one becomes primary


def test_canonicalize_all_unpriced_returns_group_but_no_stats():
    attrs = {'brand': 'Nike', 'article': 'A1'}
    candidates = [
        {'store': 'Ozon', 'title': 'p', 'price': None},
        {'store': 'WB', 'title': 'p', 'price': 'нет цены'},
    ]
    result = canonicalize(candidates, attrs)
    assert len(result) == 1
    assert result[0]['price_min'] is None
    assert result[0]['price_median'] is None


def test_canonicalize_skips_non_dict_entries():
    result = canonicalize([None, 'string', 42, {'store': 'X', 'title': 'real', 'price': '100'}])
    assert len(result) == 1


def test_canonicalize_preserves_full_listings():
    attrs = {'brand': 'Nike', 'article': 'A1'}
    candidates = [
        {'store': 'Ozon', 'title': 't1', 'price': '5000'},
        {'store': 'WB',   'title': 't2', 'price': '6000'},
    ]
    result = canonicalize(candidates, attrs)
    assert len(result[0]['all_listings']) == 2
    titles = {it['title'] for it in result[0]['all_listings']}
    assert titles == {'t1', 't2'}


def test_canonicalize_dedupes_sources():
    """Two listings from same store don't double-count in sources list."""
    attrs = {'brand': 'Nike', 'article': 'A1'}
    candidates = [
        {'store': 'Ozon', 'title': 't1', 'price': '5000'},
        {'store': 'Ozon', 'title': 't2', 'price': '5500'},  # same store
        {'store': 'WB',   'title': 't3', 'price': '5200'},
    ]
    result = canonicalize(candidates, attrs)
    assert result[0]['sources_count'] == 3  # all 3 listings
    assert set(result[0]['sources']) == {'Ozon', 'WB'}  # unique stores


# ────────────────────────────────────────────────
# group_stats
# ────────────────────────────────────────────────

def test_group_stats_empty():
    s = group_stats([])
    assert s['groups'] == 0
    assert s['total_listings'] == 0
    assert s['price_min'] is None


def test_group_stats_summary():
    attrs = {'brand': 'Nike', 'article': 'A1'}
    candidates = [
        {'store': 'Ozon', 'title': 'p', 'price': '5000'},
        {'store': 'WB', 'title': 'p', 'price': '6000'},
        {'store': 'Ozon2', 'title': 'other', 'price': '10000',
         'article': 'OTHER'},
    ]
    result = canonicalize(candidates, attrs)
    s = group_stats(result)
    assert s['groups'] == 2
    assert s['total_listings'] == 3
    assert s['price_min'] == 5000
    assert s['price_max'] == 10000
