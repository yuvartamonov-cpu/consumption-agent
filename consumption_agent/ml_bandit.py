"""
ml_bandit.py — Thompson-sampling source allocator.

Stage 6/§9 of visual-product-search skill. Learns which marketplaces
actually deliver results the user opens, per category. Replaces the
static CATEGORY_SOURCES table in ml_search_v2 with adaptive sampling.

Model:
    For each (category, source) pair, maintain a Beta(α, β) posterior
    over its "useful hit" probability. Start uniform Beta(1, 1).
    On a positive interaction (open_listing / set_reminder / like) →
    α += 1. On an impression without follow-up positive within 7 days →
    β += 1.

    At search time, sample p ∼ Beta(α, β) for each source and pick the
    top-K by sample. This balances exploitation (popular sources
    selected often) with exploration (low-confidence sources still get
    occasional trials).

Surface:
    ensure_bandit_schema(conn) — create bandit_stats table
    update_from_clicks(conn, lookback_days=30) — refresh stats from
        ml_clicks/ml_impressions (call from cron or before each search)
    sample_sources(conn, category, candidates, k=5) — return k best
        sources for this category right now
    snapshot(conn, category) — debug view {source: (alpha, beta, p_mean)}

The reset_every_days knob avoids alpha+beta growing unbounded — past
some volume the bandit becomes deaf to new evidence. Default 90 days:
keep last 90 days' interactions, decay older ones by 0.5.
"""
from __future__ import annotations

import logging
import math
import random
import sqlite3
from typing import Iterable, Optional, Sequence

