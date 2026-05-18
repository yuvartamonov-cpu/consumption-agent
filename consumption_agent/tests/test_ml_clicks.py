"""Tests for ml_clicks — impression/click tracking + active learning signals."""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_clicks as mc


# ────────────────────────────────────────────────
# Schema
# ────────────────────────────────────────────────

def test_ensure_schema_creates_tables():
    conn = sqlite3.connect(':memory:')
    mc.ensure_clicks_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert 'ml_impressions' in tables
    assert 'ml_clicks' in tables
    conn.close()


def test_ensure_schema_idempotent():
    conn = sqlite3.connect(':memory:')
    mc.ensure_clicks_schema(conn)
    mc.ensure_clicks_schema(conn)  # second call must not raise
    mc.ensure_clicks_schema(conn)
    conn.close()


def test_ensure_schema_creates_indexes():
    conn = sqlite3.connect(':memory:')
    mc.ensure_clicks_schema(conn)
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    assert 'idx_imp_item' in idx
    assert 'idx_imp_source' in idx
    assert 'idx_clk_action' in idx
    conn.close()


# ────────────────────────────────────────────────
# log_impressions
# ────────────────────────────────────────────────

def test_log_impressions_inserts_one_row_per_group():
    conn = sqlite3.connect(':memory:')
    groups = [
        {'fingerprint': 'attr:nike|a1', 'store': 'Ozon', '_final_score': 0.8},
        {'fingerprint': 'attr:nike|a2', 'store': 'WB',   '_final_score': 0.7},
        {'fingerprint': 'attr:nike|a3', 'store': 'Lamoda', '_final_score': 0.6},
    ]
    n = mc.log_impressions(conn, item_id=42, ranked_groups=groups, category='обувь')
    assert n == 3
    rows = conn.execute("SELECT item_id, fingerprint, source, category, rank_position FROM ml_impressions ORDER BY rank_position").fetchall()
    assert len(rows) == 3
    assert rows[0] == (42, 'attr:nike|a1', 'Ozon', 'обувь', 1)
    assert rows[2] == (42, 'attr:nike|a3', 'Lamoda', 'обувь', 3)
    conn.close()


def test_log_impressions_empty_groups_noop():
    conn = sqlite3.connect(':memory:')
    assert mc.log_impressions(conn, item_id=1, ranked_groups=[]) == 0
    conn.close()


def test_log_impressions_skips_rows_without_fingerprint():
    conn = sqlite3.connect(':memory:')
    groups = [
        {'fingerprint': '', 'store': 'X'},
        {'fingerprint': 'attr:y', 'store': 'Y'},
        {'store': 'Z'},  # missing fingerprint key
    ]
    assert mc.log_impressions(conn, item_id=1, ranked_groups=groups) == 1
    conn.close()


def test_log_impressions_persists_score():
    conn = sqlite3.connect(':memory:')
    mc.log_impressions(conn, item_id=7, ranked_groups=[
        {'fingerprint': 'fp1', 'store': 'X', '_final_score': 0.85}
    ])
    score = conn.execute("SELECT final_score FROM ml_impressions").fetchone()[0]
    assert abs(score - 0.85) < 1e-6
    conn.close()


# ────────────────────────────────────────────────
# log_click
# ────────────────────────────────────────────────

def test_log_click_inserts_row():
    conn = sqlite3.connect(':memory:')
    rid = mc.log_click(
        conn, item_id=5, fingerprint='fp', source='Ozon',
        category='обувь', action=mc.ACTION_OPEN, rank_position=2,
    )
    assert rid > 0
    row = conn.execute(
        "SELECT item_id, fingerprint, source, category, action, rank_position "
        "FROM ml_clicks"
    ).fetchone()
    assert row == (5, 'fp', 'Ozon', 'обужь'.replace('ж','в'), 'open_listing', 2)
    conn.close()


def test_log_click_minimum_fields():
    conn = sqlite3.connect(':memory:')
    rid = mc.log_click(conn, item_id=1, action=mc.ACTION_DISMISS)
    assert rid > 0
    row = conn.execute("SELECT action FROM ml_clicks").fetchone()
    assert row[0] == 'dismiss'
    conn.close()


