"""Tests for dedup.py — Phase 2.5 deduplication."""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dedup


def _setup_conn():
    """Build an in-memory DB with the columns dedup actually touches."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL,
            total_amount REAL,
            source TEXT,
            email_message_id TEXT UNIQUE,
            notes TEXT,
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            purchase_id INTEGER REFERENCES purchases(id)
        )
        """
    )
    return conn


def _add_purchase(conn, **kwargs):
    cols = ','.join(kwargs.keys())
    placeholders = ','.join('?' * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO purchases ({cols}) VALUES ({placeholders})",
        tuple(kwargs.values()),
    )
    return cur.lastrowid


def _add_item(conn, name, purchase_id):
    cur = conn.execute(
        "INSERT INTO items (name, purchase_id) VALUES (?, ?)",
        (name, purchase_id),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------


def test_find_clusters_by_amount_date_source():
    """Same (amount, date, source) with similar notes → one cluster."""
    conn = _setup_conn()
    p1 = _add_purchase(
        conn, purchase_date='2025-05-01', total_amount=499.99,
        source='ozon', email_message_id='m1', notes='Ваш чек: 499.99 ₽',
    )
    p2 = _add_purchase(
        conn, purchase_date='2025-05-01', total_amount=499.99,
        source='ozon', email_message_id='m2', notes='Ваш чек: 499.99 ₽',
    )
    # Different date — must not cluster.
    _add_purchase(
        conn, purchase_date='2025-05-02', total_amount=499.99,
        source='ozon', email_message_id='m3', notes='Ваш чек: 499.99 ₽',
    )
    clusters = dedup.find_duplicate_clusters(conn)
    assert clusters == [[p1, p2]]


def test_zero_amount_excluded_unless_notes_match():
    """0.0/None amount: cluster only if notes match exactly."""
    conn = _setup_conn()
    # Three ofd_yandex zero rows with DIFFERENT notes → no cluster.
    _add_purchase(conn, purchase_date='2025-04-17', total_amount=0.0,
                  source='ofd_yandex', email_message_id='m1', notes='чек А')
    _add_purchase(conn, purchase_date='2025-04-17', total_amount=0.0,
                  source='ofd_yandex', email_message_id='m2', notes='чек Б')
    _add_purchase(conn, purchase_date='2025-04-17', total_amount=0.0,
                  source='ofd_yandex', email_message_id='m3', notes='чек В')
    assert dedup.find_duplicate_clusters(conn) == []

    # Now add two rows with identical notes → cluster.
    p4 = _add_purchase(conn, purchase_date='2025-04-18', total_amount=0.0,
                       source='ofd_yandex', email_message_id='m4', notes='ровно тот же')
    p5 = _add_purchase(conn, purchase_date='2025-04-18', total_amount=0.0,
                       source='ofd_yandex', email_message_id='m5', notes='ровно тот же')
    assert dedup.find_duplicate_clusters(conn) == [[p4, p5]]


def test_merge_preserves_richest_record():
    """Keeper is the purchase with the most linked items."""
    conn = _setup_conn()
    p_lean = _add_purchase(conn, purchase_date='2025-05-01', total_amount=100.0,
                           source='ozon', email_message_id='m1', notes='ozon')
    p_rich = _add_purchase(conn, purchase_date='2025-05-01', total_amount=100.0,
                           source='ozon', email_message_id='m2', notes='ozon')
    _add_item(conn, 'A', p_rich)
    _add_item(conn, 'B', p_rich)
    _add_item(conn, 'C', p_lean)  # 1 item

    merged = dedup.merge_purchases(conn, [p_lean, p_rich])
    assert merged == 1

    # p_rich kept active; p_lean soft-deleted.
    row = conn.execute("SELECT deleted_at FROM purchases WHERE id=?", (p_rich,)).fetchone()
    assert row['deleted_at'] is None
    row = conn.execute("SELECT deleted_at, notes FROM purchases WHERE id=?", (p_lean,)).fetchone()
    assert row['deleted_at'] is not None
    assert f'merged_into={p_rich}' in row['notes']


def test_merge_relinks_items_and_records_origin():
    """Items from losers must move to keeper and remember their origin."""
    conn = _setup_conn()
    p_keep = _add_purchase(conn, purchase_date='2025-06-01', total_amount=50.0,
                           source='samokat_ofd', email_message_id='m1', notes='samokat')
    p_drop = _add_purchase(conn, purchase_date='2025-06-01', total_amount=50.0,
                           source='samokat_ofd', email_message_id='m2', notes='samokat')
    _add_item(conn, 'milk', p_keep)
    item_id = _add_item(conn, 'bread', p_drop)

    dedup.merge_purchases(conn, [p_keep, p_drop])

    row = conn.execute(
        "SELECT purchase_id, linked_purchase_id FROM items WHERE id=?",
        (item_id,),
    ).fetchone()
    assert row['purchase_id'] == p_keep
    assert row['linked_purchase_id'] == p_drop  # audit pointer to original


def test_idempotency_second_run_is_noop():
    """Running dedup twice does not corrupt state or re-merge."""
    conn = _setup_conn()
    p1 = _add_purchase(conn, purchase_date='2025-07-01', total_amount=200.0,
                       source='taxcom', email_message_id='m1', notes='X')
    p2 = _add_purchase(conn, purchase_date='2025-07-01', total_amount=200.0,
                       source='taxcom', email_message_id='m2', notes='X')

    first = dedup.merge_purchases(conn, [p1, p2])
    assert first == 1

    # Second pass: cluster finder must see only one active row left.
    clusters = dedup.find_duplicate_clusters(conn)
    assert clusters == []

    # Notes must not double-tag.
    notes = conn.execute(
        "SELECT notes FROM purchases WHERE deleted_at IS NOT NULL"
    ).fetchone()['notes']
    assert notes.count('merged_into=') == 1


def test_ensure_dedup_schema_idempotent():
    """ensure_dedup_schema adds linked_purchase_id once; second call is no-op."""
    conn = _setup_conn()
    dedup.ensure_dedup_schema(conn)
    cols1 = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    assert 'linked_purchase_id' in cols1
    # Second call must not error or duplicate the column.
    dedup.ensure_dedup_schema(conn)
    cols2 = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    assert cols1 == cols2