import ml_clicks

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_bandit_schema(conn: sqlite3.Connection) -> None:
    """Create bandit_stats table. Idempotent."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bandit_stats (
            category TEXT NOT NULL,
            source   TEXT NOT NULL,
            alpha    REAL NOT NULL DEFAULT 1.0,
            beta     REAL NOT NULL DEFAULT 1.0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (category, source)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bandit_cat ON bandit_stats(category)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Update from real click data
# ---------------------------------------------------------------------------
def _adjust(conn: sqlite3.Connection, category: str, source: str,
            d_alpha: float, d_beta: float) -> None:
    conn.execute(
        """
        INSERT INTO bandit_stats (category, source, alpha, beta, updated_at)
        VALUES (?, ?, 1.0 + ?, 1.0 + ?, datetime('now'))
        ON CONFLICT(category, source) DO UPDATE SET
            alpha = alpha + excluded.alpha - 1.0,
            beta  = beta  + excluded.beta  - 1.0,
            updated_at = datetime('now')
        """,
        (category or '', source, d_alpha, d_beta),
    )


def update_from_clicks(
    conn: sqlite3.Connection,
    *,
    lookback_days: int = 30,
    decay_factor: Optional[float] = 0.5,
    decay_after_days: int = 90,
) -> dict:
    """Rebuild (or incrementally update) bandit posteriors from clicks.

    Strategy:
      1. Optionally apply temporal decay: when any row's updated_at is
         older than decay_after_days, multiply (alpha-1)/(beta-1) by
         decay_factor. This prevents unbounded saturation.
      2. For every positive click in the last lookback_days, +1 alpha.
      3. For every impression in the same window without a matching
         positive click on (item_id, source) within +7 days, +1 beta.

    Returns counters {alpha_added, beta_added, decayed}.
    """
    ensure_bandit_schema(conn)
    ml_clicks.ensure_clicks_schema(conn)

    counters = {'alpha_added': 0, 'beta_added': 0, 'decayed': 0}

    # 1. Decay stale rows
    if decay_factor is not None and 0.0 < decay_factor < 1.0:
        cur = conn.execute(
            "SELECT category, source, alpha, beta FROM bandit_stats "
            "WHERE updated_at < datetime('now', ?)",
            (f'-{decay_after_days} days',)
        )
        for cat, src, a, b in cur.fetchall():
            new_a = 1.0 + (a - 1.0) * decay_factor
            new_b = 1.0 + (b - 1.0) * decay_factor
            conn.execute(
                "UPDATE bandit_stats SET alpha=?, beta=?, updated_at=datetime('now') "
                "WHERE category=? AND source=?",
                (new_a, new_b, cat, src),
            )
            counters['decayed'] += 1

    # 2. Positive clicks → +α
    placeholders = ','.join('?' * len(ml_clicks.POSITIVE_ACTIONS))
    rows = conn.execute(
        f"SELECT category, source FROM ml_clicks "
        f"WHERE action IN ({placeholders}) "
        f"  AND ts >= datetime('now', ?) "
        f"  AND source IS NOT NULL",
        (*ml_clicks.POSITIVE_ACTIONS, f'-{lookback_days} days')
    ).fetchall()
    for cat, src in rows:
        _adjust(conn, cat or '', src, 1.0, 0.0)
        counters['alpha_added'] += 1

    # 3. Impressions without follow-up positive within +7 days → +β
    # Implemented as left-join: impression rows where no positive click
    # exists on (item_id, source) within 7 days after impression.
    placeholders = ','.join('?' * len(ml_clicks.POSITIVE_ACTIONS))
    rows = conn.execute(
        f"""
        SELECT i.category, i.source FROM ml_impressions i
        WHERE i.ts >= datetime('now', ?)
          AND i.source IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM ml_clicks c
            WHERE c.item_id = i.item_id
              AND c.source = i.source
              AND c.action IN ({placeholders})
              AND c.ts BETWEEN i.ts AND datetime(i.ts, '+7 days')
          )
        """,
        (f'-{lookback_days} days', *ml_clicks.POSITIVE_ACTIONS)
    ).fetchall()
    for cat, src in rows:
        _adjust(conn, cat or '', src, 0.0, 1.0)
        counters['beta_added'] += 1

    conn.commit()
    return counters


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
def _row_for(conn: sqlite3.Connection, category: str, source: str) -> tuple[float, float]:
    """Get (alpha, beta) for one cell; defaults Beta(1,1) when absent."""
    row = conn.execute(
        "SELECT alpha, beta FROM bandit_stats WHERE category=? AND source=?",
        (category or '', source),
    ).fetchone()
    if row:
        return float(row[0]), float(row[1])
    return 1.0, 1.0


def sample_sources(
    conn: sqlite3.Connection,
    category: Optional[str],
    candidates: Sequence[str],
    *,
    k: int = 5,
    rng: Optional[random.Random] = None,
) -> list[str]:
    """Pick top-k sources for this category via Thompson sampling.

    `candidates` is the menu of allowed sources (e.g. from
    ml_search_v2.CATEGORY_SOURCES). The bandit reorders within that
    menu — it doesn't invent new sources.

    The order is randomised on every call so cold-start sources still
    get periodic exposure (Beta(1,1) sampling has high variance).
    """
    if not candidates:
        return []
    ensure_bandit_schema(conn)
    r = rng or random

    cat = (category or '').lower()
    samples = []
    for src in candidates:
        a, b = _row_for(conn, cat, src)
        # Beta(a, b) sampling via gamma method
        x = r.gammavariate(a, 1.0)
        y = r.gammavariate(b, 1.0)
        p = x / (x + y) if (x + y) > 0 else 0.5
        samples.append((src, p, a, b))
    samples.sort(key=lambda t: t[1], reverse=True)
    return [s for s, _, _, _ in samples[:k]]


def snapshot(
    conn: sqlite3.Connection,
    category: Optional[str] = None,
) -> list[dict]:
    """Inspectable state for /ml_stats: per-source posterior means.

    p_mean = α / (α + β) is the bandit's current belief about the
    "useful hit" probability for this source. Sorted desc by p_mean.
    """
    ensure_bandit_schema(conn)
    if category is not None:
        rows = conn.execute(
            "SELECT category, source, alpha, beta, updated_at "
            "FROM bandit_stats WHERE category=?",
            (category.lower(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT category, source, alpha, beta, updated_at FROM bandit_stats"
        ).fetchall()
    out = []
    for cat, src, a, b, ts in rows:
        total = a + b
        p_mean = (a / total) if total > 0 else 0.5
        # Variance of Beta(a,b)
        var = (a * b) / (total ** 2 * (total + 1)) if total > 1 else 0.25
        out.append({
            'category': cat, 'source': src,
            'alpha': round(a, 3), 'beta': round(b, 3),
            'p_mean': round(p_mean, 4),
            'p_std': round(math.sqrt(var), 4),
            'samples': round(total - 2, 1),  # observed evidence
            'updated_at': ts,
        })
    out.sort(key=lambda r: r['p_mean'], reverse=True)
    return out
