"""
ml_clicks.py — Click tracking + active learning signals.

Stage 9/§13 of visual-product-search skill. Logs what the user saw
(impressions/views) and what they did with it (clicks, reminders,
dismissals), giving downstream stages real-world signal to learn from:

    • Stage 6 (Bandit allocator) → success/failure counts per
      (category, source) updated from click events.
    • Stage 8 (Taste re-ranker) → token weights nudged toward what the
      user actually opened, not just what they passively liked.
    • A/B testing for CLIP threshold and weight tuning.

The two key relations:

    ml_impressions:  item_id, fingerprint, source, rank_position, ts
        — what was shown, one row per displayed canonical group.
    ml_clicks:       item_id, fingerprint, source, action,
                     rank_position, ts
        — what the user did, joined back to impressions by item_id
        + fingerprint + ts proximity.

Both tables stay small (one row per shown group, plus a click row only
when the user takes action). At 10 searches/day × 5 groups = 50
impressions/day, the tables fit comfortably forever.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Iterable, Optional

log = logging.getLogger(__name__)

# Action vocabulary — what we record when a user interacts with a result.
ACTION_OPEN = 'open_listing'      # clicked the marketplace link
ACTION_REMIND = 'set_reminder'    # set price-watch reminder
ACTION_DISMISS = 'dismiss'        # explicitly hid the result
ACTION_LIKE = 'feedback_like'     # thumbs-up
ACTION_DISLIKE = 'feedback_dislike'

# 'success' actions for bandit / taste-learning purposes
POSITIVE_ACTIONS = frozenset({ACTION_OPEN, ACTION_REMIND, ACTION_LIKE})
NEGATIVE_ACTIONS = frozenset({ACTION_DISMISS, ACTION_DISLIKE})


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_clicks_schema(conn: sqlite3.Connection) -> None:
    """Create ml_impressions and ml_clicks tables. Idempotent."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_impressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            fingerprint TEXT NOT NULL,
            source TEXT,
            category TEXT,
            rank_position INTEGER NOT NULL,
            final_score REAL,
            ts TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            fingerprint TEXT,
            source TEXT,
            category TEXT,
            action TEXT NOT NULL,
            rank_position INTEGER,
            ts TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imp_item   ON ml_impressions(item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imp_source ON ml_impressions(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imp_ts     ON ml_impressions(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clk_item   ON ml_clicks(item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clk_source ON ml_clicks(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clk_action ON ml_clicks(action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_clk_ts     ON ml_clicks(ts)")
    conn.commit()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log_impressions(
    conn: sqlite3.Connection,
    item_id: int,
    ranked_groups: list[dict],
    *,
    category: Optional[str] = None,
) -> int:
    """Record one impression row per displayed group. Returns rows written.

    Called from the /ml_search handler right after canonicalize+rank but
    before sending the message to the user. Failures are logged, not raised.
    """
    if not ranked_groups:
        return 0
    ensure_clicks_schema(conn)

    rows = []
    for idx, g in enumerate(ranked_groups, start=1):
        fp = g.get('fingerprint') or ''
        if not fp:
            continue
        rows.append((
            item_id,
            fp,
            g.get('store'),
            category,
            idx,
            g.get('_final_score'),
        ))
    if not rows:
        return 0
    try:
        conn.executemany(
            """
            INSERT INTO ml_impressions
                (item_id, fingerprint, source, category, rank_position, final_score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        log.warning("ml_clicks: log_impressions failed: %s", e)
        return 0
    return len(rows)


def log_click(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    fingerprint: Optional[str] = None,
    source: Optional[str] = None,
    category: Optional[str] = None,
    action: str = ACTION_OPEN,
    rank_position: Optional[int] = None,
) -> int:
    """Record a single user action. Returns inserted row id or 0 on failure."""
    ensure_clicks_schema(conn)
    try:
        cur = conn.execute(
            """
            INSERT INTO ml_clicks
                (item_id, fingerprint, source, category, action, rank_position)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item_id, fingerprint, source, category, action, rank_position),
        )
        conn.commit()
        return cur.lastrowid or 0
    except sqlite3.OperationalError as e:
        log.warning("ml_clicks: log_click failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------
def ctr_per_source(
    conn: sqlite3.Connection,
    *,
    since_days: Optional[int] = None,
    category: Optional[str] = None,
) -> dict:
    """Click-through rate per source: {source: {impressions, clicks, ctr}}.

    "Click" means any POSITIVE_ACTIONS event. Sources with zero impressions
    are not included. Sources are case-preserved as logged.
    """
    ensure_clicks_schema(conn)

    where_imp = ['1=1']
    where_clk = ['action IN (' + ','.join('?' * len(POSITIVE_ACTIONS)) + ')']
    params_imp: list = []
    params_clk: list = list(POSITIVE_ACTIONS)
    if since_days is not None and since_days > 0:
        where_imp.append("ts >= datetime('now', ?)")
        where_clk.append("ts >= datetime('now', ?)")
        delta = f'-{int(since_days)} days'
        params_imp.append(delta)
        params_clk.append(delta)
    if category:
        where_imp.append("category = ?")
        where_clk.append("category = ?")
        params_imp.append(category)
        params_clk.append(category)

    imp_rows = conn.execute(
        f"SELECT source, COUNT(*) FROM ml_impressions "
        f"WHERE {' AND '.join(where_imp)} GROUP BY source",
        params_imp,
    ).fetchall()
    clk_rows = conn.execute(
        f"SELECT source, COUNT(*) FROM ml_clicks "
        f"WHERE {' AND '.join(where_clk)} GROUP BY source",
        params_clk,
    ).fetchall()

    impressions = {src: n for src, n in imp_rows if src}
    clicks = {src: n for src, n in clk_rows if src}

    out = {}
    for src, n_imp in impressions.items():
        n_clk = clicks.get(src, 0)
        out[src] = {
            'impressions': n_imp,
            'clicks': n_clk,
            'ctr': round(n_clk / n_imp, 4) if n_imp else 0.0,
        }
    return out


def bandit_outcomes_since(
    conn: sqlite3.Connection,
    *,
    since_days: int = 30,
) -> Iterable[dict]:
    """Yield outcome events for Stage 6 bandit updates.

    Each event: {category, source, success: bool, ts}.
    A click on a result counts as a success for the source that displayed
    it; an impression with no follow-up click counts as a failure once it
    becomes "stale" (older than the window minus 1 day). For now we only
    emit successes — Stage 6 may compute negatives by impression - success
    differential.
    """
    ensure_clicks_schema(conn)

    placeholders = ','.join('?' * len(POSITIVE_ACTIONS))
    delta = f'-{int(since_days)} days'
    cur = conn.execute(
        f"SELECT category, source, ts FROM ml_clicks "
        f"WHERE action IN ({placeholders}) AND ts >= datetime('now', ?) "
        f"  AND source IS NOT NULL",
        (*POSITIVE_ACTIONS, delta),
    )
    for cat, src, ts in cur.fetchall():
        yield {'category': cat, 'source': src, 'success': True, 'ts': ts}


def positive_fingerprints(
    conn: sqlite3.Connection,
    *,
    since_days: int = 180,
) -> list[dict]:
    """Return canonical fingerprints the user opened/saved.

    Used by Stage 8 taste-rank refinement to boost weights of tokens that
    actually correlated with user action (not just passive 'нравится').
    """
    ensure_clicks_schema(conn)
    placeholders = ','.join('?' * len(POSITIVE_ACTIONS))
    delta = f'-{int(since_days)} days'
    rows = conn.execute(
        f"SELECT item_id, fingerprint, source, category, action, ts "
        f"FROM ml_clicks "
        f"WHERE action IN ({placeholders}) AND ts >= datetime('now', ?) "
        f"  AND fingerprint IS NOT NULL",
        (*POSITIVE_ACTIONS, delta),
    ).fetchall()
    cols = ('item_id', 'fingerprint', 'source', 'category', 'action', 'ts')
    return [dict(zip(cols, r)) for r in rows]


def dismissed_fingerprints(
    conn: sqlite3.Connection,
    *,
    since_days: int = 180,
) -> list[dict]:
    """Mirror of positive_fingerprints for NEGATIVE_ACTIONS."""
    ensure_clicks_schema(conn)
    placeholders = ','.join('?' * len(NEGATIVE_ACTIONS))
    delta = f'-{int(since_days)} days'
    rows = conn.execute(
        f"SELECT item_id, fingerprint, source, category, action, ts "
        f"FROM ml_clicks "
        f"WHERE action IN ({placeholders}) AND ts >= datetime('now', ?) "
        f"  AND fingerprint IS NOT NULL",
        (*NEGATIVE_ACTIONS, delta),
    ).fetchall()
    cols = ('item_id', 'fingerprint', 'source', 'category', 'action', 'ts')
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Debug / inspection helpers
# ---------------------------------------------------------------------------
def recent_events(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict]:
    """Last N events across impressions+clicks for /ml_stats debugging."""
    ensure_clicks_schema(conn)
    rows = conn.execute(
        """
        SELECT 'impression' AS kind, item_id, fingerprint, source, category,
               NULL AS action, rank_position, ts
        FROM ml_impressions
        UNION ALL
        SELECT 'click' AS kind, item_id, fingerprint, source, category,
               action, rank_position, ts
        FROM ml_clicks
        ORDER BY ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    cols = ('kind', 'item_id', 'fingerprint', 'source', 'category',
            'action', 'rank_position', 'ts')
    return [dict(zip(cols, r)) for r in rows]
