"""Tests for ml_search_v2 — full visual-product-search pipeline."""
import asyncio
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_search_v2 as v2
from ml_search_v2 import (
    _select_provider_queries,
    search_ml_item_v2,
    format_search_result_telegram,
    route_sources,
)


# ────────────────────────────────────────────────
# route_sources
# ────────────────────────────────────────────────

def test_route_sources_uses_category_map():
    sources = route_sources({'category': 'одежда'})
    assert 'lamoda' in sources
    assert 'wildberries' in sources
    assert 'aliexpress' in sources


def test_route_sources_default_for_unknown_category():
    sources = route_sources({'category': 'неизвестно'})
    assert 'wildberries' in sources
    assert 'lamoda' in sources
    assert 'alibaba' in sources


def test_route_sources_prepends_brand_site():
    sources = route_sources({'category': 'одежда', 'brand': 'Nike'})
    assert sources[0] == 'brand:Nike'


def test_route_sources_empty_attrs():
    sources = route_sources({})
    assert sources  # falls back to DEFAULT_SOURCES


def test_select_provider_queries_keeps_only_branded_variants():
    queries = [
        ('hamington джемпер серый', 'brand_subcat'),
        ('джемпер серый', 'descriptive'),
        ('джемпер casual', 'style_broad'),
    ]
    assert _select_provider_queries(queries, {'brand': 'hamington'}) == [
        'hamington джемпер серый'
    ]


# ────────────────────────────────────────────────
# Test DB helper
# ────────────────────────────────────────────────

