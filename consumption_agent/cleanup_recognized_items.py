#!/usr/bin/env python3
"""
cleanup_recognized_items.py — purge OCR noise from recognized_items_log.

Background. recognized_items_log accumulates two distinct kinds of rows:

  1. real bot inputs (chef receipts / clothing tags) that matched.py
     successfully linked to an items row — these stay forever.
  2. screen-OCR captures of the OpenClaw / mobile UI itself. Tesseract
     happily produces lines like «OpenClaw», «Mobile Setup», «em Fail ]».
     With matched_item_id IS NULL these are pure dead weight that hurts
     fuzzy-match recall on subsequent runs.

The roadmap technical-debt list calls out ~1230 of these noise rows.

The heuristic is conservative and intentionally narrow:

  DELETE FROM recognized_items_log
   WHERE source_type = 'screen_ocr'
     AND matched_item_id IS NULL

Any matched screen-OCR row is kept (~1000 in production), any future
source_type with its own noise must be added explicitly.

Usage:
    python3 cleanup_recognized_items.py            # dry-run (default)
    python3 cleanup_recognized_items.py --apply    # delete
    python3 cleanup_recognized_items.py --sample 20  # show N rows in dry-run

Exit codes:
    0  success
    1  schema mismatch or DB unreachable
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'consumption.db'

# The cluster is intentionally narrow — we want explicit pattern coverage,
# not «delete everything unmatched».
NOISE_WHERE = "source_type = 'screen_ocr' AND matched_item_id IS NULL"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


def count_noise(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM recognized_items_log WHERE {NOISE_WHERE}").fetchone()[0]


def count_total(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM recognized_items_log").fetchone()[0]


def sample_noise(conn: sqlite3.Connection, n: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT id, source_file, recognized_product, confidence "
        f"FROM recognized_items_log WHERE {NOISE_WHERE} "
        f"ORDER BY id LIMIT ?",
        (n,),
    ).fetchall()


def delete_noise(conn: sqlite3.Connection) -> int:
    """Delete the noise rows and return the number affected. Caller commits."""
    cur = conn.execute(f"DELETE FROM recognized_items_log WHERE {NOISE_WHERE}")
    return cur.rowcount


def run(args, db_path: Path | str | None = None) -> int:
    try:
        conn = _connect(db_path)
    except sqlite3.OperationalError as e:
        print(f'DB error: {e}', file=sys.stderr)
        return 1

    total_before = count_total(conn)
    noise_before = count_noise(conn)
    print(f'recognized_items_log: {total_before} rows total, {noise_before} match noise heuristic')
    if noise_before == 0:
        print('Nothing to do.')
        return 0

    rows = sample_noise(conn, args.sample)
    print(f'\nSample of {len(rows)} (of {noise_before}):')
    for r in rows:
        text = (r['recognized_product'] or '').replace('\n', ' ')[:60]
        print(f'  id={r["id"]:<6} src={r["source_file"][:32]:<32}  {text!r}')

    if not args.apply:
        print('\nDry-run. Re-run with --apply to delete.')
        return 0

    affected = delete_noise(conn)
    conn.commit()
    total_after = count_total(conn)
    noise_after = count_noise(conn)
    print(f'\nDeleted {affected} rows.')
    print(f'After: {total_after} total, {noise_after} noise remaining.')
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='cleanup_recognized_items')
    p.add_argument('--apply', action='store_true', help='Actually delete (default is dry-run).')
    p.add_argument('--sample', type=int, default=20,
                   help='How many noise rows to print as a sample (default 20).')
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
