"""Smoke tests for the consumption CLI.

We mock out systemctl + socket so the suite runs offline. The CLI is
exercised against a temp DB with minimal schema — enough to make status
and check-db produce real output.
"""
import io
import os
import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            name TEXT,
            deleted_at TEXT,
            purchase_id INTEGER
        );
        CREATE TABLE purchases (
            id INTEGER PRIMARY KEY,
            total_amount REAL,
            source TEXT,
            deleted_at TEXT
        );
        CREATE TABLE cheques_log (id INTEGER PRIMARY KEY);
        CREATE TABLE alerts (id INTEGER PRIMARY KEY, status TEXT);
        INSERT INTO purchases (total_amount, source) VALUES (100.0, 'ozon'), (200.0, 'samokat');
        INSERT INTO items (name, purchase_id) VALUES ('Гречка', 1);
        INSERT INTO items (name, purchase_id) VALUES ('Молоко', 2);
        INSERT INTO cheques_log DEFAULT VALUES;
        INSERT INTO alerts (status) VALUES ('pending'), ('sent');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def tmp_db(tmp_path):
    p = tmp_path / 'consumption.db'
    _make_db(p)
    return p


# ---------------------------------------------------------------------------
# build_parser & dispatch
# ---------------------------------------------------------------------------
def test_build_parser_recognises_all_subcommands():
    p = cli.build_parser()
    for cmd in ('status', 'doctor', 'check-db', 'backup-now', 'restart-bot'):
        args = p.parse_args([cmd])
        assert args.cmd == cmd


def test_main_rejects_unknown_subcommand():
    with pytest.raises(SystemExit):
        cli.main(['nope'])


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def test_status_prints_real_counts(tmp_db, capsys):
    with mock.patch.object(cli, '_systemctl', return_value=(0, 'active\n')):
        rc = cli.cmd_status(None, db_path=tmp_db)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'items' in out and '2' in out
    assert 'purchases' in out
    assert 'linked' in out


# ---------------------------------------------------------------------------
# check-db
# ---------------------------------------------------------------------------
def test_check_db_passes_on_clean_db(tmp_db, capsys):
    rc = cli.cmd_check_db(None, db_path=tmp_db)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'integrity_check' in out
    assert 'foreign_key_check' in out
    assert 'pages' in out


def test_check_db_fails_on_missing_file(tmp_path, capsys):
    rc = cli.cmd_check_db(None, db_path=tmp_path / 'missing.db')
    assert rc == 1
    assert 'missing' in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# backup-now
# ---------------------------------------------------------------------------
class _BackupArgs:
    def __init__(self, out_dir, keep=10):
        self.out_dir = str(out_dir)
        self.keep = keep


def test_backup_now_creates_dated_file_and_prunes(tmp_db, tmp_path, capsys):
    out_dir = tmp_path / 'bk'
    rc = cli.cmd_backup_now(_BackupArgs(out_dir, keep=2), db_path=tmp_db)
    assert rc == 0
    files = list(out_dir.glob('consumption.db.*.bak'))
    assert len(files) == 1
    assert files[0].stat().st_size > 0

    # Two more — prune should kick in (keep=2)
    for _ in range(3):
        cli.cmd_backup_now(_BackupArgs(out_dir, keep=2), db_path=tmp_db)
    files = sorted(out_dir.glob('consumption.db.*.bak'))
    assert len(files) <= 2


# ---------------------------------------------------------------------------
# doctor (no network)
# ---------------------------------------------------------------------------
def test_doctor_reports_missing_env_and_offline_imap(monkeypatch, capsys):
    # Force every TCP ping to fail (no network in CI).
    monkeypatch.setattr(cli, '_tcp_ping', lambda *a, **kw: False)
    # Make sure none of the required env keys are set.
    for k in cli.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    # Point ROOT to a fresh tmp dir so .env is not picked up.
    monkeypatch.setattr(cli, 'ROOT', Path('/nonexistent-cli-root-xyz'))
    rc = cli.cmd_doctor(None)
    out = capsys.readouterr().out
    assert 'doctor' in out.lower()
    assert 'MISSING' in out  # at least one missing env reported
    # Doctor must exit non-zero when there are required failures
    assert rc != 0


# ---------------------------------------------------------------------------
# restart-bot
# ---------------------------------------------------------------------------
def test_restart_bot_returns_ok_when_systemctl_succeeds(capsys):
    def fake_sc(*args):
        if args[0] == 'restart':
            return 0, 'restarted\n'
        if args[0] == 'is-active':
            return 0, 'active\n'
        return 0, 'MainPID=12345\n'
    with mock.patch.object(cli, '_systemctl', side_effect=fake_sc):
        rc = cli.cmd_restart_bot(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert 'active' in out
    assert 'MainPID=12345' in out


def test_restart_bot_propagates_failure(capsys):
    with mock.patch.object(cli, '_systemctl', return_value=(1, 'restart failed\n')):
        rc = cli.cmd_restart_bot(None)
    assert rc != 0
