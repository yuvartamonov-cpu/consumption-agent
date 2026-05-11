#!/usr/bin/env python3
"""
Сканирование почты на чеки, УПД, счета, билеты и другие документы
(кроме маркетплейсов)
"""
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import re

IMAP_USER = 'yu.v.artamonov@gmail.com'
IMAP_PASS = os.getenv('GMAIL_APP_PASSWORD', '').replace('"', '').replace(' ', '')

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
    return ''.join(result).lower()

def categorize_email(subject, from_addr):
    """Определяет категорию документа"""
    text = (subject + ' ' + from_addr).lower()

    # Кассовые чеки
    if any(x in text for x in ['кассовый чек', 'электронный чек', 'фискальный чек', 'ваш чек', 'чек №']):
        return 'Кассовый чек'

    # УПД, счета, накладные, акты
    if any(x in text for x in ['упд', 'универсальный передаточный', 'счет на оплату', 'счет-фактура',
                                'накладная', 'торг-12', 'акт выполненных', 'акт оказанных', 'акт сверки']):
        return 'УПД / Счет / Накладная / Акт'

    # Билеты и транспорт
    if any(x in text for x in ['электронный билет', 'посадочный', 'билет на', 'авиабилет', 'билет']):
        if any(x in text for x in ['rzd', 's7', 'aeroflot', 'tutu', 'ostrovok', 'avia', 'ticket']):
            return 'Билет / Транспорт'

    # Аптеки и медицина
    if any(x in text for x in ['аптека', 'рецепт', 'zdravcity', 'rigla', '36.6', '36,6']):
        return 'Аптека / Медицина'

    # Общие признаки оплаты
    if any(x in text for x in ['итого к оплате', 'сумма оплаты', 'оплачено', 'платежное поручение', 'qr-код']):
        return 'Другое (признаки оплаты)'

    return None

def main():
    print("Подключаемся к Gmail...")
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(IMAP_USER, IMAP_PASS)
    mail.select('inbox')

    since = (datetime.now() - timedelta(days=365)).strftime("%d-%b-%Y")
    typ, data = mail.search(None, f'(SINCE {since})')
    all_ids = data[0].split()
    print(f"Всего писем за год: {len(all_ids)}")

    categories = {}
    total_found = 0

    # Обрабатываем последние 3000 писем (чтобы не ждать слишком долго)
    recent_ids = all_ids[-3000:] if len(all_ids) > 3000 else all_ids

    print(f"Анализируем последние {len(recent_ids)} писем...")

    for i, eid in enumerate(recent_ids):
        if i % 200 == 0:
            print(f"  Обработано {i}/{len(recent_ids)}...")

        typ, msg_data = mail.fetch(eid, '(RFC822)')
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_subject(msg)
        from_addr = msg.get('From', '').lower()

        category = categorize_email(subject, from_addr)
        if category:
            categories[category] = categories.get(category, 0) + 1
            total_found += 1

    mail.close()
    mail.logout()

    print("\n" + "="*50)
    print("Результаты сканирования (последние ~3000 писем):")
    print("="*50)
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")
    print(f"\nВсего найдено документов с признаками покупок: {total_found}")

if __name__ == "__main__":
    main()
