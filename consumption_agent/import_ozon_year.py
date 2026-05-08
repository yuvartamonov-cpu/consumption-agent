#!/usr/bin/env python3
"""
Полный импорт чеков Ozon за последний год в consumption.db
"""
import sqlite3
import imaplib
import email
import re
from email.header import decode_header
from datetime import datetime, timedelta
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')
IMAP_USER = 'yu.v.artamonov@gmail.com'
IMAP_PASS = 'xrsa izwn tvod ohqp'

def decode_subject(msg):
    subj = msg['Subject']
    if not subj:
        return ''
    decoded = decode_header(subj)
    result = []
    for part, enc in decoded:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(enc or 'utf-8', errors='replace'))
            except:
                result.append(part.decode('utf-8', errors='replace'))
        else:
            result.append(str(part))
    return ''.join(result)

def extract_total_from_body(body):
    """Ищет итоговую сумму в теле письма"""
    patterns = [
        r'Итого[:\s]*([\d\s]+[.,]\d{2})',
        r'Сумма[:\s]*([\d\s]+[.,]\d{2})',
        r'Всего[:\s]*([\d\s]+[.,]\d{2})',
        r'(\d[\d\s]*[.,]\d{2})\s*₽',
    ]
    for p in patterns:
        m = re.search(p, body)
        if m:
            val = m.group(1).replace(' ', '').replace(',', '.')
            try:
                return float(val)
            except:
                pass
    return None

def import_ozon_last_year():
    print("Подключаемся к Gmail...")
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select('inbox')

    since = (datetime.now() - timedelta(days=365)).strftime("%d-%b-%Y")
    # Ищем все письма от Ozon, фильтруем по subject в Python
    typ, data = mail.search(None, f'(SINCE {since}) (FROM "ozon")')
    ids = data[0].split()
    print(f"Найдено {len(ids)} писем Ozon за год")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    imported = 0
    skipped = 0

    for i, eid in enumerate(ids):
        if i % 50 == 0:
            print(f"  Обработано {i}/{len(ids)} ...")

        typ, msg_data = mail.fetch(eid, '(RFC822)')
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_subject(msg)
        if 'чек' not in subject.lower():
            skipped += 1
            continue

        # Извлекаем тело
        body = ''
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    body += part.get_payload(decode=True).decode('utf-8', errors='ignore')
        else:
            body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

        total = extract_total_from_body(body)
        date_str = msg.get('Date', '')[:25]

        if total:
            # Проверяем дубликат
            existing = conn.execute(
                "SELECT id FROM purchases WHERE purchase_date = ? AND total_amount = ? AND source = 'ozon_email'",
                (date_str[:10], total)
            ).fetchone()

            if existing:
                skipped += 1
                continue

            conn.execute('''
                INSERT INTO purchases (purchase_date, total_amount, source, data_origin, store_name)
                VALUES (?, ?, 'ozon_email', 'ozon_email', 'Ozon')
            ''', (date_str[:10], total))
            imported += 1

    conn.commit()
    conn.close()
    mail.close()
    mail.logout()

    print(f"\nГотово. Импортировано: {imported}, пропущено: {skipped}")

if __name__ == "__main__":
    import_ozon_last_year()
