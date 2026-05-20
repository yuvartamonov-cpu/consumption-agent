"""Tests for carsharing_import — Delimobil receipt parsing and aggregation.

No IMAP / network: parsing and DB upsert are exercised directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import carsharing_import as ci
from consumption.db import connect


# A sanitized copy of a real Delimobil fiscal receipt (echeck@1-ofd.ru).
RECEIPT_SAMPLE = """
Электронная копия чека
Публичное Акционерное Общество "КАРШЕРИНГ РУССИЯ"
ИНН: 9718236471
https://delimobil.ru/
Кассовый чек.
Приход
Смена №: 52
Чек №: 1851
20.05.2026 18:47
№ авт.: KKT041803
1.
Продление аренды транспортного средства
860.29
1
860.29
ИТОГО:
860.29
echeck@1-ofd.ru
"""

# A non-Delimobil receipt from the same fiscal operator (must be ignored).
OTHER_MERCHANT = """
Кассовый чек.
ООО "ПЯТЁРОЧКА"
ИНН: 7700000000
14.05.2026 10:00
Хлеб
50.00
ИТОГО:
50.00
echeck@1-ofd.ru
"""


def _schema(conn):
    conn.executescript(
        """
        CREATE TABLE carsharing_trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_start TEXT, date_end TEXT, car_model TEXT, car_plate TEXT,
            distance_km REAL, tariff TEXT, base_cost REAL, insurance REAL,
            over_minutes_cost REAL DEFAULT 0, discounts REAL DEFAULT 0,
            total REAL, source TEXT DEFAULT 'yandex_drive',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# parse_delimobil_receipt
# ---------------------------------------------------------------------------

def test_parse_receipt_extracts_datetime_and_total():
    rec = ci.parse_delimobil_receipt(RECEIPT_SAMPLE)
    assert rec is not None
    assert rec.dt == datetime(2026, 5, 20, 18, 47)
    assert rec.total == 860.29


def test_parse_receipt_rejects_other_merchant():
    assert ci.parse_delimobil_receipt(OTHER_MERCHANT) is None


def test_parse_receipt_rejects_empty():
    assert ci.parse_delimobil_receipt('') is None
    assert ci.parse_delimobil_receipt('случайный текст') is None


def test_parse_receipt_handles_spaced_thousands():
    text = RECEIPT_SAMPLE.replace('860.29', '1 234,56')
    rec = ci.parse_delimobil_receipt(text)
    assert rec is not None
    assert rec.total == 1234.56


# ---------------------------------------------------------------------------
# aggregate_by_day
# ---------------------------------------------------------------------------

def test_aggregate_groups_by_day_and_sums():
    recs = [
        ci.ReceiptRecord(datetime(2026, 5, 20, 11, 2), 100.0),
        ci.ReceiptRecord(datetime(2026, 5, 20, 18, 47), 860.29),
        ci.ReceiptRecord(datetime(2026, 5, 18, 9, 0), 500.0),
    ]
    days = ci.aggregate_by_day(recs)
    assert len(days) == 2
    by_date = {d.date_iso: d for d in days}
    assert by_date['2026-05-20'].total == 960.29
    assert by_date['2026-05-20'].receipts == 2
    assert by_date['2026-05-20'].date_start == '2026-05-20 11:02'
    assert by_date['2026-05-20'].date_end == '2026-05-20 18:47'
    assert by_date['2026-05-18'].total == 500.0


def test_aggregate_empty():
    assert ci.aggregate_by_day([]) == []


# ---------------------------------------------------------------------------
# upsert_trips (idempotency + manual-row preservation)
# ---------------------------------------------------------------------------

def test_upsert_writes_rows(tmp_path: Path):
    conn = connect(tmp_path / 'c.db')
    _schema(conn)
    days = ci.aggregate_by_day([
        ci.ReceiptRecord(datetime(2026, 5, 20, 11, 2), 100.0),
        ci.ReceiptRecord(datetime(2026, 5, 20, 18, 47), 860.29),
    ])
    n = ci.upsert_trips(conn, days)
    assert n == 1
    row = conn.execute(
        "SELECT date_start, total, source, tariff, car_model, distance_km "
        "FROM carsharing_trips WHERE source='delimobil'"
    ).fetchone()
    assert row['total'] == 960.29
    assert row['tariff'] == ci.AUTO_TARIFF_MARKER
    assert row['car_model'] is None
    assert row['distance_km'] is None
    conn.close()


def test_upsert_idempotent(tmp_path: Path):
    conn = connect(tmp_path / 'd.db')
    _schema(conn)
    days = ci.aggregate_by_day([ci.ReceiptRecord(datetime(2026, 5, 20, 11, 2), 100.0)])
    ci.upsert_trips(conn, days)
    ci.upsert_trips(conn, days)  # re-run must not duplicate
    cnt = conn.execute(
        "SELECT COUNT(*) FROM carsharing_trips WHERE source='delimobil' "
        "AND date(date_start)='2026-05-20'"
    ).fetchone()[0]
    assert cnt == 1
    conn.close()


def test_upsert_preserves_manual_rows(tmp_path: Path):
    conn = connect(tmp_path / 'e.db')
    _schema(conn)
    # A manually entered trip on the same day (no auto marker).
    conn.execute(
        "INSERT INTO carsharing_trips (date_start, source, total, tariff, car_model) "
        "VALUES ('2026-05-20 09:00', 'delimobil', 555.0, 'ручной', 'Kia Rio')"
    )
    conn.commit()
    days = ci.aggregate_by_day([ci.ReceiptRecord(datetime(2026, 5, 20, 18, 47), 860.29)])
    ci.upsert_trips(conn, days)
    rows = conn.execute(
        "SELECT tariff, total FROM carsharing_trips WHERE source='delimobil' "
        "AND date(date_start)='2026-05-20' ORDER BY total"
    ).fetchall()
    # Manual row preserved + one auto row added.
    tariffs = {r['tariff'] for r in rows}
    assert 'ручной' in tariffs
    assert ci.AUTO_TARIFF_MARKER in tariffs
    assert len(rows) == 2
    conn.close()
