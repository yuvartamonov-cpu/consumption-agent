"""Tests for ml_taste — time-decayed taste profile + re-ranking."""
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_taste as mt
from ml_taste import (
    build_taste_profile,
    taste_score,
    rank_candidates,
    get_trust,
    SOURCE_TRUST,
)


# ────────────────────────────────────────────────
# get_trust
# ────────────────────────────────────────────────

def test_trust_known_sources():
    assert get_trust('brand_site') == 1.00
    assert get_trust('Lamoda') == 0.90
    assert get_trust('OZON') == 0.70
    assert get_trust('Wildberries') == 0.70
    assert get_trust('avito') == 0.40


def test_trust_unknown_falls_back():
    assert get_trust(None) == 0.50
    assert get_trust('') == 0.50
    assert get_trust('SomeNewMarketplace') == 0.50


def test_trust_substring_match():
    # "Yandex.Market via SerpAPI" should still map to ym tier
    assert get_trust('Yandex.Market via SerpAPI') == 0.70
    # "Ozon Express" likewise
    assert get_trust('Ozon Express') == 0.70


# ────────────────────────────────────────────────
# _tokens & private helpers
# ────────────────────────────────────────────────

def test_tokens_extracts_words():
    assert mt._tokens('Серое пальто шерсть') == ['серое', 'пальто', 'шерсть']


def test_tokens_strips_stop_words():
    out = mt._tokens('нравится купить серое пальто')
    assert 'нравится' not in out
    assert 'купить' not in out
    assert 'серое' in out


def test_tokens_handles_hashtag_prefix():
    assert mt._tokens('#пальто') == ['пальто']


def test_tokens_handles_lists():
    assert set(mt._tokens(['#пальто', 'шерсть'])) == {'пальто', 'шерсть'}


def test_tokens_empty_inputs():
    assert mt._tokens(None) == []
    assert mt._tokens('') == []
    assert mt._tokens([]) == []


def test_tokens_drops_single_char():
    assert mt._tokens('a b cd') == ['cd']


# ────────────────────────────────────────────────
# _decay_weight
# ────────────────────────────────────────────────

def test_decay_recent_full_weight():
    assert mt._decay_weight(0, 180) == 1.0


def test_decay_one_halflife_period():
    # exp(-180/180) = exp(-1) ≈ 0.367
    w = mt._decay_weight(180, 180)
    assert 0.35 < w < 0.38


def test_decay_future_dates_clamped_to_one():
    assert mt._decay_weight(-5, 180) == 1.0


# ────────────────────────────────────────────────
# _item_sentiment
# ────────────────────────────────────────────────

def test_sentiment_positive_only():
    assert mt._item_sentiment('["нравится"]', '[]') == 1


def test_sentiment_negative_only():
    assert mt._item_sentiment('[]', '["фу"]') == -1


def test_sentiment_neutral_both_empty():
    assert mt._item_sentiment('[]', '[]') == 0


def test_sentiment_ambiguous_both_filled():
    assert mt._item_sentiment('["нравится"]', '["не нравится"]') == 0


def test_sentiment_robust_to_invalid_json():
    assert mt._item_sentiment('not json', None) == 0


# ────────────────────────────────────────────────
# build_taste_profile — DB integration
# ────────────────────────────────────────────────

