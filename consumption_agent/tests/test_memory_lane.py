"""Tests for memory_lane.py — Phase B fast path."""
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory_lane


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    memory_lane.ensure_memory_lane_schema(c)
    return c


# ---------------------------------------------------------------------------
# parse_caption
# ---------------------------------------------------------------------------
def test_parse_caption_liked_keywords():
    p = memory_lane.parse_caption('нравится это пальто, хочу такое же')
    assert 'нравится' in p['liked']
    assert 'хочу' in p['liked']
    assert p['disliked'] == []


def test_parse_caption_disliked_keywords():
    p = memory_lane.parse_caption('не нравится, фу')
    assert 'не нравится' in p['disliked']
    # 'не нравится' must NOT have also matched 'нравится'
    assert p['liked'] == [] or 'нравится' not in p['liked']


def test_parse_caption_hashtags_to_style():
    p = memory_lane.parse_caption('красиво #серое #пальто #zara')
    assert set(p['style_tags']) == {'серое', 'пальто', 'zara'}


def test_parse_caption_topic_detection():
    assert memory_lane.parse_caption('новый диван').get('topic') == 'мебель'
    assert memory_lane.parse_caption('крутое пальто').get('topic') == 'одежда'
    assert memory_lane.parse_caption('хочу новый ноутбук').get('topic') == 'техника'
    assert memory_lane.parse_caption('просто текст без ключей').get('topic') is None


def test_is_memory_lane_caption_positive_negative():
    assert memory_lane.is_memory_lane_caption('нравится')
    assert memory_lane.is_memory_lane_caption('красиво #пальто')
    assert memory_lane.is_memory_lane_caption('запомни это')
    assert not memory_lane.is_memory_lane_caption('просто фото без триггера')
    assert not memory_lane.is_memory_lane_caption(None)
    assert not memory_lane.is_memory_lane_caption('')


# ---------------------------------------------------------------------------
# save_media — sha256 dedupe
# ---------------------------------------------------------------------------
def test_save_media_dedupes_by_sha256(tmp_path):
    conn = _conn()
    data = b'\xff\xd8\xff\xe0fake-jpeg-bytes-here'
    a = memory_lane.save_media(conn, data, mime='image/jpeg', base_dir=str(tmp_path))
    b = memory_lane.save_media(conn, data, mime='image/jpeg', base_dir=str(tmp_path))
    assert a == b
    rows = conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0]
    assert rows == 1
    files = list(tmp_path.iterdir())
    assert len(files) == 1


# ---------------------------------------------------------------------------
# save_memory_lane + list_recent
# ---------------------------------------------------------------------------
def test_save_memory_lane_persists_json_and_returns_id(tmp_path):
    conn = _conn()
    data = b'image-bytes-1'
    asset = memory_lane.save_media(conn, data, base_dir=str(tmp_path))
    item_id = memory_lane.save_memory_lane(
        conn, 'нравится #пальто #серое', asset
    )
    assert item_id == 1
    row = conn.execute(
        "SELECT caption, liked_features, style_tags, topic, media_asset_id "
        "FROM memory_lane_items WHERE id=?",
        (item_id,),
    ).fetchone()
    assert row['caption'] == 'нравится #пальто #серое'
    assert json.loads(row['liked_features']) == ['нравится']
    assert set(json.loads(row['style_tags'])) == {'пальто', 'серое'}
    assert row['topic'] == 'одежда'
    assert row['media_asset_id'] == asset


def test_list_recent_newest_first_and_topic_filter(tmp_path):
    conn = _conn()
    a = memory_lane.save_media(conn, b'a', base_dir=str(tmp_path / 'a'))
    b = memory_lane.save_media(conn, b'b', base_dir=str(tmp_path / 'b'))
    c = memory_lane.save_media(conn, b'c', base_dir=str(tmp_path / 'c'))
    memory_lane.save_memory_lane(conn, 'нравится диван', a)         # мебель
    memory_lane.save_memory_lane(conn, 'нравится пальто', b)        # одежда
    memory_lane.save_memory_lane(conn, 'нравится комод', c)         # мебель

    all_rows = memory_lane.list_recent(conn, n=10)
    assert [r['id'] for r in all_rows] == [3, 2, 1]

    only_furn = memory_lane.list_recent(conn, n=10, topic='мебель')
    assert [r['id'] for r in only_furn] == [3, 1]


# ---------------------------------------------------------------------------
# Schema idempotency
# ---------------------------------------------------------------------------
def test_ensure_memory_lane_schema_idempotent():
    conn = sqlite3.connect(":memory:")
    memory_lane.ensure_memory_lane_schema(conn)
    memory_lane.ensure_memory_lane_schema(conn)  # second call must not raise
    cols_ml = {r[1] for r in conn.execute("PRAGMA table_info(memory_lane_items)").fetchall()}
    cols_ma = {r[1] for r in conn.execute("PRAGMA table_info(media_assets)").fetchall()}
    assert {'caption', 'liked_features', 'disliked_features', 'style_tags',
            'topic', 'media_asset_id'} <= cols_ml
    assert {'file_path', 'sha256', 'mime', 'size_bytes'} <= cols_ma
