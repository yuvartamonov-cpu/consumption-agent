"""Tests for /ml_find and /ml_profile core logic (memory_lane.search_items / build_profile)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory_lane as ml
from consumption.db import connect


def _db(tmp_path: Path):
    conn = connect(tmp_path / 'ml.db')
    ml.ensure_memory_lane_schema(conn)
    ml._ensure_vision_columns(conn)
    return conn


def _add(conn, *, caption='', liked=None, disliked=None, tags=None, topic=None,
         name=None, description=None, brand=None):
    conn.execute(
        """
        INSERT INTO memory_lane_items
            (caption, liked_features, disliked_features, style_tags, topic,
             name, description, brand)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            caption,
            json.dumps(liked or [], ensure_ascii=False),
            json.dumps(disliked or [], ensure_ascii=False),
            json.dumps(tags or [], ensure_ascii=False),
            topic, name, description, brand,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# search_items
# ---------------------------------------------------------------------------

def test_search_empty_db_returns_empty(tmp_path):
    conn = _db(tmp_path)
    assert ml.search_items(conn, 'что угодно') == []
    conn.close()


def test_search_matches_caption(tmp_path):
    conn = _db(tmp_path)
    _add(conn, caption='нравится тёмное пальто', topic='одежда')
    _add(conn, caption='кожаный диван', topic='мебель')
    rows = ml.search_items(conn, 'пальто')
    assert len(rows) == 1
    assert rows[0]['caption'] == 'нравится тёмное пальто'
    conn.close()


def test_search_matches_name_and_brand(tmp_path):
    conn = _db(tmp_path)
    _add(conn, name='Кроссовки', brand='Nike', topic='обувь')
    assert len(ml.search_items(conn, 'nike')) == 1
    assert len(ml.search_items(conn, 'кроссовки')) == 1
    conn.close()


def test_search_topic_filter(tmp_path):
    conn = _db(tmp_path)
    _add(conn, caption='пальто шерстяное', topic='одежда')
    _add(conn, caption='пальто на витрине', topic='интерьер')
    rows = ml.search_items(conn, 'пальто', topic='одежда')
    assert len(rows) == 1
    assert rows[0]['topic'] == 'одежда'
    conn.close()


def test_search_brand_filter(tmp_path):
    conn = _db(tmp_path)
    _add(conn, name='куртка', brand='Adidas')
    _add(conn, name='куртка', brand='Puma')
    rows = ml.search_items(conn, 'куртка', brand='adidas')
    assert len(rows) == 1
    assert rows[0]['brand'] == 'Adidas'
    conn.close()


def test_search_color_filter_via_style_tags(tmp_path):
    conn = _db(tmp_path)
    _add(conn, caption='пальто', tags=['пальто', 'чёрный'])
    _add(conn, caption='пальто', tags=['пальто', 'белый'])
    rows = ml.search_items(conn, 'пальто', color='чёрный')
    assert len(rows) == 1
    conn.close()


def test_search_filters_only_no_query(tmp_path):
    conn = _db(tmp_path)
    _add(conn, name='шарф', topic='аксессуары')
    rows = ml.search_items(conn, None, topic='аксессуары')
    assert len(rows) == 1
    conn.close()


def test_search_newest_first(tmp_path):
    conn = _db(tmp_path)
    _add(conn, caption='пальто один')
    _add(conn, caption='пальто два')
    rows = ml.search_items(conn, 'пальто')
    assert rows[0]['id'] > rows[1]['id']
    conn.close()


# ---------------------------------------------------------------------------
# build_profile
# ---------------------------------------------------------------------------

def test_profile_empty_db(tmp_path):
    conn = _db(tmp_path)
    p = ml.build_profile(conn)
    assert p['count'] == 0
    assert p['liked'] == [] and p['brands'] == [] and p['examples'] == []
    conn.close()


def test_profile_aggregates_features_and_brands(tmp_path):
    conn = _db(tmp_path)
    _add(conn, liked=['минимализм'], tags=['пальто', 'чёрный', 'кашемир'],
         brand='Cos', topic='одежда')
    _add(conn, liked=['минимализм'], disliked=['яркое'], tags=['пальто', 'серый', 'шерсть'],
         brand='Cos', topic='одежда')
    p = ml.build_profile(conn, 'одежда')
    assert p['count'] == 2
    assert ('минимализм', 2) in p['liked']
    assert ('cos', 2) in p['brands']
    # colours / materials derived from style_tags vocabulary
    assert ('чёрный', 1) in p['colors']
    assert ('кашемир', 1) in p['materials'] or ('шерсть', 1) in p['materials']
    conn.close()


def test_profile_topic_filter_isolates(tmp_path):
    conn = _db(tmp_path)
    _add(conn, liked=['уют'], topic='мебель', brand='IKEA')
    _add(conn, liked=['стиль'], topic='одежда', brand='Zara')
    p = ml.build_profile(conn, 'мебель')
    assert p['count'] == 1
    assert ('уют', 1) in p['liked']
    assert ('ikea', 1) in p['brands']
    conn.close()


def test_profile_examples_limit(tmp_path):
    conn = _db(tmp_path)
    for i in range(8):
        _add(conn, caption=f'пример {i}', topic='одежда')
    p = ml.build_profile(conn, examples=5)
    assert len(p['examples']) == 5
    # newest first
    assert p['examples'][0]['caption'] == 'пример 7'
    conn.close()


# ---------------------------------------------------------------------------
# source_stats (ml_source_matcher tier × geo × CTR)
# ---------------------------------------------------------------------------

def test_source_stats_empty(tmp_path):
    import ml_source_matcher as sm
    conn = connect(tmp_path / 's.db')
    sm.ensure_schema(conn)
    assert sm.source_stats(conn) == []
    conn.close()


def test_source_stats_ctr(tmp_path):
    import ml_source_matcher as sm
    conn = connect(tmp_path / 's2.db')
    sm.ensure_schema(conn)
    sm.seed_sources(conn)
    # find a real source key to attach clicks to
    src = sm.list_sources(conn)[0]['key']
    geo = sm.list_sources(conn)[0]['geo']
    tier = sm.list_sources(conn)[0]['tier']
    conn.execute("INSERT INTO source_clicks (source_key, item_type, action) VALUES (?,?,?)",
                 (src, 'clothing', 'click'))
    conn.execute("INSERT INTO source_clicks (source_key, item_type, action) VALUES (?,?,?)",
                 (src, 'clothing', 'skip'))
    conn.commit()
    stats = sm.source_stats(conn, since_days=30)
    match = [s for s in stats if s['tier'] == tier and s['geo'] == geo]
    assert match
    assert match[0]['clicks'] == 1
    assert match[0]['skips'] == 1
    assert match[0]['total'] == 2
    assert match[0]['ctr'] == 0.5
    conn.close()
