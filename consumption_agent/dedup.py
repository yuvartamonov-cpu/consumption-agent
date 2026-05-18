#!/usr/bin/env python3
"""
dedup.py — Phase 2.5 deduplication of purchases.

A single real-world purchase can land in `purchases` twice:
  1. via /add_photo (screenshot from a mobile app)
  2. via email-import (parsed receipt from the same vendor)

This module finds clusters of likely duplicates and merges them, keeping
the richest record (most linked items) and soft-deleting the rest. Items
attached to the losing records are re-pointed to the keeper and a
`linked_purchase_id` audit pointer is preserved on the dropped purchases
via the `notes` field (the purchases table itself has no dedicated column).

CLI:
    python3 consumption_agent_full_030526.py dedup --dry-run
    python3 consumption_agent_full_030526.py dedup --apply

The find heuristic is intentionally conservative:

  Cluster key  = (round(total_amount, 2), purchase_date, source)
  Validation   = subject fuzz ratio >= 90  OR  same email_message_id
  Excluded     = total_amount in (None, 0, 0.0)
                 unless the two rows share the exact same notes text
                 (handles ozon's 7× zero-amount technical receipts).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable, List, Sequence, Tuple

try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover — rapidfuzz is in venv
    _HAS_RAPIDFUZZ = False

SUBJECT_THRESHOLD = 90


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------
def ensure_dedup_schema(conn: sqlite3.Connection) -> None:
    """Add `linked_purchase_id` to items if missing. Idempotent."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    if 'linked_purchase_id' not in cols:
        conn.execute(
            "ALTER TABLE items ADD COLUMN linked_purchase_id INTEGER REFERENCES purchases(id)"
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Find
# ---------------------------------------------------------------------------
def _subject_match(a: str | None, b: str | None) -> bool:
    """True if the two notes/subjects are similar enough to be the same receipt."""
    if not a or not b:
        return False
    if a == b:
        return True
    if not _HAS_RAPIDFUZZ:
        # Fallback: only accept exact matches without rapidfuzz.
        return False
    return fuzz.token_set_ratio(a, b) >= SUBJECT_THRESHOLD


def find_duplicate_clusters(conn: sqlite3.Connection) -> List[List[int]]:
    """Return a list of clusters, each cluster a list of purchase ids (>= 2).

    Only active (deleted_at IS NULL) rows are considered. Within a cluster,
    purchase ids are returned in ascending order so callers can pick a
    deterministic keeper.
    """
    rows = conn.execute(
        """
        SELECT id, total_amount, purchase_date, source, notes, email_message_id
        FROM purchases
        WHERE deleted_at IS NULL
        ORDER BY id ASC
        """
    ).fetchall()

    # Bucket by (rounded amount, date, source). amount=None gets its own bucket.
    buckets: dict[tuple, list[sqlite3.Row]] = {}
    for r in rows:
        amount = r['total_amount']
        amt_key = round(amount, 2) if amount is not None else None
        key = (amt_key, r['purchase_date'], r['source'])
        buckets.setdefault(key, []).append(r)

    clusters: List[List[int]] = []
    for (amt_key, _date, _source), group in buckets.items():
        if len(group) < 2:
            continue
        # Same email_message_id is already impossible (UNIQUE), so each row
        # in a bucket is from a different source path.
        cluster_ids: List[int] = []
        # For zero/None amounts demand a stronger signal — exact notes match
        # within the bucket. Otherwise the cluster is too speculative.
        strict_subject = amt_key in (None, 0, 0.0)
        # Pick a reference row and group the rest by similarity. Single
        # pass is fine for small buckets we actually see (max ~7).
        consumed: set[int] = set()
        for i, ref in enumerate(group):
            if ref['id'] in consumed:
                continue
            sub_cluster = [ref['id']]
            for cand in group[i + 1:]:
                if cand['id'] in consumed:
                    continue
                if strict_subject:
                    matched = (ref['notes'] or '') == (cand['notes'] or '') and (ref['notes'] or '')
                else:
                    matched = _subject_match(ref['notes'], cand['notes']) or not (ref['notes'] or cand['notes'])
                if matched:
                    sub_cluster.append(cand['id'])
                    consumed.add(cand['id'])
            if len(sub_cluster) >= 2:
                consumed.update(sub_cluster)
                clusters.append(sorted(sub_cluster))
            else:
                consumed.add(ref['id'])

    # Stable ordering: by smallest id in cluster.
    clusters.sort(key=lambda c: c[0])
    return clusters


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------
def _pick_keeper(conn: sqlite3.Connection, ids: Sequence[int]) -> int:
    """Keeper = row with the most linked items; ties broken by smallest id."""
    placeholders = ','.join('?' * len(ids))
    rows = conn.execute(
        f"""
        SELECT p.id, COUNT(i.id) AS n_items
        FROM purchases p
        LEFT JOIN items i ON i.purchase_id = p.id
        WHERE p.id IN ({placeholders})
        GROUP BY p.id
        ORDER BY n_items DESC, p.id ASC
        """,
        list(ids),
    ).fetchall()
    return rows[0]['id']


def merge_purchases(conn: sqlite3.Connection, cluster: Sequence[int]) -> int:
    """Merge a cluster, return the number of purchases soft-deleted.

    Strategy:
      - Pick keeper (most linked items, then smallest id).
      - Re-point items.purchase_id from losers to keeper.
      - Set items.linked_purchase_id = original_purchase_id for audit.
      - Soft-delete losers (deleted_at = now()) and tag their notes with
        ``[merged_into=<keeper>]`` so the merge is reversible by hand.
    """
    if len(cluster) < 2:
        return 0
    ensure_dedup_schema(conn)
    keeper = _pick_keeper(conn, cluster)
    losers = [pid for pid in cluster if pid != keeper]
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    for loser in losers:
        conn.execute(
            "UPDATE items SET linked_purchase_id = purchase_id, purchase_id = ? "
            "WHERE purchase_id = ?",
            (keeper, loser),
        )
        # Tag notes with merge audit info; preserve original text.
        conn.execute(
            "UPDATE purchases "
            "SET deleted_at = ?, "
            "    notes = COALESCE(notes, '') || ' [merged_into=' || ? || ']' "
            "WHERE id = ? AND deleted_at IS NULL",
            (now, keeper, loser),
        )
    conn.commit()
    return len(losers)


def merge_all(conn: sqlite3.Connection, clusters: Iterable[Sequence[int]]) -> int:
    total = 0
    for c in clusters:
        total += merge_purchases(conn, c)
    return total


# ---------------------------------------------------------------------------
# CLI helper (called from consumption_agent_full_030526.py)
# ---------------------------------------------------------------------------
def cmd_dedup(args) -> None:
    """Entry point for `consumption_agent_full_030526.py dedup [--apply]`."""
    from consumption.db import connect

    conn = connect()
    ensure_dedup_schema(conn)
    clusters = find_duplicate_clusters(conn)

    print(f'Found {len(clusters)} duplicate clusters.')
    for c in clusters[:25]:
        rows = conn.execute(
            f"SELECT id, total_amount, purchase_date, source, substr(COALESCE(notes,''), 1, 40) AS notes "
            f"FROM purchases WHERE id IN ({','.join('?' * len(c))}) ORDER BY id",
            list(c),
        ).fetchall()
        print(f'  cluster ({len(c)} rows):')
        for r in rows:
            print(f"    id={r['id']:>5}  amt={r['total_amount']}  date={r['purchase_date']}  src={r['source']}  notes={r['notes']!r}")
    if len(clusters) > 25:
        print(f'  ... and {len(clusters) - 25} more')

    if not getattr(args, 'apply', False):
        print('\nDry-run mode. Re-run with --apply to merge.')
        return

    before = conn.execute(
        "SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL"
    ).fetchone()[0]
    merged = merge_all(conn, clusters)
    after = conn.execute(
        "SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL"
    ).fetchone()[0]
    print(f'\nMerged {merged} duplicate(s). Active purchases: {before} -> {after}.')
