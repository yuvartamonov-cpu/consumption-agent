#!/usr/bin/env python3
"""Import carsharing trips into ``carsharing_trips`` from email receipts.

Delimobil sends fiscal receipts from ``echeck@1-ofd.ru`` (subject
"Чек за DD.MM.YYYY"). Each receipt carries only a timestamp and an amount —
the vehicle model and distance are NOT present in the receipt (the "№ авт."
field is the cash-register / KKT id, not the car). We therefore aggregate
receipts by calendar day into one ``carsharing_trips`` row per day, leaving
``car_model`` / ``distance_km`` empty.

Design notes:
- parsing is pure and unit-tested (``parse_delimobil_receipt`` /
  ``aggregate_by_day``); IMAP I/O is isolated in ``import_delimobil``;
- rows written by this importer are marked ``tariff='чек (авто)'`` so re-runs
  are idempotent and never clobber manually entered trips;
- searches every configured mailbox (gmail/yandex/zorea/neutrinon) — receipts
  may land in any of them.
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

log = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DELIMOBIL_SENDER = 'echeck@1-ofd.ru'
DELIMOBIL_INN = '9718236471'  # ПАО "КАРШЕРИНГ РУССИЯ"
AUTO_TARIFF_MARKER = 'чек (авто)'

_DATETIME_RE = re.compile(r'(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})')
_TOTAL_RE = re.compile(r'ИТОГ[О]?\s*:?\s*(\d[\d\s ]*[.,]\d{2})', re.IGNORECASE)


@dataclass
class ReceiptRecord:
    dt: datetime
    total: float


# ---------------------------------------------------------------------------
# Pure parsing (unit-tested)
# ---------------------------------------------------------------------------

def _parse_amount(raw: str) -> float | None:
    num = re.sub(r'[^\d.,]', '', raw.replace(' ', '').replace(' ', ''))
    if not num:
        return None
    try:
        return float(num.replace(',', '.'))
    except ValueError:
        return None


def parse_delimobil_receipt(text: str) -> ReceiptRecord | None:
    """Extract (datetime, total) from a Delimobil fiscal receipt body.

    Returns None if the text is not a recognisable Delimobil receipt.
    """
    if not text:
        return None
    # Confirm it is a Delimobil / КАРШЕРИНГ РУССИЯ receipt.
    low = text.lower()
    if DELIMOBIL_INN not in text and 'каршеринг руссия' not in low and 'delimobil' not in low:
        return None

    dm = _DATETIME_RE.search(text)
    if not dm:
        return None
    day, month, year, hh, mm = (int(x) for x in dm.groups())
    try:
        dt = datetime(year, month, day, hh, mm)
    except ValueError:
        return None

    tm = _TOTAL_RE.search(text)
    total = _parse_amount(tm.group(1)) if tm else None
    if total is None:
        return None

    return ReceiptRecord(dt=dt, total=total)


@dataclass
class DailyTrip:
    date_iso: str          # YYYY-MM-DD
    date_start: str        # YYYY-MM-DD HH:MM (earliest receipt)
    date_end: str          # YYYY-MM-DD HH:MM (latest receipt)
    total: float
    receipts: int


def aggregate_by_day(records: Iterable[ReceiptRecord]) -> list[DailyTrip]:
    """Group receipts into one trip row per calendar day (summed amounts)."""
    by_day: dict[str, list[ReceiptRecord]] = {}
    for r in records:
        by_day.setdefault(r.dt.strftime('%Y-%m-%d'), []).append(r)

    result: list[DailyTrip] = []
    for day, recs in sorted(by_day.items()):
        recs.sort(key=lambda x: x.dt)
        result.append(DailyTrip(
            date_iso=day,
            date_start=recs[0].dt.strftime('%Y-%m-%d %H:%M'),
            date_end=recs[-1].dt.strftime('%Y-%m-%d %H:%M'),
            total=round(sum(r.total for r in recs), 2),
            receipts=len(recs),
        ))
    return result


# ---------------------------------------------------------------------------
# DB upsert (idempotent)
# ---------------------------------------------------------------------------

def upsert_trips(conn, daily: Iterable[DailyTrip], *, source: str = 'delimobil') -> int:
    """Idempotently write daily aggregated trips for ``source``.

    Only rows previously written by this importer (tariff marker) are replaced,
    so manually entered trips are preserved.
    """
    written = 0
    for d in daily:
        conn.execute(
            "DELETE FROM carsharing_trips WHERE source = ? AND tariff = ? "
            "AND date(date_start) = ?",
            (source, AUTO_TARIFF_MARKER, d.date_iso),
        )
        conn.execute(
            """
            INSERT INTO carsharing_trips
                (date_start, date_end, car_model, car_plate, distance_km,
                 tariff, base_cost, total, source)
            VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
            """,
            (d.date_start, d.date_end, AUTO_TARIFF_MARKER, d.total, d.total, source),
        )
        written += 1
    conn.commit()
    return written


# ---------------------------------------------------------------------------
# IMAP fetch (isolated I/O)
# ---------------------------------------------------------------------------

def _body_text(msg) -> str:
    from bs4 import BeautifulSoup
    raw = ''
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() in ('text/html', 'text/plain'):
                payload = p.get_payload(decode=True)
                if payload:
                    raw += payload.decode('utf-8', errors='replace')
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            raw += payload.decode('utf-8', errors='replace')
    try:
        return BeautifulSoup(raw, 'html.parser').get_text('\n', strip=True)
    except Exception:
        return re.sub(r'<[^>]+>', '\n', raw)


def _fetch_receipts(cfg: dict, *, sender: str, max_msgs: int = 200) -> list[ReceiptRecord]:
    records: list[ReceiptRecord] = []
    try:
        mail = imaplib.IMAP4_SSL(cfg['host'], cfg['port'])
        mail.login(cfg['user'], cfg['password'])
        mail.select('INBOX', readonly=True)
    except Exception as e:
        log.warning('carsharing_import: cannot open %s: %s', cfg.get('user'), e)
        return records
    try:
        typ, data = mail.search(None, 'FROM', sender)
        ids = data[0].split() if data and data[0] else []
        for uid in ids[-max_msgs:]:
            try:
                _, fd = mail.fetch(uid, '(BODY.PEEK[])')
                msg = email.message_from_bytes(fd[0][1])
                rec = parse_delimobil_receipt(_body_text(msg))
                if rec:
                    records.append(rec)
            except Exception as e:
                log.debug('carsharing_import: parse failed for uid %s: %s', uid, e)
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return records


def _load_mailbox_cfgs() -> dict:
    """Reuse the credential resolution from the legacy monolith config."""
    try:
        from dotenv import dotenv_values
        env = dotenv_values(os.path.join(SCRIPT_DIR, '.env'))
    except Exception:
        env = {}

    def g(*names, default=''):
        for n in names:
            v = os.environ.get(n) or env.get(n)
            if v:
                return v
        return default

    return {
        'gmail': {'host': 'imap.gmail.com', 'port': 993,
                  'user': g('IMAP_USER', default='yu.v.artamonov@gmail.com'),
                  'password': g('GMAIL_APP_PASSWORD', 'GMAIL_PASSWORD')},
        'yandex': {'host': 'imap.yandex.ru', 'port': 993,
                   'user': g('YANDEX_USER', default='HKID2021@yandex.ru'),
                   'password': g('YANDEX_APP_PASSWORD', 'YANDEX_PASSWORD')},
        'zorea': {'host': 'imap.mail.ru', 'port': 993,
                  'user': g('ZOREA_USER', default='zorea2001@mail.ru'),
                  'password': g('MAILRU_ZOREA_PASSWORD', 'ZOREA_PASSWORD')},
        'neutrinon': {'host': 'imap.mail.ru', 'port': 993,
                      'user': g('NEUTRINON_USER', default='neutrinon@mail.ru'),
                      'password': g('MAILRU_NEUTRINON_PASSWORD', 'NEUTRINON_PASSWORD')},
    }


def import_delimobil(conn, *, max_msgs: int = 200) -> int:
    """Scan every mailbox for Delimobil receipts and write daily trips."""
    cfgs = _load_mailbox_cfgs()
    all_records: list[ReceiptRecord] = []
    for name, cfg in cfgs.items():
        if not cfg.get('password'):
            log.info('carsharing_import: skip %s (no password)', name)
            continue
        recs = _fetch_receipts(cfg, sender=DELIMOBIL_SENDER, max_msgs=max_msgs)
        log.info('carsharing_import: %s → %d Delimobil receipts', name, len(recs))
        all_records.extend(recs)
    daily = aggregate_by_day(all_records)
    written = upsert_trips(conn, daily, source='delimobil')
    log.info('carsharing_import: wrote %d Delimobil trip-days (%d receipts)',
             written, len(all_records))
    return written


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:
        from consumption.db import connect, DB_PATH
        conn = connect(DB_PATH)
    except ImportError:
        import sqlite3
        conn = sqlite3.connect(os.path.join(SCRIPT_DIR, 'consumption.db'))
    try:
        n = import_delimobil(conn)
        print(f'✅ carsharing_import: {n} Delimobil trip-days written')
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(_main())
