#!/usr/bin/env python3
"""
Consumption Agent — мониторинг SMS на Windows через Phone Link.
Читает SQLite-базу приложения Microsoft Phone Link / Your Phone.
"""
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

WINDOWS_PHONE_LINK_DB_GLOB = (
    "/mnt/c/Users/*/AppData/Local/Packages/"
    "Microsoft.YourPhone_8wekyb3d8bbwe/LocalCache/Indexed/*/System/Database/phone.db"
)

BANK_SMS_SENDERS = {
    '900': 'sberbank',
    'sberid': 'sberbank',
    'vtb': 'vtb',
    'alfa-bank': 'alfa',
    'alfabank': 'alfa',
    'tinkoff': 'tinkoff',
    't-bank': 'tinkoff',
    'sovcombank': 'sovcombank',
    'halva': 'sovcombank',
}

BODY_SENDER_PATTERNS = {
    'sberbank': ['сбер', 'sber', '900'],
    'vtb': ['втб', 'vtb'],
    'alfa': ['альфа', 'alfa', 'alfa-bank'],
    'tinkoff': ['тинькофф', 'tinkoff', 't-bank', 'т-банк'],
    'sovcombank': ['совком', 'sovcom', 'халва', 'halva'],
    'joy_finance': ['joy finance', 'джой финанс'],
    'turbozaim': ['turbozaim', 'турбозайм'],
    'nebus': ['nebus', 'небус'],
    'boostra': ['boostra', 'бустра'],
    'ekvazaim': ['эквазайм', 'ekvazaim'],
    'webzaim': ['webzaim', 'вебзайм'],
}

SMS_INTEREST_PATTERNS = [
    r'не забудьте внести',
    r'внесите плат[её]ж',
    r'внесите .* до ',
    r'до\s+\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?',
    r'до\s+\d{4}-\d{2}-\d{2}',
    r'очередн[а-я\s]+плат[её]ж',
    r'сумма платежа',
    r'к оплате',
    r'задолж',
    r'взнос',
    r'минимальн[а-я\s]+плат[её]ж',
    r'плат[её]ж по займу',
    r'по кредитке',
    r'не допустить просрочку',
    r'спишем\s+\d',
]

SMS_EXCLUDE_PATTERNS = [
    r'код',
    r'никому не сообщайте',
    r'покупк',
    r'баланс',
    r'перевод',
    r'заявк[аи].*кредит',
    r'одобрен',
    r'готов[оы]? к переводу',
    r'получите',
    r'кредитн[а-я\s]+истори',
    r'подтвердите',
    r'подписк',
    r'оставил вам сообщение',
    r'обручальн',
]


def windows_ticks_to_datetime(value: int) -> Optional[datetime]:
    """Конвертирует Windows FILETIME (100ns since 1601-01-01) в datetime."""
    try:
        unix_seconds = (int(value) - 116444736000000000) / 10_000_000
        return datetime.fromtimestamp(unix_seconds)
    except Exception:
        return None


def find_phone_link_db() -> Optional[str]:
    import glob
    matches = glob.glob(WINDOWS_PHONE_LINK_DB_GLOB)
    matches = [m for m in matches if os.path.exists(m)]
    if not matches:
        return None
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return matches[0]


def copy_db_bundle(src_db: str) -> str:
    """Копирует db + wal/shm во временную папку для безопасного чтения."""
    tmp_dir = tempfile.mkdtemp(prefix='phone_link_')
    base = Path(src_db)
    for suffix in ('', '-wal', '-shm'):
        src = str(base) + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp_dir, os.path.basename(src)))
    return os.path.join(tmp_dir, os.path.basename(src_db))


def detect_sender_name(from_address: str, body: str) -> str:
    sender = (from_address or '').lower()
    for key, bank_id in BANK_SMS_SENDERS.items():
        if key in sender:
            return bank_id

    text = f"{from_address} {body}".lower()
    for bank_id, patterns in BODY_SENDER_PATTERNS.items():
        for pattern in patterns:
            if pattern in text:
                return bank_id
    return 'unknown'


