#!/usr/bin/env python3
"""Backfill items for Ozon purchases that have email_message_id but no linked items.

Usage:
    python backfill_ozon_items.py [--db PATH] [--dry-run] [--limit N]

Connects to Gmail IMAP, fetches each Ozon email by UID, parses items with
_parse_ozon_items, and inserts them idempotently into the items table.
"""
import argparse
import email
import imaplib
import os
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from consumption_agent_full_030526 import (
    DB_PATH,
    IMAP_CFG,
    _parse_ozon_items,
)


def _fetch_html(mail, uid_str):
    """Return HTML body of email with the given UID string, or '' on failure."""
    uid_b = uid_str.encode() if isinstance(uid_str, str) else uid_str
    try:
        _, fd = mail.fetch(uid_b, '(BODY.PEEK[])')
        if not fd or fd[0] is None:
            return ''
        msg = email.message_from_bytes(fd[0][1])
        html = ''
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/html':
                    payload = part.get_payload(decode=True)
                    if payload:
                        html += payload.decode('utf-8', errors='replace')
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                html = payload.decode('utf-8', errors='replace')
        return html
    except Exception:
        return ''


def _purchases_without_items(conn):
    """Return list of (purchase_id, source, purchase_date, email_message_id)."""
    return conn.execute("""
        SELECT p.id, p.source, p.purchase_date, p.email_message_id
        FROM purchases p
        WHERE p.source IN ('ozon', 'ozon_noreply')
          AND p.deleted_at IS NULL
          AND p.email_message_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM items i
              WHERE i.purchase_id = p.id AND i.deleted_at IS NULL
          )
        ORDER BY p.id
    """).fetchall()


def _insert_items(conn, purchase_id, source, purchase_date, items, dry_run=False):
    """Insert items for a purchase, skipping duplicates. Returns count inserted."""
    inserted = 0
    for item in items:
        existing = conn.execute(
            "SELECT id FROM items WHERE name = ? AND purchase_id = ?",
            (item['name'], purchase_id),
        ).fetchone()
        if existing:
            continue
        if not dry_run:
            conn.execute(
                """INSERT INTO items
                   (name, category_id, quantity, unit, purchase_price,
                    purchase_date, purchase_source, purchase_id, data_origin)
                   VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'email_import')""",
                (
                    item['name'],
                    item.get('qty', 1),
                    item.get('unit', 'шт'),
                    item.get('price'),
                    purchase_date,
                    source,
                    purchase_id,
                ),
            )
        inserted += 1
    return inserted


def run_backfill(db_path=None, dry_run=False, limit=None, imap_cfg=None):
    """Run the backfill. Returns (processed, items_added) counts."""
    db_path = db_path or DB_PATH
    imap_cfg = imap_cfg or IMAP_CFG

    conn = sqlite3.connect(db_path)
    rows = _purchases_without_items(conn)
    if limit:
        rows = rows[:limit]

    total = len(rows)
    print(f'Ozon purchases without items: {total}', flush=True)
    if total == 0:
        conn.close()
        return 0, 0

    # Count before
    before = conn.execute(
        "SELECT COUNT(*) FROM items WHERE purchase_source IN ('ozon','ozon_noreply')"
    ).fetchone()[0]

    mail = imaplib.IMAP4_SSL(imap_cfg['host'], imap_cfg['port'])
    mail.login(imap_cfg['user'], imap_cfg['password'])
    mail.select('INBOX')

    processed = 0
    items_added = 0

    for purchase_id, source, purchase_date, uid_str in rows:
        html = _fetch_html(mail, uid_str)
        items = _parse_ozon_items(html) if html else []
        n = _insert_items(conn, purchase_id, source, purchase_date, items, dry_run)
        items_added += n
        processed += 1
        if processed % 20 == 0:
            if not dry_run:
                conn.commit()
            print(f'  {processed}/{total} processed, {items_added} items added so far', flush=True)

    if not dry_run:
        conn.commit()

    mail.logout()

    after = conn.execute(
        "SELECT COUNT(*) FROM items WHERE purchase_source IN ('ozon','ozon_noreply')"
    ).fetchone()[0]

    conn.close()

    print(f'{processed}/{total} processed, {items_added} items added')
    if not dry_run:
        print(f'Ozon items: {before} → {after} (linked_items delta: +{after - before})')

    return processed, items_added


def main():
    parser = argparse.ArgumentParser(description='Backfill items for Ozon purchases')
    parser.add_argument('--db', default=None, help='Path to consumption.db')
    parser.add_argument('--dry-run', action='store_true', help='Parse but do not write to DB')
    parser.add_argument('--limit', type=int, default=None, help='Process at most N purchases')
    args = parser.parse_args()
    run_backfill(db_path=args.db, dry_run=args.dry_run, limit=args.limit)


if __name__ == '__main__':
    main()