def _setup_full_db(item_rows=None, inventory_rows=None) -> sqlite3.Connection:
    """Build an in-memory DB with the schema search_ml_item_v2 expects."""
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE memory_lane_items (
            id INTEGER PRIMARY KEY,
            profile_id TEXT DEFAULT 'default',
            created_at TEXT DEFAULT (datetime('now')),
            caption TEXT,
            liked_features TEXT DEFAULT '[]',
            disliked_features TEXT DEFAULT '[]',
            style_tags TEXT DEFAULT '[]',
            topic TEXT,
            media_asset_id INTEGER,
            name TEXT, description TEXT, brand TEXT,
            attributes_json TEXT,
            deleted_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE media_assets (
            id INTEGER PRIMARY KEY,
            file_path TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            brand TEXT, model TEXT, sku TEXT,
            status TEXT, purchase_date TEXT, purchase_price REAL,
            deleted_at TEXT
        )
    """)
    for r in item_rows or []:
        cols = list(r.keys())
        conn.execute(
            f"INSERT INTO memory_lane_items ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            [r[k] for k in cols]
        )
    for r in inventory_rows or []:
        cols = list(r.keys())
        conn.execute(
            f"INSERT INTO items ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            [r[k] for k in cols]
        )
    return conn


def _run(coro):
    return asyncio.run(coro)


# ────────────────────────────────────────────────
# search_ml_item_v2 — happy path
# ────────────────────────────────────────────────

def test_pipeline_missing_item():
    conn = _setup_full_db()
    result = _run(search_ml_item_v2(conn, 999))
    assert result['errors']
    assert result['canonical_groups'] == []
    conn.close()


def test_pipeline_uses_cached_attributes():
    """If attributes_json exists, no Vision call is made."""
    cached = {
        'category': 'одежда', 'subcategory': 'пальто',
        'brand': 'Massimo Dutti', 'primary_color': 'серый',
        'confidence': 0.9,
    }
    conn = _setup_full_db([{
        'id': 1, 'caption': 'нравится #пальто',
        'liked_features': '["нравится"]',
        'style_tags': '["пальто"]', 'topic': 'одежда',
        'attributes_json': json.dumps(cached),
    }])
    call_count = [0]

    async def fail_extractor(*a, **kw):
        call_count[0] += 1
        return {}

    async def empty_provider(queries, sources, photo):
        return []

    result = _run(search_ml_item_v2(
        conn, 1,
        attribute_extractor=fail_extractor,
        candidates_provider=empty_provider,
    ))
    assert call_count[0] == 0   # cached, no Vision call
    assert result['attributes']['brand'] == 'Massimo Dutti'
    conn.close()


def test_pipeline_calls_extractor_when_no_cache():
    conn = _setup_full_db([{
        'id': 1, 'caption': 'тест',
        'liked_features': '[]',
        'style_tags': '[]',
    }])
    extractor_calls = []

    async def my_extractor(photo_path, caption):
        extractor_calls.append((photo_path, caption))
        return {
            'category': 'обувь', 'subcategory': 'кроссовки',
            'brand': 'Nike', 'primary_color': 'белый',
            'confidence': 0.8,
        }

    async def empty_provider(queries, sources, photo):
        return []

    result = _run(search_ml_item_v2(
        conn, 1,
        attribute_extractor=my_extractor,
        candidates_provider=empty_provider,
    ))
    assert len(extractor_calls) == 1
    assert result['attributes']['brand'] == 'Nike'
    # Result was persisted
    row = conn.execute("SELECT attributes_json FROM memory_lane_items WHERE id=1").fetchone()
    assert row[0] is not None
    persisted = json.loads(row[0])
    assert persisted['brand'] == 'Nike'
    conn.close()


def test_pipeline_force_refresh_ignores_cache():
    conn = _setup_full_db([{
        'id': 1, 'caption': 'x',
        'liked_features': '[]', 'style_tags': '[]',
        'attributes_json': json.dumps({'brand': 'OLD'}),
    }])
    calls = [0]

    async def fresh_extractor(photo, caption):
        calls[0] += 1
        return {'brand': 'NEW', 'category': 'одежда'}

    async def empty(q, s, p):
        return []

    result = _run(search_ml_item_v2(
        conn, 1, attribute_extractor=fresh_extractor,
        candidates_provider=empty, force_refresh_attrs=True,
    ))
    assert calls[0] == 1
    assert result['attributes']['brand'] == 'NEW'
    conn.close()


def test_pipeline_canonicalizes_candidates():
    conn = _setup_full_db([{
        'id': 1, 'caption': 'нравится',
        'liked_features': '["нравится"]',
        'style_tags': '["кроссовки"]', 'topic': 'обувь',
        'attributes_json': json.dumps({
            'category': 'обувь', 'subcategory': 'кроссовки',
            'brand': 'Nike', 'article': 'AF1-001',
            'primary_color': 'белый',
        }),
    }])

    async def fake_provider(queries, sources, photo):
        # 3 listings on different stores, same article
        return [
            {'store': 'Ozon', 'title': 'Nike AF1', 'price': '12990',
             'url': 'ozon.ru/1'},
            {'store': 'Wildberries', 'title': 'AF1 White', 'price': '11590',
             'url': 'wb.ru/1'},
            {'store': 'Lamoda', 'title': 'Nike', 'price': '12500',
             'url': 'lamoda.ru/1'},
        ]

    async def cached_extractor(*a, **kw):
        return {}

    result = _run(search_ml_item_v2(
        conn, 1, candidates_provider=fake_provider,
        attribute_extractor=cached_extractor,
    ))
    # 3 listings → 1 canonical group (same brand+article)
    assert len(result['canonical_groups']) == 1
    g = result['canonical_groups'][0]
    assert g['sources_count'] == 3
    assert g['price_min'] == 11590
    conn.close()


def test_pipeline_flags_anomaly():
    conn = _setup_full_db([{
        'id': 1, 'caption': 'x',
        'liked_features': '["нравится"]',
        'style_tags': '[]',
        'attributes_json': json.dumps({
            'category': 'обувь', 'brand': 'Nike', 'article': 'A1',
            'estimated_price_rub': 12000,
        }),
    }])

    async def cheap_provider(queries, sources, photo):
        # Median 12000, but one listing at 2000 (suspicious)
        return [
            {'store': 'Ozon', 'title': 'p', 'price': '12000'},
            {'store': 'WB', 'title': 'p', 'price': '12500'},
            {'store': 'Avito', 'title': 'p', 'price': '2000'},
        ]

    result = _run(search_ml_item_v2(
        conn, 1, candidates_provider=cheap_provider,
        attribute_extractor=lambda *a, **kw: asyncio.sleep(0, result={}),
    ))
    g = result['canonical_groups'][0]
    assert g.get('anomaly') is not None
    assert g['anomaly']['kind'] == 'suspicious_cheap'
    conn.close()


def test_pipeline_detects_inventory_collision():
    conn = _setup_full_db(
        item_rows=[{
            'id': 1, 'caption': 'x',
            'liked_features': '["нравится"]',
            'attributes_json': json.dumps({
                'category': 'обувь', 'subcategory': 'кроссовки',
                'brand': 'Nike', 'model': 'Air Force 1',
                'primary_color': 'белый',
            }),
        }],
        inventory_rows=[{
            'id': 100, 'name': 'Nike Air Force 1 кроссовки белые',
            'brand': 'Nike', 'model': 'Air Force 1',
            'status': 'in_use', 'purchase_date': '2025-06-01'
        }],
    )

    async def cached(*a, **kw):
        return {}

    async def empty(q, s, p):
        return []

    result = _run(search_ml_item_v2(
        conn, 1, attribute_extractor=cached, candidates_provider=empty,
    ))
    assert result['inventory_collisions']
    assert result['collision_warning'] is not None
    assert 'Nike' in result['collision_warning']
    conn.close()


def test_pipeline_taste_ranks_results():
    """User who liked серое пальто earlier — that result should rank higher."""
    conn = _setup_full_db([
        # Historical positive: серый плащ
        {'id': 1, 'caption': 'нравится #серое #плащ',
         'liked_features': '["нравится"]',
         'style_tags': '["серое", "плащ"]', 'topic': 'одежда'},
        # The query item
        {'id': 2, 'caption': 'нравится',
         'liked_features': '["нравится"]',
         'style_tags': '[]',
         'attributes_json': json.dumps({
             'category': 'одежда', 'subcategory': 'пальто',
             'brand': 'X', 'article': 'PAL1',
         })},
    ])

    async def provider(queries, sources, photo):
        # Two products with same brand/article but different titles
        return [
            {'store': 'Ozon', 'title': 'Красное пальто', 'price': '10000',
             'article': 'PAL1'},
            {'store': 'Ozon', 'title': 'Серое пальто', 'price': '10000',
             'article': 'PAL1'},
        ]

    async def cached(*a, **kw):
        return {}

    result = _run(search_ml_item_v2(
        conn, 2, candidates_provider=provider, attribute_extractor=cached,
    ))
    # Same canonical group (same article) — but the test is about ranking.
    # Since they collapse into one group, let's adjust: give them different
    # articles so they stay separate.
    conn.close()


def test_pipeline_taste_ranks_separate_groups():
    """Two distinct products: the one matching user's taste ranks higher."""
    conn = _setup_full_db([
        # Earlier liked: серое пальто
        {'id': 1, 'caption': 'нравится',
         'liked_features': '["нравится"]',
         'style_tags': '["серое", "пальто"]', 'topic': 'одежда'},
        {'id': 2, 'caption': 'нравится',
         'liked_features': '["нравится"]',
         'style_tags': '[]',
         'attributes_json': json.dumps({
             'category': 'одежда', 'subcategory': 'пальто',
         })},
    ])

    async def provider(queries, sources, photo):
        return [
            {'store': 'Ozon', 'title': 'красное пальто шерсть',
             'price': '10000', 'article': 'A1'},
            {'store': 'Ozon', 'title': 'серое пальто шерсть',
             'price': '10000', 'article': 'A2'},
        ]

    async def cached(*a, **kw):
        return {}

    result = _run(search_ml_item_v2(
        conn, 2, candidates_provider=provider, attribute_extractor=cached,
    ))
    assert len(result['canonical_groups']) == 2
    # Serое пальто should rank higher because user previously liked серое
    assert 'серое' in result['canonical_groups'][0]['title']
    conn.close()


def test_pipeline_no_queries_no_search():
    """No usable attributes → no queries → empty candidates."""
    conn = _setup_full_db([{
        'id': 1, 'caption': '',
        'liked_features': '[]', 'style_tags': '[]',
        'attributes_json': json.dumps({}),  # validates to all-None
    }])
    called = [0]

    async def provider(q, s, p):
        called[0] += 1
        return []

    async def cached(*a, **kw):
        return {}

    result = _run(search_ml_item_v2(
        conn, 1, candidates_provider=provider, attribute_extractor=cached,
    ))
    # No queries built → provider not invoked
    assert called[0] == 0
    assert result['errors']
    conn.close()


def test_pipeline_provider_exception_doesnt_crash():
    conn = _setup_full_db([{
        'id': 1, 'caption': 'x',
        'liked_features': '["нравится"]',
        'attributes_json': json.dumps({
            'category': 'одежда', 'subcategory': 'пальто',
            'brand': 'X',
        }),
    }])

    async def broken_provider(q, s, p):
        raise RuntimeError("API rate limited")

    async def cached(*a, **kw):
        return {}

    result = _run(search_ml_item_v2(
        conn, 1, candidates_provider=broken_provider,
        attribute_extractor=cached,
    ))
    assert any('rate limited' in e for e in result['errors'])
    assert result['canonical_groups'] == []
    conn.close()


def test_pipeline_filters_out_foreign_brand_results():
    conn = _setup_full_db([{
        'id': 1,
        'caption': 'нравится джемпер hamington',
        'liked_features': '["нравится"]',
        'style_tags': '["casual"]',
        'topic': 'одежда',
        'brand': 'hamington',
        'attributes_json': json.dumps({
            'category': 'одежда',
            'subcategory': 'джемпер',
            'brand': 'hamington',
            'primary_color': 'серый',
        }),
    }])

    async def noisy_provider(q, s, p):
        return [
            {'store': 'Wildberries', 'title': 'Джемпер женский серый', 'brand': 'AITA MODA',
             'price': '999', 'url': 'https://wb.ru/1'},
            {'store': 'Wildberries', 'title': 'Remington sweater', 'brand': 'Remington',
             'price': '1999', 'url': 'https://wb.ru/2'},
        ]

    async def cached(*a, **kw):
        return {}

    result = _run(search_ml_item_v2(
        conn, 1, candidates_provider=noisy_provider, attribute_extractor=cached,
    ))
    assert result['canonical_groups'] == []
    assert any('hamington' in e for e in result['errors'])
    conn.close()


def test_link_only_results_do_not_collapse_into_one_group():
    conn = _setup_full_db([{
        'id': 1,
        'caption': 'x',
        'liked_features': '["нравится"]',
        'attributes_json': json.dumps({
            'category': 'одежда', 'subcategory': 'джемпер', 'brand': 'hamington'
        }),
    }])

    async def link_provider(q, s, p):
        return [
            {'title': '🔗 hamington: Google', 'store': 'Официальный сайт', 'source': 'brand_site', 'url': 'https://google.example', '_link_only': True},
            {'title': '🔗 Lamoda: hamington', 'store': 'Lamoda', 'source': 'lamoda', 'url': 'https://lamoda.example', '_link_only': True},
        ]

    async def cached(*a, **kw):
        return {}

    result = _run(search_ml_item_v2(
        conn, 1, candidates_provider=link_provider, attribute_extractor=cached,
    ))
    assert len(result['canonical_groups']) == 2
    conn.close()


# ────────────────────────────────────────────────
# format_search_result_telegram
# ────────────────────────────────────────────────

def test_format_no_results():
    result = {
        'item_id': 1, 'attributes': {'subcategory': 'пальто', 'brand': 'X'},
        'canonical_groups': [], 'errors': [], 'collision_warning': None,
        'summary': {},
    }
    out = format_search_result_telegram(result)
    assert 'пальто' in out
    assert 'не нашёл' in out.lower() or 'ничего' in out.lower()


def test_format_with_groups():
    result = {
        'item_id': 1,
        'attributes': {'subcategory': 'пальто', 'brand': 'Massimo'},
        'canonical_groups': [
            {'title': 'Massimo пальто серое', 'store': 'Lamoda',
             'price_min': 10000, 'price_max': 12000, 'sources_count': 2,
             'url': 'lamoda.ru/x'},
        ],
        'summary': {'groups': 1, 'total_listings': 2},
        'errors': [], 'collision_warning': None,
    }
    out = format_search_result_telegram(result)
    assert 'Massimo' in out
    assert 'Lamoda' in out
    assert '10 000' in out  # range formatted
    assert 'lamoda.ru/x' in out


def test_format_with_collision_warning():
    result = {
        'item_id': 1, 'attributes': {'subcategory': 'X'},
        'canonical_groups': [], 'summary': {},
        'errors': [],
        'collision_warning': '🟡 У вас уже есть похожее:\n  • «Парка»',
    }
    out = format_search_result_telegram(result)
    assert 'уже есть похожее' in out


def test_format_with_anomaly():
    result = {
        'item_id': 1, 'attributes': {'subcategory': 'X'},
        'canonical_groups': [{
            'title': 'X', 'store': 'Ozon', 'price_min': 1000,
            'price_max': 1000, 'sources_count': 1, 'url': 'u',
            'anomaly': {'kind': 'suspicious_cheap',
                        'reason': 'Слишком дёшево'},
        }],
        'summary': {'groups': 1, 'total_listings': 1},
        'errors': [], 'collision_warning': None,
    }
    out = format_search_result_telegram(result)
    assert 'Слишком дёшево' in out
    assert '⚠️' in out


def test_format_html_escapes_user_content():
    result = {
        'item_id': 1, 'attributes': {'subcategory': '<script>'},
        'canonical_groups': [{
            'title': 'Item <b>X</b>', 'store': 'O', 'price_min': 100,
            'sources_count': 1, 'url': 'https://x',
        }],
        'summary': {'groups': 1, 'total_listings': 1},
        'errors': [], 'collision_warning': None,
    }
    out = format_search_result_telegram(result)
    # < and > escaped
    assert '<script>' not in out
    assert '&lt;script&gt;' in out
    assert 'Item &lt;b&gt;X&lt;/b&gt;' in out


def test_format_caps_groups_shown():
    groups = [{
        'title': f'item{i}', 'store': 'O',
        'price_min': 100, 'sources_count': 1,
    } for i in range(10)]
    result = {
        'item_id': 1, 'attributes': {'subcategory': 'X'},
        'canonical_groups': groups,
        'summary': {'groups': 10, 'total_listings': 10},
        'errors': [], 'collision_warning': None,
    }
    out = format_search_result_telegram(result, max_groups=3)
    assert 'item0' in out
    assert 'item2' in out
    assert 'item3' not in out
    assert 'ещё 7' in out