def _setup_ml_db(rows: list[dict], *, has_deleted_at: bool = True,
                 has_vision: bool = True, has_attrs: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    extras = []
    if has_deleted_at:
        extras.append('deleted_at TEXT')
    if has_vision:
        extras += ['name TEXT', 'description TEXT', 'brand TEXT']
    if has_attrs:
        extras.append('attributes_json TEXT')
    extra_sql = (', ' + ', '.join(extras)) if extras else ''
    conn.execute(
        f"CREATE TABLE memory_lane_items ("
        f"  id INTEGER PRIMARY KEY,"
        f"  profile_id TEXT DEFAULT 'default',"
        f"  created_at TEXT,"
        f"  caption TEXT,"
        f"  liked_features TEXT DEFAULT '[]',"
        f"  disliked_features TEXT DEFAULT '[]',"
        f"  style_tags TEXT DEFAULT '[]',"
        f"  topic TEXT"
        f"  {extra_sql}"
        f")"
    )
    for r in rows:
        # Default created_at to now if missing
        r = {**r}
        r.setdefault('created_at', "datetime('now')")
        cols = list(r.keys())
        placeholders = ','.join(
            f"datetime('now')" if r[k] == "datetime('now')" else '?'
            for k in cols
        )
        values = [r[k] for k in cols if r[k] != "datetime('now')"]
        conn.execute(
            f"INSERT INTO memory_lane_items ({','.join(cols)}) VALUES ({placeholders})",
            values
        )
    return conn


def test_profile_empty_db():
    conn = _setup_ml_db([])
    p = build_taste_profile(conn)
    assert p['positive'] == {}
    assert p['negative'] == {}
    assert p['n_positive_items'] == 0
    conn.close()


def test_profile_only_neutral_items_skipped():
    conn = _setup_ml_db([
        {'liked_features': '[]', 'disliked_features': '[]',
         'style_tags': '["пальто"]', 'topic': 'одежда'},
    ])
    p = build_taste_profile(conn)
    assert p['positive'] == {}
    assert p['negative'] == {}
    conn.close()


def test_profile_collects_positive_tokens():
    conn = _setup_ml_db([
        {'liked_features': '["нравится"]', 'disliked_features': '[]',
         'style_tags': '["пальто", "серое"]', 'topic': 'одежда',
         'brand': 'Massimo Dutti', 'name': 'Шерстяное пальто'},
    ])
    p = build_taste_profile(conn)
    assert p['n_positive_items'] == 1
    assert 'пальто' in p['positive']
    assert 'серое' in p['positive']
    assert 'одежда' in p['positive']
    # Brand tokens included
    assert any(t.startswith('massimo') for t in p['positive']) or 'massimo' in p['positive']
    assert 'шерстяное' in p['positive']
    # No negative signals
    assert p['negative'] == {}
    conn.close()


def test_profile_collects_negative_tokens():
    conn = _setup_ml_db([
        {'liked_features': '[]', 'disliked_features': '["фу"]',
         'style_tags': '["пиджак"]', 'topic': 'одежда'},
    ])
    p = build_taste_profile(conn)
    assert p['n_negative_items'] == 1
    assert 'пиджак' in p['negative']
    assert 'одежда' in p['negative']
    conn.close()


def test_profile_recency_decay():
    """Recent items dominate over older ones for the same token."""
    conn = _setup_ml_db([
        {'created_at': '2020-01-01',
         'liked_features': '["нравится"]', 'style_tags': '["пальто"]'},
        # Today
    ])
    # Add a recent one via raw insert to use real datetime('now')
    conn.execute(
        "INSERT INTO memory_lane_items "
        "(liked_features, disliked_features, style_tags, created_at) "
        "VALUES ('[\"нравится\"]', '[]', '[\"пальто\"]', datetime('now'))"
    )
    p = build_taste_profile(conn, decay_days=180)
    # Old item weight ~ exp(-365*X/180) — very small
    # New item weight ~ 1.0
    # Total should be slightly above 1.0, but well below 2.0
    assert p['positive']['пальто'] > 0.99
    assert p['positive']['пальто'] < 1.5
    conn.close()


def test_profile_uses_attributes_json():
    """Attributes from Stage 1 contribute tokens to the profile."""
    attrs = {
        'subcategory': 'кроссовки', 'brand': 'Nike',
        'primary_color': 'белый', 'material': 'кожа',
        'style': ['streetwear', 'minimalism'],
    }
    conn = _setup_ml_db([
        {'liked_features': '["нравится"]', 'disliked_features': '[]',
         'style_tags': '[]', 'topic': 'обувь',
         'attributes_json': json.dumps(attrs)},
    ])
    p = build_taste_profile(conn)
    assert 'кроссовки' in p['positive']
    assert 'белый' in p['positive']
    assert 'streetwear' in p['positive']
    assert 'nike' in p['positive']
    conn.close()


def test_profile_excludes_soft_deleted():
    conn = _setup_ml_db([
        {'liked_features': '["нравится"]', 'style_tags': '["пальто"]',
         'deleted_at': '2025-01-01'},
        {'liked_features': '["нравится"]', 'style_tags': '["пиджак"]'},
    ])
    p = build_taste_profile(conn)
    assert 'пиджак' in p['positive']
    assert 'пальто' not in p['positive']  # deleted
    conn.close()


def test_profile_filters_by_profile_id():
    conn = _setup_ml_db([
        {'liked_features': '["нравится"]', 'style_tags': '["пальто"]',
         'profile_id': 'default'},
        {'liked_features': '["нравится"]', 'style_tags': '["диван"]',
         'profile_id': 'other'},
    ])
    p = build_taste_profile(conn, profile_id='default')
    assert 'пальто' in p['positive']
    assert 'диван' not in p['positive']
    conn.close()


def test_profile_handles_schema_without_optional_columns():
    """Older schemas without name/description/brand/attributes_json."""
    conn = _setup_ml_db([
        {'liked_features': '["нравится"]', 'style_tags': '["пальто"]',
         'topic': 'одежда'}
    ], has_vision=False, has_attrs=False, has_deleted_at=False)
    p = build_taste_profile(conn)
    assert 'пальто' in p['positive']
    assert 'одежда' in p['positive']
    conn.close()


def test_profile_robust_to_invalid_json_in_style_tags():
    conn = _setup_ml_db([
        {'liked_features': '["нравится"]', 'style_tags': 'not json',
         'topic': 'одежда'}
    ])
    p = build_taste_profile(conn)
    # Style tags parse fails, but topic still contributes
    assert 'одежда' in p['positive']
    conn.close()


def test_profile_dedupes_tokens_within_one_item():
    """A single item should add weight 1×w per token, not multiple times
    if the same token appears in style_tags AND name AND description."""
    conn = _setup_ml_db([
        {'liked_features': '["нравится"]',
         'style_tags': '["пальто"]', 'topic': 'пальто',
         'name': 'пальто', 'description': 'пальто пальто пальто',
         'created_at': "datetime('now')"}
    ])
    p = build_taste_profile(conn, decay_days=180)
    # One recent item with weight≈1.0 → token weight should be ≈1.0
    assert 0.9 < p['positive']['пальто'] < 1.1
    conn.close()


# ────────────────────────────────────────────────
# taste_score
# ────────────────────────────────────────────────

def test_score_no_profile_returns_zero():
    assert taste_score('anything', None) == 0.0
    assert taste_score('anything', {}) == 0.0
    assert taste_score('anything', {'positive': {}, 'negative': {}}) == 0.0


def test_score_positive_match():
    profile = {'positive': {'пальто': 2.0, 'серый': 1.0}, 'negative': {}}
    assert taste_score('Серое шерстяное пальто', profile) > 0


def test_score_negative_match():
    profile = {'positive': {}, 'negative': {'красный': 3.0}}
    # Exact token match — we don't do stemming
    assert taste_score('красный портфель', profile) < 0


def test_score_no_overlap_zero():
    profile = {'positive': {'пальто': 2.0}, 'negative': {}}
    assert taste_score('телефон iphone', profile) == 0.0


def test_score_in_range_minus_one_to_one():
    big_profile = {'positive': {'пальто': 1000.0}, 'negative': {}}
    s = taste_score('пальто', big_profile)
    assert -1.0 <= s <= 1.0


def test_score_negative_outweighs_positive():
    profile = {'positive': {'пальто': 1.0}, 'negative': {'пальто': 5.0}}
    # Same token in both — net negative
    assert taste_score('Шерстяное пальто', profile) < 0


# ────────────────────────────────────────────────
# _price_advantage
# ────────────────────────────────────────────────

def test_price_advantage_normal():
    adv = mt._price_advantage([1000, 5000, 10000])
    assert adv[0] == 1.0   # cheapest
    assert adv[2] == 0.0   # most expensive
    assert 0.4 < adv[1] < 0.6  # middle


def test_price_advantage_all_equal():
    adv = mt._price_advantage([5000, 5000, 5000])
    assert adv == [1.0, 1.0, 1.0]


def test_price_advantage_with_none():
    adv = mt._price_advantage([1000, None, 5000])
    assert adv[0] == 1.0
    assert adv[1] == 0.5    # missing price → neutral
    assert adv[2] == 0.0


def test_price_advantage_empty():
    assert mt._price_advantage([]) == []


def test_price_advantage_all_none():
    assert mt._price_advantage([None, None, None]) == [0.5, 0.5, 0.5]


# ────────────────────────────────────────────────
# rank_candidates
# ────────────────────────────────────────────────

def test_rank_empty():
    assert rank_candidates([], None) == []


def test_rank_sorts_descending_by_final_score():
    profile = {'positive': {'пальто': 5.0, 'серый': 3.0}, 'negative': {}}
    rows = [
        {'title': 'iPhone 15 Pro', 'store': 'Ozon', 'price_min': 100000},
        {'title': 'Серое шерстяное пальто', 'store': 'Lamoda',
         'price_min': 10000},
        {'title': 'Чайник', 'store': 'Wildberries', 'price_min': 3000},
    ]
    ranked = rank_candidates(rows, profile)
    # Серое пальто on Lamoda must come first (taste+trust+price decent)
    assert 'пальто' in ranked[0]['title']


def test_rank_attaches_breakdown():
    profile = {'positive': {'пальто': 2.0}, 'negative': {}}
    rows = [{'title': 'Пальто серое', 'store': 'Ozon', 'price_min': 10000}]
    ranked = rank_candidates(rows, profile)
    r = ranked[0]
    assert '_taste' in r
    assert '_trust' in r
    assert '_price_advantage' in r
    assert '_final_score' in r
    assert 'taste' in r['_score_breakdown']


def test_rank_trust_affects_outcome():
    """Same product, two sources — higher-trust source ranks higher."""
    profile = None  # No taste signal
    rows = [
        {'title': 'Кроссовки', 'store': 'Ozon', 'price_min': 5000},
        {'title': 'Кроссовки', 'store': 'Lamoda', 'price_min': 5000},
        {'title': 'Кроссовки', 'store': 'Avito', 'price_min': 5000},
    ]
    ranked = rank_candidates(rows, profile)
    assert ranked[0]['store'] == 'Lamoda'   # 0.90
    assert ranked[-1]['store'] == 'Avito'   # 0.40


def test_rank_price_advantage_breaks_ties():
    """No taste, same trust — cheaper wins."""
    rows = [
        {'title': 'X', 'store': 'Ozon', 'price_min': 10000},
        {'title': 'X', 'store': 'Ozon', 'price_min': 5000},
    ]
    ranked = rank_candidates(rows, None)
    assert ranked[0]['price_min'] == 5000


def test_rank_custom_weights():
    """Pass weights to override defaults."""
    profile = {'positive': {'пальто': 5.0}, 'negative': {}}
    rows = [
        {'title': 'Пальто', 'store': 'Avito', 'price_min': 10000},     # taste hit, low trust
        {'title': 'Чайник', 'store': 'brand_site', 'price_min': 10000},  # no taste, max trust
    ]
    # Trust-dominant weights → brand_site wins
    r_trust = rank_candidates(rows, profile, weights={'taste': 0.0, 'trust': 1.0, 'price': 0.0})
    assert r_trust[0]['store'] == 'brand_site'
    # Taste-dominant weights → Avito wins
    r_taste = rank_candidates(rows, profile, weights={'taste': 1.0, 'trust': 0.0, 'price': 0.0})
    assert r_taste[0]['store'] == 'Avito'


def test_rank_visual_sim_uses_existing_field():
    """If a row already has _visual_sim (from Stage 5), it contributes."""
    rows = [
        {'title': 'A', 'store': 'X', 'price_min': 1000, '_visual_sim': 0.9},
        {'title': 'B', 'store': 'X', 'price_min': 1000, '_visual_sim': 0.1},
    ]
    # When we give visual a weight, A should win
    ranked = rank_candidates(rows, None,
                             weights={'visual': 1.0, 'taste': 0.0,
                                      'trust': 0.0, 'price': 0.0})
    assert ranked[0]['title'] == 'A'