# ────────────────────────────────────────────────
# ctr_per_source
# ────────────────────────────────────────────────

def test_ctr_zero_when_no_clicks():
    conn = sqlite3.connect(':memory:')
    mc.log_impressions(conn, item_id=1, ranked_groups=[
        {'fingerprint': 'fp1', 'store': 'Ozon'},
        {'fingerprint': 'fp2', 'store': 'Ozon'},
    ])
    stats = mc.ctr_per_source(conn)
    assert stats['Ozon']['impressions'] == 2
    assert stats['Ozon']['clicks'] == 0
    assert stats['Ozon']['ctr'] == 0.0
    conn.close()


def test_ctr_computed_correctly():
    conn = sqlite3.connect(':memory:')
    # 4 impressions on Ozon, 1 on Lamoda
    mc.log_impressions(conn, item_id=1, ranked_groups=[
        {'fingerprint': f'fp{i}', 'store': 'Ozon'} for i in range(4)
    ])
    mc.log_impressions(conn, item_id=2, ranked_groups=[
        {'fingerprint': 'fpL', 'store': 'Lamoda'}
    ])
    # 1 click on Ozon, 1 on Lamoda
    mc.log_click(conn, item_id=1, fingerprint='fp0', source='Ozon',
                 action=mc.ACTION_OPEN)
    mc.log_click(conn, item_id=2, fingerprint='fpL', source='Lamoda',
                 action=mc.ACTION_OPEN)
    stats = mc.ctr_per_source(conn)
    assert stats['Ozon']['ctr'] == 0.25       # 1/4
    assert stats['Lamoda']['ctr'] == 1.0      # 1/1
    conn.close()


def test_ctr_negative_actions_do_not_count():
    conn = sqlite3.connect(':memory:')
    mc.log_impressions(conn, item_id=1, ranked_groups=[
        {'fingerprint': 'fp', 'store': 'Ozon'}
    ])
    mc.log_click(conn, item_id=1, source='Ozon', action=mc.ACTION_DISMISS)
    stats = mc.ctr_per_source(conn)
    assert stats['Ozon']['clicks'] == 0
    conn.close()


def test_ctr_category_filter():
    conn = sqlite3.connect(':memory:')
    mc.log_impressions(conn, item_id=1, ranked_groups=[
        {'fingerprint': 'a', 'store': 'Ozon'}], category='обувь')
    mc.log_impressions(conn, item_id=2, ranked_groups=[
        {'fingerprint': 'b', 'store': 'Ozon'}], category='техника')
    mc.log_click(conn, item_id=1, source='Ozon', category='обувь',
                 action=mc.ACTION_OPEN)
    s = mc.ctr_per_source(conn, category='обувь')
    assert s['Ozon']['ctr'] == 1.0
    s2 = mc.ctr_per_source(conn, category='техника')
    assert s2['Ozon']['ctr'] == 0.0
    conn.close()


def test_ctr_since_days_filter():
    conn = sqlite3.connect(':memory:')
    # Old impression (manually backdated)
    mc.ensure_clicks_schema(conn)
    conn.execute(
        "INSERT INTO ml_impressions (item_id, fingerprint, source, rank_position, ts) "
        "VALUES (1, 'fp_old', 'Ozon', 1, datetime('now', '-60 days'))"
    )
    # Recent impression
    mc.log_impressions(conn, item_id=2, ranked_groups=[
        {'fingerprint': 'fp_new', 'store': 'Ozon'}])
    s = mc.ctr_per_source(conn, since_days=30)
    assert s['Ozon']['impressions'] == 1  # only recent counts
    conn.close()


# ────────────────────────────────────────────────
# Helpers for Stage 6 / 8
# ────────────────────────────────────────────────

