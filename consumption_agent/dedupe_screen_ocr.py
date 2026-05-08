#!/usr/bin/env python3
"""MVP deduplication for active screen_ocr items.

Conservative rules only:
- active items only
- screen_ocr only
- soft delete exact normalized duplicates
- keep the most informative variant
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'consumption.db'


def normalize_name(text: str) -> str:
    text = text.lower().replace('ё', 'е')
    text = re.sub(r'^[^\wа-я]+', '', text)
    text = re.sub(r'[^\w\sа-я]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def score_row(row) -> tuple:
    item_id, name, category_id = row
    normalized = normalize_name(name)
    return (
        1 if category_id != 'cat_other' else 0,
        len(normalized),
        len(name),
        item_id,
    )


def load_active_screen_ocr(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, name, category_id FROM items WHERE data_origin='screen_ocr' AND deleted_at IS NULL ORDER BY id"
    ).fetchall()


def build_duplicate_groups(rows):
    groups = defaultdict(list)
    for row in rows:
        key = normalize_name(row[1])
        if not key:
            continue
        groups[key].append(row)
    return {key: vals for key, vals in groups.items() if len(vals) > 1}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--show', type=int, default=20)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = load_active_screen_ocr(conn)
        groups = build_duplicate_groups(rows)

        duplicate_items = sum(len(vals) - 1 for vals in groups.values())
        print(f'active screen_ocr items: {len(rows)}')
        print(f'duplicate groups: {len(groups)}')
        print(f'duplicate items to remove: {duplicate_items}')

        shown = 0
        deletions = []
        for key, vals in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            keep = sorted(vals, key=score_row, reverse=True)[0]
            drop = [row for row in vals if row[0] != keep[0]]
            if shown < args.show:
                print(f'\nKEEP #{keep[0]} [{keep[2]}] {keep[1]}')
                for row in drop:
                    print(f'  DROP #{row[0]} [{row[2]}] {row[1]}')
                shown += 1
            for row in drop:
                deletions.append((keep[0], row[0]))

        if not args.apply:
            print('\ndry-run only; no DB changes applied')
            return

        for keep_id, drop_id in deletions:
            conn.execute(
                """
                UPDATE items
                SET deleted_at = datetime('now'),
                    notes = COALESCE(notes, '') || CASE WHEN notes IS NULL OR notes = '' THEN '' ELSE '\n' END || ?
                WHERE id = ?
                """,
                (f'auto-deduped screen_ocr -> kept #{keep_id}', drop_id),
            )

        conn.commit()
        remaining = conn.execute(
            "SELECT COUNT(*) FROM items WHERE data_origin='screen_ocr' AND deleted_at IS NULL"
        ).fetchone()[0]
        print(f'\ndeduped items: {len(deletions)}')
        print(f'remaining active screen_ocr items: {remaining}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
