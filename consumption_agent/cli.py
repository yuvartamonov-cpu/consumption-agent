#!/usr/bin/env python3
"""
consumption — Phase A CLI for routine maintenance of consumption_agent.

Subcommands:
    consumption status        Snapshot of production state (DB + bot + cron)
    consumption doctor        Health checks (env, IMAP, disk, hooks)
    consumption check-db      SQLite integrity + size + index report
    consumption backup-now    Timestamped .backup of consumption.db
    consumption restart-bot   systemctl --user restart consumption-bot.service

All commands exit 0 on success, non-zero on failure. Output is plain text
suitable for ssh+grep workflows.

The CLI is invoked from the repo root:

    python3 cli.py <subcommand>

or from anywhere as long as DB_PATH resolves (defaults to consumption.db
next to this file).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'consumption.db'
BACKUP_DIR = ROOT / 'backups'
BARE_HOOK = Path('/mnt/c/Users/Yuri Artamonov/CLaudeCodeConsumption/consumption_agent.git/hooks/pre-receive')

OK = '✅'
WARN = '⚠️'
FAIL = '❌'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


def _systemctl(*args: str) -> tuple[int, str]:
    """Run `systemctl --user ...` and return (rc, combined output)."""
    try:
        r = subprocess.run(
            ['systemctl', '--user', *args],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except FileNotFoundError:
        return 127, 'systemctl not found'
    except Exception as e:  # noqa: BLE001
        return 1, f'systemctl error: {e}'


def _tcp_ping(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def cmd_status(_args, db_path: Path | str | None = None) -> int:
    conn = _connect(db_path)
    print('consumption status')
    print('=' * 50)

    def _scalar(q: str, *p) -> int:
        try:
            return conn.execute(q, p).fetchone()[0]
        except sqlite3.OperationalError:
            return -1

    items_active = _scalar("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL")
    items_total = _scalar("SELECT COUNT(*) FROM items")
    purchases_active = _scalar("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL")
    purchases_total = _scalar("SELECT COUNT(*) FROM purchases")
    purchases_linked = _scalar(
        "SELECT COUNT(DISTINCT purchase_id) FROM items "
        "WHERE deleted_at IS NULL AND purchase_id IS NOT NULL"
    )
    cheques = _scalar("SELECT COUNT(*) FROM cheques_log")
    alerts_pending = _scalar("SELECT COUNT(*) FROM alerts WHERE status='pending'")
    sources = _scalar("SELECT COUNT(DISTINCT source) FROM purchases WHERE source IS NOT NULL")

    print(f'items       {items_active:>6} active / {items_total} total')
    print(f'purchases   {purchases_active:>6} active / {purchases_total} total')
    pct = (purchases_linked / purchases_active * 100) if purchases_active else 0
    print(f'  linked    {purchases_linked:>6} ({pct:.0f}%)')
    print(f'cheques_log {cheques:>6}')
    print(f'alerts.pending {alerts_pending}')
    print(f'sources distinct {sources}')

    # Optional tables — not present on every DB snapshot
    ml = _scalar("SELECT COUNT(*) FROM memory_lane_items")
    if ml >= 0:
        print(f'memory_lane {ml:>6}')
    cred = _scalar("SELECT COUNT(*) FROM credit_alerts")
    if cred >= 0:
        print(f'credit_alerts {cred:>6}')

    print('-' * 50)
    rc, out = _systemctl('is-active', 'consumption-bot.service')
    state = out.strip()
    icon = OK if state == 'active' else FAIL
    print(f'{icon} consumption-bot.service: {state}')
    rc2, out2 = _systemctl('show', 'consumption-bot.service',
                            '--property=MainPID,ActiveEnterTimestamp')
    if rc2 == 0:
        for line in out2.strip().splitlines():
            print(f'  {line}')
    conn.close()
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
REQUIRED_ENV = (
    'GMAIL_APP_PASSWORD',
    'CONSUMPTION_BOT_TOKEN',
    'OWNER_CHAT_ID',
)
OPTIONAL_ENV = (
    'YANDEX_APP_PASSWORD',
    'MAILRU_ZOREA_PASSWORD',
    'MAILRU_NEUTRINON_PASSWORD',
)
REQUIRED_DEPS = ('rapidfuzz', 'bs4', 'telegram')


def _check_env() -> list[tuple[str, str, str]]:
    out = []
    # Load .env best-effort (not via dotenv to keep zero-deps)
    env_path = ROOT / '.env'
    extra: dict[str, str] = {}
    if env_path.is_file():
        try:
            mode = env_path.stat().st_mode & 0o777
            if mode != 0o600:
                out.append((WARN, '.env permissions', f'mode {oct(mode)}, expected 600'))
            else:
                out.append((OK, '.env permissions', '600'))
            for line in env_path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                extra[k.strip()] = v.strip().strip('"').strip("'")
        except OSError as e:
            out.append((FAIL, '.env read', str(e)))
    else:
        out.append((WARN, '.env file', 'not found'))

    for key in REQUIRED_ENV:
        val = os.environ.get(key) or extra.get(key)
        out.append((OK if val else FAIL, f'env {key}', 'set' if val else 'MISSING'))
    for key in OPTIONAL_ENV:
        val = os.environ.get(key) or extra.get(key)
        out.append((OK if val else WARN, f'env {key}', 'set' if val else 'absent (optional)'))
    return out


def _check_deps() -> list[tuple[str, str, str]]:
    out = []
    for dep in REQUIRED_DEPS:
        try:
            importlib.import_module(dep)
            out.append((OK, f'import {dep}', 'OK'))
        except ImportError:
            out.append((FAIL, f'import {dep}', 'NOT INSTALLED'))
    return out


def _check_connectivity() -> list[tuple[str, str, str]]:
    out = []
    pings = [
        ('imap.gmail.com', 993, 'Gmail IMAP'),
        ('smtp.gmail.com', 465, 'Gmail SMTP'),
        ('imap.yandex.ru', 993, 'Yandex IMAP'),
        ('imap.mail.ru', 993, 'Mail.ru IMAP'),
    ]
    for host, port, label in pings:
        ok = _tcp_ping(host, port)
        out.append((OK if ok else WARN, label, f'{host}:{port} {"reachable" if ok else "blocked/timeout"}'))
    return out


def _check_hook() -> list[tuple[str, str, str]]:
    out = []
    if BARE_HOOK.exists():
        if os.access(BARE_HOOK, os.X_OK):
            out.append((OK, 'pre-receive hook', 'installed & executable'))
        else:
            out.append((WARN, 'pre-receive hook', 'exists but not executable'))
    else:
        out.append((WARN, 'pre-receive hook', f'not found at {BARE_HOOK}'))
    return out


def _check_disk() -> list[tuple[str, str, str]]:
    out = []
    try:
        usage = shutil.disk_usage(ROOT)
        free_gb = usage.free / 1024 ** 3
        pct = usage.used / usage.total * 100
        icon = OK if free_gb > 1 and pct < 95 else WARN
        out.append((icon, 'disk space', f'{free_gb:.1f} GB free, {pct:.0f}% used'))
    except OSError as e:
        out.append((FAIL, 'disk space', str(e)))
    return out


def cmd_doctor(_args) -> int:
    print('consumption doctor')
    print('=' * 50)
    sections = [
        ('Environment', _check_env),
        ('Dependencies', _check_deps),
        ('Connectivity', _check_connectivity),
        ('Disk', _check_disk),
        ('Git hooks', _check_hook),
    ]
    bad = 0
    for label, fn in sections:
        print(f'\n[{label}]')
        for icon, name, detail in fn():
            print(f'{icon} {name}: {detail}')
            if icon == FAIL:
                bad += 1
    print()
    print(f'doctor: {"OK" if bad == 0 else f"{bad} failures"}')
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------------------
# check-db
# ---------------------------------------------------------------------------
def cmd_check_db(_args, db_path: Path | str | None = None) -> int:
    p = Path(db_path) if db_path else DB_PATH
    print('consumption check-db')
    print('=' * 50)
    print(f'DB: {p}')
    if not p.exists():
        print(f'{FAIL} file missing')
        return 1

    size_mb = p.stat().st_size / 1024 ** 2
    print(f'size: {size_mb:.2f} MB')

    conn = _connect(p)
    integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
    print(f'{OK if integrity == "ok" else FAIL} integrity_check: {integrity}')

    fk_problems = conn.execute('PRAGMA foreign_key_check').fetchall()
    if fk_problems:
        print(f'{WARN} foreign_key_check: {len(fk_problems)} issues')
        for row in fk_problems[:5]:
            print(f'    {dict(row)}')
    else:
        print(f'{OK} foreign_key_check: clean')

    page_count = conn.execute('PRAGMA page_count').fetchone()[0]
    page_size = conn.execute('PRAGMA page_size').fetchone()[0]
    print(f'pages: {page_count} × {page_size} B = {page_count * page_size / 1024 ** 2:.2f} MB')

    indexes = conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY tbl_name, name"
    ).fetchall()
    print(f'indexes: {len(indexes)}')
    for row in indexes[:15]:
        print(f'  {row["tbl_name"]}.{row["name"]}')
    if len(indexes) > 15:
        print(f'  ... +{len(indexes) - 15} more')

    conn.close()
    return 0 if integrity == 'ok' else 1


# ---------------------------------------------------------------------------
# backup-now
# ---------------------------------------------------------------------------
def cmd_backup_now(args, db_path: Path | str | None = None) -> int:
    src = Path(db_path) if db_path else DB_PATH
    out_dir = Path(getattr(args, 'out_dir', None) or BACKUP_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print(f'{FAIL} source DB missing: {src}')
        return 1
    ts = _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = out_dir / f'consumption.db.{ts}.bak'

    conn = _connect(src)
    bk = _connect(dst)
    with bk:
        conn.backup(bk)
    bk.close()
    conn.close()

    sz_mb = dst.stat().st_size / 1024 ** 2
    print(f'{OK} backup: {dst}  ({sz_mb:.2f} MB)')
    # Retention: keep last N. Default 10.
    keep = int(getattr(args, 'keep', 10) or 10)
    backups = sorted(out_dir.glob('consumption.db.*.bak'))
    if len(backups) > keep:
        for old in backups[:-keep]:
            try:
                old.unlink()
                print(f'  pruned {old.name}')
            except OSError as e:
                print(f'  could not prune {old.name}: {e}')
    return 0


# ---------------------------------------------------------------------------
# restart-bot
# ---------------------------------------------------------------------------
def cmd_restart_bot(_args) -> int:
    print('consumption restart-bot')
    print('=' * 50)
    rc, out = _systemctl('restart', 'consumption-bot.service')
    print(out.rstrip() or f'restart rc={rc}')
    if rc != 0:
        return rc
    rc2, status = _systemctl('is-active', 'consumption-bot.service')
    state = status.strip()
    icon = OK if state == 'active' else FAIL
    print(f'{icon} state: {state}')
    rc3, show = _systemctl('show', 'consumption-bot.service', '--property=MainPID')
    print(show.rstrip())
    return 0 if state == 'active' else 1


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='consumption')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('status', help='Snapshot of production state')
    sub.add_parser('doctor', help='Health checks')
    sub.add_parser('check-db', help='SQLite integrity report')

    bk = sub.add_parser('backup-now', help='Timestamped backup of consumption.db')
    bk.add_argument('--out-dir', dest='out_dir', help='Custom backup dir')
    bk.add_argument('--keep', type=int, default=10, help='Retain last N backups')

    sub.add_parser('restart-bot', help='Restart consumption-bot.service')
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        'status': cmd_status,
        'doctor': cmd_doctor,
        'check-db': cmd_check_db,
        'backup-now': cmd_backup_now,
        'restart-bot': cmd_restart_bot,
    }
    return handlers[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
