"""Helpers for cross-source purchase deduplication."""

from __future__ import annotations

import re
import sqlite3
from email.utils import parsedate_to_datetime
from typing import Optional

_DATE_DMY_HHMM_RX = re.compile(r'(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}:\d{2}))?')
_DATE_ISO_RX = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
_HHMM_RX = re.compile(r'\b(\d{2}:\d{2})\b')
_DELIVERY_FEE_RX = re.compile(r'доставк[аи]?\s*[:=+-]?\s*(\d[\d\s]*[.,]?\d*)', re.IGNORECASE)

STORE_ALIASES = (
    (('умный ритейл', 'умныи ритеил', 'умный ритеил', 'smart retail', 'samokat', 'самокат'), 'Самокат'),
    (('sberchaevye', 'sbertips', 'сберчаевые'), 'СберЧаевые'),
    (('гку ампп', 'ампп'), 'ГКУ "АМПП"'),
)


def normalize_purchase_date(date_str: str | None) -> str:
    if not date_str:
        return ''
    value = str(date_str).strip()
    if not value:
        return ''
    match = _DATE_DMY_HHMM_RX.search(value)
    if match:
        day, month, year = match.group(1), match.group(2), match.group(3)
        return f'{year}-{month}-{day}'
    match = _DATE_ISO_RX.search(value)
    if match:
        return f'{match.group(1)}-{match.group(2)}-{match.group(3)}'
    return value.split()[0]


def extract_event_time(value: str | None) -> Optional[str]:
    if not value:
        return None
    match = _HHMM_RX.search(str(value))
    return match.group(1) if match else None


def email_event_details(parsed_date: str | None, raw_date: str | None) -> tuple[str, Optional[str]]:
    normalized = normalize_purchase_date(parsed_date)
    event_time = extract_event_time(parsed_date)
    if normalized and event_time:
        return normalized, event_time

    if raw_date:
        try:
            dt = parsedate_to_datetime(raw_date)
            normalized = dt.date().isoformat()
            event_time = dt.strftime('%H:%M')
            return normalized, event_time
        except Exception:
            pass

    return normalized, event_time


def build_time_note(event_time: str | None) -> str:
    return f'время {event_time}' if event_time else ''


def build_delivery_note(delivery_fee: float | int | None) -> str:
    if delivery_fee in (None, 0):
        return ''
    amount = f'{float(delivery_fee):.2f}'.rstrip('0').rstrip('.')
    return f'доставка {amount} ₽'


def extract_delivery_fee(value: str | None) -> Optional[float]:
    if not value:
        return None
    match = _DELIVERY_FEE_RX.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(1).replace(' ', '').replace(',', '.'))
    except Exception:
        return None


def canonical_store_name(store_name: str | None) -> str:
    if not store_name:
        return ''
    original = str(store_name).strip()
    lowered = original.lower()
    normalized = lowered.replace('ё', 'е')
    for variants, canonical in STORE_ALIASES:
        if any(variant in normalized for variant in variants):
            return canonical
    return original


def is_duplicate_purchase(
    conn: sqlite3.Connection,
    date_str: str | None,
    amount: float | int | None,
    store_name: str | None,
    *,
    event_time: str | None = None,
    email_msg_id: str | None = None,
    delivery_fee: float | int | None = None,
) -> bool:
    canonical_store = canonical_store_name(store_name)
    if not date_str or amount is None or not canonical_store:
        return False

    normalized_date = normalize_purchase_date(date_str)
    if not normalized_date:
        return False

    if email_msg_id:
        row = conn.execute(
            'SELECT id FROM purchases WHERE email_message_id = ? AND deleted_at IS NULL',
            (str(email_msg_id).strip(),),
        ).fetchone()
        if row:
            return True

    rows = conn.execute(
        '''
        SELECT id, total_amount, notes, source, email_message_id
        FROM purchases
        WHERE purchase_date = ?
          AND store_name = ?
          AND deleted_at IS NULL
        ''',
        (normalized_date, canonical_store),
    ).fetchall()
    if not rows:
        return False

    if not event_time:
        return False

    current_total = float(amount)
    current_base = current_total - float(delivery_fee or 0)

    for row in rows:
        existing_time = extract_event_time(row[2])
        if existing_time and existing_time == event_time:
            existing_total = float(row[1] or 0)
            existing_delivery = float(extract_delivery_fee(row[2]) or 0)
            existing_base = existing_total - existing_delivery
            if abs(existing_total - current_total) < 0.01:
                return True
            if abs(existing_base - current_total) < 0.01:
                return True
            if abs(existing_total - current_base) < 0.01:
                return True
            if abs(existing_base - current_base) < 0.01:
                return True

    return False