def is_credit_sms(from_address: str, body: str) -> bool:
    text = f"{from_address} {body}".lower()
    if any(re.search(p, text, re.IGNORECASE) for p in SMS_EXCLUDE_PATTERNS):
        return False

    payment_signal = any(re.search(p, text, re.IGNORECASE) for p in SMS_INTEREST_PATTERNS)
    lender_signal = detect_sender_name(from_address, body) != 'unknown' or any(
        token in text for token in ['займ', 'кредит', 'кредитке', 'мкк', 'просроч', 'льготн']
    )
    return payment_signal and lender_signal


def extract_sms_payment_date(body: str) -> Optional[datetime]:
    months = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
        'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
        'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
    }
    patterns = [
        r'до\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4})',
        r'до\s+(\d{1,2}[./]\d{1,2})',
        r'(\d{4}-\d{2}-\d{2})',
        r'(\d{1,2}[./]\d{1,2}[./]\d{2,4})',
        r'до\s+(\d{1,2}\s+[а-я]+(?:\s+\d{4})?)',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if not m:
            continue
        s = m.group(1)
        try:
            if '-' in s and len(s) >= 10:
                year, month, day = map(int, s.split('-'))
                return datetime(year, month, day)
            if '.' in s or '/' in s:
                parts = re.split(r'[./]', s)
                day = int(parts[0])
                month = int(parts[1])
                year = datetime.now().year if len(parts) < 3 or not parts[2] else int(parts[2])
                if year < 100:
                    year += 2000
                return datetime(year, month, day)
            parts = s.split()
            day = int(parts[0])
            month = months.get(parts[1].lower())
            year = int(parts[2]) if len(parts) > 2 else datetime.now().year
            if month:
                return datetime(year, month, day)
        except Exception:
            continue
    return None


def extract_sms_payment_amount(body: str) -> Optional[float]:
    patterns = [
        r'внести\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'плат[её]ж(?:\s+по\s+кредитке)?\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'к оплате\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'спишем\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if not m:
            continue
        try:
            return float(m.group(1).replace(' ', '').replace('\u00A0', '').replace(',', '.'))
        except Exception:
            continue
    return None


def scan_sms_messages(days_back: int = 7) -> List[dict]:
    """Сканирует SMS из Phone Link database."""
    db_path = find_phone_link_db()
    if not db_path:
        print('⚠️ База Phone Link не найдена')
        return []

    local_db = copy_db_bundle(db_path)
    conn = sqlite3.connect(local_db)
    conn.row_factory = sqlite3.Row

    cutoff = datetime.now() - timedelta(days=days_back)
    rows = conn.execute(
        'SELECT message_id, thread_id, from_address, to_address, body, timestamp, type FROM message ORDER BY timestamp DESC LIMIT 2000'
    ).fetchall()

    alerts = []
    for row in rows:
        dt = windows_ticks_to_datetime(row['timestamp'])
        if dt and dt < cutoff:
            continue

        from_address = row['from_address'] or ''
        body = row['body'] or ''
        if not body.strip():
            continue
        if not is_credit_sms(from_address, body):
            continue

        sender_name = detect_sender_name(from_address, body)
        payment_date = extract_sms_payment_date(body)
        payment_amount = extract_sms_payment_amount(body)

        alerts.append({
            'source': 'sms',
            'sender': from_address,
            'sender_name': sender_name,
            'subject': body[:100],
            'body': body,
            'payment_date': payment_date,
            'payment_amount': payment_amount,
            'timestamp': dt,
            'raw_message_id': f"sms:{row['message_id']}",
        })

    conn.close()
    return alerts


def main():
    alerts = scan_sms_messages(days_back=7)
    print(f'Найдено SMS-алертов: {len(alerts)}')
    for a in alerts[:10]:
        print(a)


if __name__ == '__main__':
    main()
