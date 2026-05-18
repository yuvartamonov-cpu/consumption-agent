"""Tests for ml_inventory — text-only inventory collision detection."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_inventory as mi
from ml_inventory import (
    build_query_text,
    score_candidate,
    find_inventory_collisions,
    format_collision_warning,
)


# ────────────────────────────────────────────────
# build_query_text
# ────────────────────────────────────────────────

def test_build_query_full_attrs():
    q = build_query_text({
        'brand': 'Nike', 'model': 'Air Force 1',
        'subcategory': 'кроссовки', 'primary_color': 'белый',
        'material': 'кожа',
    })
    assert q == 'Nike Air Force 1 кроссовки белый кожа'


def test_build_query_skips_null_strings():
    q = build_query_text({
        'brand': 'null', 'model': None,
        'subcategory': 'пальто', 'primary_color': 'серый',
    })
    assert q == 'пальто серый'


def test_build_query_falls_back_to_category():
    q = build_query_text({'brand': 'IKEA', 'category': 'мебель'})
    assert q == 'IKEA мебель'


def test_build_query_empty_input():
    assert build_query_text({}) == ''
    assert build_query_text(None) == ''


# ────────────────────────────────────────────────
# score_candidate
# ────────────────────────────────────────────────

def test_score_exact_name_match():
    item = {'name': 'Nike Air Force 1', 'brand': 'Nike'}
    score = score_candidate('Nike Air Force 1 кроссовки', {'brand': 'Nike'}, item)
    assert score >= 80


def test_score_unrelated_low():
    item = {'name': 'Чайник Bosch', 'brand': 'Bosch'}
    score = score_candidate('Nike Air Force 1', {'brand': 'Nike'}, item)
    assert score < 50


def test_score_brand_boost_applied():
    item_with_brand = {'name': 'Кеды белые', 'brand': 'Nike'}
    item_without_brand = {'name': 'Кеды белые', 'brand': 'Adidas'}
    s_with = score_candidate('Nike кеды белые', {'brand': 'Nike'},
                              item_with_brand)
    s_without = score_candidate('Nike кеды белые', {'brand': 'Nike'},
                                 item_without_brand)
    assert s_with > s_without


def test_score_model_boost_applied():
    a = {'name': 'Кроссовки', 'brand': 'Nike', 'model': 'Air Force 1'}
    b = {'name': 'Кроссовки', 'brand': 'Nike', 'model': 'Cortez'}
    sa = score_candidate('Nike Air Force кроссовки',
                          {'brand': 'Nike', 'model': 'Air Force 1'}, a)
    sb = score_candidate('Nike Air Force кроссовки',
                          {'brand': 'Nike', 'model': 'Air Force 1'}, b)
    assert sa > sb


def test_score_empty_item_returns_zero():
    assert score_candidate('Nike', {'brand': 'Nike'}, {}) == 0


def test_score_capped_at_100():
    item = {'name': 'Nike Air Force 1', 'brand': 'Nike', 'model': 'Air Force 1'}
    assert score_candidate(
        'Nike Air Force 1', {'brand': 'Nike', 'model': 'Air Force 1'}, item
    ) <= 100


# ────────────────────────────────────────────────
# find_inventory_collisions — SQL integration
# ────────────────────────────────────────────────

def _setup_inventory(rows: list[dict], *, has_deleted_at: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    extra = ', deleted_at TEXT' if has_deleted_at else ''
    conn.execute(
        f"CREATE TABLE items ("
        f"  id INTEGER PRIMARY KEY,"
        f"  name TEXT NOT NULL,"
        f"  brand TEXT, model TEXT, sku TEXT,"
        f"  status TEXT,"
        f"  purchase_date TEXT, purchase_price REAL"
        f"  {extra}"
        f")"
    )
    for r in rows:
        keys = list(r.keys())
        placeholders = ','.join('?' for _ in keys)
        conn.execute(
            f"INSERT INTO items ({','.join(keys)}) VALUES ({placeholders})",
            list(r.values())
        )
    return conn


def test_find_collisions_returns_strong_match():
    conn = _setup_inventory([
        {'name': 'Nike Air Force 1 кроссовки белые', 'brand': 'Nike',
         'status': 'in_use', 'purchase_date': '2025-12-01'},
    ])
    hits = find_inventory_collisions(conn, {
        'brand': 'Nike', 'subcategory': 'кроссовки',
        'primary_color': 'белый'
    })
    assert len(hits) == 1
    assert hits[0]['name'].startswith('Nike Air Force')
    assert hits[0]['similarity'] >= 75
    conn.close()


def test_find_collisions_drops_unrelated_items():
    conn = _setup_inventory([
        {'name': 'Чайник Bosch TWK', 'brand': 'Bosch', 'status': 'in_use'},
        {'name': 'Парка зимняя Uniqlo', 'brand': 'Uniqlo', 'status': 'in_use'},
    ])
    hits = find_inventory_collisions(conn, {
        'brand': 'Nike', 'subcategory': 'кроссовки',
    })
    # Brand filter excludes Bosch and Uniqlo → 0 candidates → 0 hits
    assert hits == []
    conn.close()


def test_find_collisions_no_brand_no_prefilter():
    conn = _setup_inventory([
        # Use a name that matches by exact tokens (avoiding Russian inflection
        # quirks like серый/серая that lower token_set_ratio below threshold)
        {'name': 'парка серый зимняя', 'brand': None, 'status': 'in_use',
         'purchase_date': '2025-03-15'},
        {'name': 'Чайник Bosch', 'brand': 'Bosch', 'status': 'in_use'},
    ])
    hits = find_inventory_collisions(conn, {
        'subcategory': 'парка', 'primary_color': 'серый'
    })
    # No brand → all items considered, text similarity finds the parka
    assert any('парка' in h['name'].lower() for h in hits)
    # Чайник should not be in the results
    assert not any('Чайник' in h['name'] for h in hits)
    conn.close()


def test_find_collisions_excludes_disposed():
    conn = _setup_inventory([
        {'name': 'Nike Air Force 1', 'brand': 'Nike', 'status': 'disposed'},
    ])
    hits = find_inventory_collisions(conn, {'brand': 'Nike'})
    assert hits == []
    conn.close()


def test_find_collisions_excludes_soft_deleted():
    conn = _setup_inventory([
        {'name': 'Nike Air Force 1', 'brand': 'Nike', 'status': 'in_use',
         'deleted_at': '2025-01-01'},
    ])
    hits = find_inventory_collisions(conn, {'brand': 'Nike'})
    assert hits == []
    conn.close()


def test_find_collisions_works_without_deleted_at_column():
    conn = _setup_inventory(
        [{'name': 'Nike Air Force 1 кроссовки', 'brand': 'Nike', 'status': 'in_use'}],
        has_deleted_at=False,
    )
    hits = find_inventory_collisions(conn, {
        'brand': 'Nike', 'subcategory': 'кроссовки'
    })
    assert len(hits) == 1
    conn.close()


def test_find_collisions_respects_threshold():
    conn = _setup_inventory([
        {'name': 'Кроссовки белые', 'brand': 'Nike', 'status': 'in_use'},
    ])
    # Threshold = 99: nothing will match this loose query well enough
    hits = find_inventory_collisions(conn, {
        'brand': 'Nike', 'subcategory': 'обувь'
    }, threshold=99)
    assert hits == []
    conn.close()


def test_find_collisions_sorted_desc_and_limited():
    conn = _setup_inventory([
        {'name': f'Nike Air Force 1 кроссовки белые', 'brand': 'Nike',
         'status': 'in_use'},
        {'name': f'Nike кеды', 'brand': 'Nike', 'status': 'in_use'},
        {'name': f'Nike обувь', 'brand': 'Nike', 'status': 'in_use'},
        {'name': f'Nike носки', 'brand': 'Nike', 'status': 'in_use'},
    ])
    hits = find_inventory_collisions(conn, {
        'brand': 'Nike', 'subcategory': 'кроссовки',
        'primary_color': 'белый', 'model': 'Air Force 1'
    }, limit=2, threshold=30)
    assert len(hits) <= 2
    # Descending
    assert all(hits[i]['similarity'] >= hits[i+1]['similarity']
               for i in range(len(hits) - 1))
    # Top match must be the Air Force 1 item
    assert 'Air Force' in hits[0]['name']
    conn.close()


def test_find_collisions_empty_attrs_returns_empty():
    conn = _setup_inventory([
        {'name': 'X', 'brand': 'X', 'status': 'in_use'},
    ])
    assert find_inventory_collisions(conn, {}) == []
    conn.close()


# ────────────────────────────────────────────────
# format_collision_warning
# ────────────────────────────────────────────────

def test_format_empty_collisions_returns_none():
    assert format_collision_warning([]) is None


def test_format_single_hit():
    msg = format_collision_warning([{
        'id': 1, 'name': 'Парка серая', 'brand': 'Uniqlo',
        'purchase_date': '2025-12-01', 'similarity': 87,
    }])
    assert 'Парка серая' in msg
    assert 'Uniqlo' in msg
    assert '87' in msg


def test_format_multiple_hits_truncated():
    hits = [
        {'name': f'item{i}', 'brand': 'B', 'purchase_date': '2025-01-01',
         'similarity': 80} for i in range(5)
    ]
    msg = format_collision_warning(hits, max_show=2)
    assert 'item0' in msg
    assert 'item1' in msg
    assert 'item2' not in msg
    assert 'ещё 3' in msg
