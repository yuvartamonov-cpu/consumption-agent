"""Tests for ml_bandit — Thompson-sampling source allocator."""
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_bandit as mb
import ml_clicks


# ────────────────────────────────────────────────
# Schema
# ────────────────────────────────────────────────

def test_ensure_bandit_schema_idempotent():
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    mb.ensure_bandit_schema(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert 'bandit_stats' in tables
    conn.close()


# ────────────────────────────────────────────────
# sample_sources — cold-start behaviour
# ────────────────────────────────────────────────

def test_sample_cold_start_returns_all_when_k_large():
    conn = sqlite3.connect(':memory:')
    out = mb.sample_sources(conn, 'обувь', ['Ozon', 'WB', 'Lamoda'], k=10)
    assert set(out) == {'Ozon', 'WB', 'Lamoda'}
    conn.close()


def test_sample_caps_at_k():
    conn = sqlite3.connect(':memory:')
    out = mb.sample_sources(conn, 'обувь', ['A', 'B', 'C', 'D', 'E'], k=3)
    assert len(out) == 3
    conn.close()


def test_sample_returns_empty_for_empty_candidates():
    conn = sqlite3.connect(':memory:')
    assert mb.sample_sources(conn, 'обувь', []) == []
    conn.close()


def test_sample_is_randomised_at_cold_start():
    """Beta(1,1) sampling has high variance — repeated calls should
    produce different orders at least once across many tries."""
    conn = sqlite3.connect(':memory:')
    orders = set()
    rng = random.Random(0)
    for _ in range(50):
        out = mb.sample_sources(conn, 'обувь', ['A', 'B', 'C', 'D'], k=4, rng=rng)
        orders.add(tuple(out))
    assert len(orders) > 1
    conn.close()


# ────────────────────────────────────────────────
# sample_sources — converged behaviour
# ────────────────────────────────────────────────

def test_sample_prefers_higher_alpha_source():
    """When one source has much higher α, it should dominate."""
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    # Lamoda: 100 successes, 5 failures → p_mean ≈ 0.94
    conn.execute(
        "INSERT INTO bandit_stats (category, source, alpha, beta) "
        "VALUES ('обувь', 'Lamoda', 101.0, 6.0)"
    )
    # Ozon: 5 successes, 100 failures → p_mean ≈ 0.05
    conn.execute(
        "INSERT INTO bandit_stats (category, source, alpha, beta) "
        "VALUES ('обувь', 'Ozon', 6.0, 101.0)"
    )
    conn.commit()
    rng = random.Random(42)
    lamoda_first = 0
    for _ in range(100):
        out = mb.sample_sources(conn, 'обувь', ['Ozon', 'Lamoda'], k=1, rng=rng)
        if out[0] == 'Lamoda':
            lamoda_first += 1
    # With these priors Lamoda should win >85% of trials
    assert lamoda_first > 85
    conn.close()


def test_sample_falls_back_to_uniform_for_unseen_category():
    """A category never updated should behave like cold start across all
    sources, regardless of stats in another category."""
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    # Strong prior for 'обувь', Ozon
    conn.execute(
        "INSERT INTO bandit_stats (category, source, alpha, beta) "
        "VALUES ('обувь', 'Ozon', 50.0, 1.0)"
    )
    conn.commit()
    # Query for different category — should not be biased toward Ozon
    rng = random.Random(7)
    counts = {'Ozon': 0, 'WB': 0}
    for _ in range(60):
        out = mb.sample_sources(conn, 'техника', ['Ozon', 'WB'], k=1, rng=rng)
        counts[out[0]] += 1
    # Neither should hugely dominate (uniform → roughly 50/50)
    assert 15 <= counts['Ozon'] <= 45
    conn.close()


# ────────────────────────────────────────────────
# update_from_clicks — empty / no-op cases
# ────────────────────────────────────────────────

def test_update_from_clicks_on_empty_db():
    conn = sqlite3.connect(':memory:')
    counters = mb.update_from_clicks(conn)
    assert counters == {'alpha_added': 0, 'beta_added': 0, 'decayed': 0}
    conn.close()


# ────────────────────────────────────────────────
# update_from_clicks — alpha updates from positive actions
# ────────────────────────────────────────────────

def test_positive_click_adds_alpha():
    conn = sqlite3.connect(':memory:')
    ml_clicks.log_click(conn, item_id=1, source='Lamoda', category='обувь',
                        action=ml_clicks.ACTION_OPEN)
    counters = mb.update_from_clicks(conn)
    assert counters['alpha_added'] == 1
    snap = mb.snapshot(conn, 'обувь')
    lamoda = next(r for r in snap if r['source'] == 'Lamoda')
    assert lamoda['alpha'] > 1.0
    assert lamoda['beta'] == 1.0
    conn.close()


def test_multiple_clicks_accumulate():
    conn = sqlite3.connect(':memory:')
    for _ in range(5):
        ml_clicks.log_click(conn, item_id=1, source='Lamoda',
                            category='обувь', action=ml_clicks.ACTION_OPEN)
    mb.update_from_clicks(conn)
    snap = mb.snapshot(conn, 'обувь')
    lamoda = next(r for r in snap if r['source'] == 'Lamoda')
    assert lamoda['alpha'] == 6.0   # 1 base + 5 successes
    conn.close()


def test_negative_actions_do_not_add_alpha():
    conn = sqlite3.connect(':memory:')
    ml_clicks.log_click(conn, item_id=1, source='Ozon',
                        category='обувь', action=ml_clicks.ACTION_DISMISS)
    counters = mb.update_from_clicks(conn)
    assert counters['alpha_added'] == 0
    conn.close()


# ────────────────────────────────────────────────
# update_from_clicks — beta updates from unconverted impressions
# ────────────────────────────────────────────────

def test_impression_without_click_adds_beta():
    conn = sqlite3.connect(':memory:')
    ml_clicks.log_impressions(conn, item_id=1, ranked_groups=[
        {'fingerprint': 'fp1', 'store': 'Ozon'},
        {'fingerprint': 'fp2', 'store': 'WB'},
    ], category='обувь')
    counters = mb.update_from_clicks(conn)
    assert counters['beta_added'] == 2
    snap = mb.snapshot(conn, 'обувь')
    by_src = {r['source']: r for r in snap}
    assert by_src['Ozon']['beta'] == 2.0
    assert by_src['WB']['beta'] == 2.0
    conn.close()


def test_impression_with_followup_click_does_not_add_beta():
    """If a positive click on (item, source) happens after impression,
    it shouldn't count as a failure."""
    conn = sqlite3.connect(':memory:')
    ml_clicks.log_impressions(conn, item_id=5, ranked_groups=[
        {'fingerprint': 'fp1', 'store': 'Ozon'},
    ], category='обувь')
    ml_clicks.log_click(conn, item_id=5, fingerprint='fp1', source='Ozon',
                        category='обувь', action=ml_clicks.ACTION_OPEN)
    counters = mb.update_from_clicks(conn)
    assert counters['alpha_added'] == 1
    assert counters['beta_added'] == 0
    conn.close()


# ────────────────────────────────────────────────
# Decay
# ────────────────────────────────────────────────

def test_old_stats_decay_when_stale():
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    conn.execute(
        "INSERT INTO bandit_stats (category, source, alpha, beta, updated_at) "
        "VALUES ('обувь', 'Ozon', 101.0, 21.0, datetime('now', '-200 days'))"
    )
    conn.commit()
    counters = mb.update_from_clicks(conn, decay_factor=0.5, decay_after_days=90)
    assert counters['decayed'] == 1
    snap = mb.snapshot(conn, 'обувь')
    ozon = next(r for r in snap if r['source'] == 'Ozon')
    # 1 + (101-1)*0.5 = 51
    assert ozon['alpha'] == 51.0
    assert ozon['beta'] == 11.0    # 1 + (21-1)*0.5
    conn.close()


def test_decay_disabled_when_factor_none():
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    conn.execute(
        "INSERT INTO bandit_stats (category, source, alpha, beta, updated_at) "
        "VALUES ('обувь', 'Ozon', 50.0, 10.0, datetime('now', '-365 days'))"
    )
    conn.commit()
    mb.update_from_clicks(conn, decay_factor=None)
    snap = mb.snapshot(conn, 'обувь')
    ozon = next(r for r in snap if r['source'] == 'Ozon')
    assert ozon['alpha'] == 50.0
    assert ozon['beta'] == 10.0
    conn.close()


# ────────────────────────────────────────────────
# snapshot
# ────────────────────────────────────────────────

def test_snapshot_sorted_by_p_mean_desc():
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    conn.executemany(
        "INSERT INTO bandit_stats (category, source, alpha, beta) VALUES (?,?,?,?)",
        [('обувь', 'A', 50.0, 5.0),     # p_mean ≈ 0.91
         ('обувь', 'B', 10.0, 10.0),    # p_mean = 0.50
         ('обувь', 'C',  2.0, 20.0)],   # p_mean ≈ 0.09
    )
    conn.commit()
    snap = mb.snapshot(conn, 'обувь')
    assert [r['source'] for r in snap] == ['A', 'B', 'C']
    conn.close()


def test_snapshot_filters_by_category():
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    conn.executemany(
        "INSERT INTO bandit_stats (category, source, alpha, beta) VALUES (?,?,?,?)",
        [('обувь', 'A', 10.0, 1.0),
         ('техника', 'B', 10.0, 1.0)]
    )
    conn.commit()
    s1 = mb.snapshot(conn, 'обувь')
    assert len(s1) == 1
    assert s1[0]['source'] == 'A'
    conn.close()


def test_snapshot_computes_p_mean_and_std():
    conn = sqlite3.connect(':memory:')
    mb.ensure_bandit_schema(conn)
    conn.execute(
        "INSERT INTO bandit_stats (category, source, alpha, beta) "
        "VALUES ('обувь', 'X', 10.0, 10.0)"
    )
    conn.commit()
    snap = mb.snapshot(conn, 'обувь')
    r = snap[0]
    assert abs(r['p_mean'] - 0.5) < 1e-6
    assert r['p_std'] > 0


# ────────────────────────────────────────────────
# End-to-end smoke test
# ────────────────────────────────────────────────

def test_smoke_full_loop():
    """Realistic scenario: user opens 5 results on Lamoda, ignores 50
    on Ozon → Lamoda gets sampled more often."""
    conn = sqlite3.connect(':memory:')
    # Pretend lots of impressions on both
    for i in range(50):
        ml_clicks.log_impressions(conn, item_id=i, ranked_groups=[
            {'fingerprint': f'fp_o_{i}', 'store': 'Ozon'},
            {'fingerprint': f'fp_l_{i}', 'store': 'Lamoda'},
        ], category='обувь')
    # User opens 5 on Lamoda
    for i in range(5):
        ml_clicks.log_click(conn, item_id=i, fingerprint=f'fp_l_{i}',
                            source='Lamoda', category='обувь',
                            action=ml_clicks.ACTION_OPEN)

    counters = mb.update_from_clicks(conn)
    assert counters['alpha_added'] == 5
    # 50 impressions on Ozon, 0 clicks → 50 beta
    # 50 impressions on Lamoda, 5 clicks → 45 beta
    assert counters['beta_added'] == 50 + 45

    rng = random.Random(123)
    lamoda_wins = sum(
        1 for _ in range(200)
        if mb.sample_sources(conn, 'обувь', ['Ozon', 'Lamoda'], k=1, rng=rng)[0] == 'Lamoda'
    )
    assert lamoda_wins > 130     # clear preference for Lamoda
    conn.close()