def test_bandit_outcomes_yields_success_events():
    conn = sqlite3.connect(':memory:')
    mc.log_click(conn, item_id=1, source='Ozon', category='обувь',
                 action=mc.ACTION_OPEN)
    mc.log_click(conn, item_id=1, source='WB', category='обувь',
                 action=mc.ACTION_REMIND)
    mc.log_click(conn, item_id=1, source='Avito', category='обувь',
                 action=mc.ACTION_DISMISS)  # not a success
    events = list(mc.bandit_outcomes_since(conn, since_days=30))
    sources = {e['source'] for e in events}
    assert sources == {'Ozon', 'WB'}
    assert all(e['success'] for e in events)
    conn.close()


def test_bandit_outcomes_drops_null_source():
    conn = sqlite3.connect(':memory:')
    mc.log_click(conn, item_id=1, source=None, action=mc.ACTION_OPEN)
    events = list(mc.bandit_outcomes_since(conn, since_days=30))
    assert events == []
    conn.close()


def test_positive_fingerprints_returns_opened_groups():
    conn = sqlite3.connect(':memory:')
    mc.log_click(conn, item_id=1, fingerprint='attr:nike|a1',
                 source='Lamoda', category='обувь',
                 action=mc.ACTION_OPEN)
    mc.log_click(conn, item_id=2, fingerprint='attr:nike|a2',
                 action=mc.ACTION_LIKE)
    mc.log_click(conn, item_id=3, fingerprint='attr:bad',
                 action=mc.ACTION_DISMISS)
    hits = mc.positive_fingerprints(conn)
    fps = {h['fingerprint'] for h in hits}
    assert fps == {'attr:nike|a1', 'attr:nike|a2'}
    conn.close()


def test_dismissed_fingerprints_collects_negatives():
    conn = sqlite3.connect(':memory:')
    mc.log_click(conn, item_id=1, fingerprint='fp_ok',
                 action=mc.ACTION_OPEN)
    mc.log_click(conn, item_id=2, fingerprint='fp_bad',
                 action=mc.ACTION_DISMISS)
    mc.log_click(conn, item_id=3, fingerprint='fp_no',
                 action=mc.ACTION_DISLIKE)
    hits = mc.dismissed_fingerprints(conn)
    fps = {h['fingerprint'] for h in hits}
    assert fps == {'fp_bad', 'fp_no'}
    conn.close()


def test_positive_fingerprints_excludes_old_events():
    conn = sqlite3.connect(':memory:')
    mc.ensure_clicks_schema(conn)
    conn.execute(
        "INSERT INTO ml_clicks (item_id, fingerprint, action, ts) "
        "VALUES (1, 'old', 'open_listing', datetime('now', '-365 days'))"
    )
    mc.log_click(conn, item_id=2, fingerprint='new', action=mc.ACTION_OPEN)
    hits = mc.positive_fingerprints(conn, since_days=180)
    fps = {h['fingerprint'] for h in hits}
    assert fps == {'new'}
    conn.close()


# ────────────────────────────────────────────────
# recent_events
# ────────────────────────────────────────────────

def test_recent_events_returns_mixed_kinds():
    conn = sqlite3.connect(':memory:')
    mc.log_impressions(conn, item_id=1, ranked_groups=[
        {'fingerprint': 'fp', 'store': 'Ozon'}])
    time.sleep(0.01)
    mc.log_click(conn, item_id=1, fingerprint='fp', source='Ozon',
                 action=mc.ACTION_OPEN)
    events = mc.recent_events(conn, limit=5)
    kinds = {e['kind'] for e in events}
    assert kinds == {'impression', 'click'}
    conn.close()


def test_recent_events_orders_by_ts_desc():
    conn = sqlite3.connect(':memory:')
    mc.ensure_clicks_schema(conn)
    # Insert with explicit ts so order is predictable
    conn.execute(
        "INSERT INTO ml_clicks (item_id, action, ts) VALUES (1, 'open_listing', '2025-01-01')"
    )
    conn.execute(
        "INSERT INTO ml_clicks (item_id, action, ts) VALUES (2, 'dismiss', '2025-12-31')"
    )
    events = mc.recent_events(conn, limit=10)
    assert events[0]['item_id'] == 2  # newest first
    conn.close()
