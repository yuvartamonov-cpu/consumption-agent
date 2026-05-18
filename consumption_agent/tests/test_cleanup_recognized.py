"""Tests for cleanup_recognized_items.py."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cleanup_recognized_items as cri


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE recognized_items_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            source_type TEXT NOT NULL,
            recognized_product TEXT NOT NULL,
            confidence TEXT,
            matched_item_id INTEGER REFERENCES items(id),
            notes TEXT,
            imported_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO items (id, name) VALUES (1, 'Молоко');
        INSERT INTO recognized_items_log (source_file, source_type, recognized_product, matched_item_id)
        VALUES
          ('shot1.jpg', 'screen_ocr', 'OpenClaw',     NULL),
          ('shot1.jpg', 'screen_ocr', 'Mobile Setup', NULL),
          ('shot2.jpg', 'screen_ocr', 'em Fail ]',    NULL),
          ('shot3.jpg', 'screen_ocr', 'Молоко 1л',    1),
          ('cheque.jpg', 'cheque_ocr', 'Сыр Гауда',   NULL);
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def tmp_db(tmp_path):
    return _make_db(tmp_path / 'consumption.db')


class _Args:
    def __init__(self, apply=False, sample=5):
        self.apply = apply
        self.sample = sample


def test_count_noise_only_matches_screen_ocr_unmatched(tmp_db):
    conn = cri._connect(tmp_db)
    assert cri.count_noise(conn) == 3
    assert cri.count_total(conn) == 5


def test_dry_run_leaves_data_intact(tmp_db, capsys):
    rc = cri.run(_Args(apply=False), db_path=tmp_db)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'Dry-run' in out
    conn = cri._connect(tmp_db)
    assert cri.count_total(conn) == 5
    assert cri.count_noise(conn) == 3


def test_apply_deletes_noise_and_keeps_legitimate(tmp_db, capsys):
    rc = cri.run(_Args(apply=True), db_path=tmp_db)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'Deleted 3 rows' in out
    conn = cri._connect(tmp_db)
    assert cri.count_total(conn) == 2
    assert cri.count_noise(conn) == 0
    surviving = conn.execute(
        "SELECT recognized_product FROM recognized_items_log ORDER BY id"
    ).fetchall()
    names = [r[0] for r in surviving]
    assert 'Молоко 1л' in names
    assert 'Сыр Гауда' in names


def test_idempotent_second_run_is_noop(tmp_db, capsys):
    cri.run(_Args(apply=True), db_path=tmp_db)
    capsys.readouterr()
    rc = cri.run(_Args(apply=True), db_path=tmp_db)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'Nothing to do.' in out
