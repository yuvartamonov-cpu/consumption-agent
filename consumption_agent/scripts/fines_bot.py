#!/usr/bin/env python3
"""
Fines Bot — мониторинг штрафов (ГИБДД, парковки, МСД) из писем Госуслуг.

Запуск:  python3 scripts/fines_bot.py [--days N]
         python3 scripts/fines_bot.py --notify   (отправить найденное в Telegram)
         python3 scripts/fines_bot.py --debug    (показать сырые темы)
"""

import argparse
import imaplib
import json
import os
import re
import sqlite3
import sys
import email
from datetime import datetime, timedelta, timezone
from email.header import decode_header as email_decode_header
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, '..') if os.path.basename(SCRIPT_DIR) == 'scripts' else SCRIPT_DIR
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

FINE_WATCHERS = [
    {'label': 'zorea',   'user': 'zorea2001@mail.ru',     'pass_env': 'MAILRU_ZOREA_PASSWORD'},
    {'label': 'neutrinon', 'user': 'neutrinon@mail.ru',     'pass_env': 'MAILRU_NEUTRINON_PASSWORD'},
]

FINE_SENDERS = ['no-reply@gosuslugi.ru']

# Типы штрафных писем
# Ключевые слова для отбора писем (только настоящие штрафы/счета)
FINE_KEYWORDS = ['штраф', 'счёт на опл']

# Ключевые слова, которые НЕ являются штрафом (исключения)
NOT_FINE_KEYWORDS = [
    'егрн', 'кадастров', 'сведений из', 'предоставление публично',
    'узнайте подробности', 'результат ок', 'отзыв по про',
    'предоставление', 'годовой отчёт', 'ознакомьтесь со счетом',
]

FINE_TYPES = {
    'new': r'(?:новый\s+)?штраф(?:\s+от\s+гибдд|\s+гибдд)?$|наложен\s+штраф|вынесен\s+штраф|постановление',
    'fined': r'штраф\s+оплачен|оплачен\s+штраф',
    'paid': r'оплата\s+прошла\s+успешно',
    'bill': r'счёт\s+на\s+оплату',
    'cancelled': r'штраф\s+отмен[её]н|отмен[её]н\s+штраф',
}

# Парковочные ведомства (Москва)
PARKING_VENDORS = [
    'администратор московского парковочного пространства',
    'гку ампп',
    'московский скоростной диаметр',
    'мсд',
]

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def load_env():
    env_path = os.path.join(PROJECT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        k, v = line.split('=', 1)
                        os.environ[k] = v


def decode_subj(s):
    parts = email_decode_header(s)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            result.append(part)
    return ''.join(result)


def extract_html_body(msg) -> str:
    html = ''
    if msg.is_multipart():
        for p in msg.walk():
            ct = p.get_content_type()
            if ct == 'text/html' and p.get_payload(decode=True):
                html += p.get_payload(decode=True).decode('utf-8', errors='replace')
    else:
        pl = msg.get_payload(decode=True)
        if pl:
            html += pl.decode('utf-8', errors='replace')
    return html


def html_to_text(html: str) -> str:
    """Извлекает читаемый текст из HTML письма Госуслуг."""
    text = html
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    # Таблицы — каждую ячейку с новой строки
    text = re.sub(r'</?tr[^>]*>', '\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&[lg]t;', '', text)
    # Схлопываем пустые строки
    text = '\n'.join(l.strip() for l in text.split('\n') if l.strip())
    return text


def parse_fine_details(text: str, subject: str) -> dict:
    """Парсит детали штрафа из очищенного текста письма."""
    details = {
        'type': 'unknown',
        'number': None,
        'amount': None,
        'description': None,
        'vehicle': None,
        'date': None,
        'vendor': None,
        'sts': None,
    }

    # Тип штрафа
    for ftype, pattern in FINE_TYPES.items():
        if re.search(pattern, subject.lower()):
            details['type'] = ftype
            break

    # Номер штрафа
    m = re.search(r'№\s*(\d{10,30})', text)
    if m:
        details['number'] = m.group(1)

    # Сумма
    m = re.search(r'Сумма[:\s]*([\d\s]+)[\s₽]', text)
    if m:
        details['amount'] = float(m.group(1).replace(' ', '').replace('\u00a0', ''))
    if not details['amount']:
        m = re.search(r'([\d]+(?:[\s\u00a0]\d{3})*)\s*[₽]', text)
        if m:
            try:
                details['amount'] = float(m.group(1).replace(' ', '').replace('\u00a0', ''))
            except ValueError:
                pass

    # Описание нарушения
    m = re.search(r'(?:Назначение платежа|Комментарий|Информация\s+о\s+штрафе)[:\s]*([^\n]+)', text)
    if m:
        details['description'] = m.group(1).strip()
    if not details['description']:
        m = re.search(r'\d+\.\d+\s*ч\.?\d*\s*[-–]\s*(.+?)(?:\n|$)', text)
        if m:
            details['description'] = m.group(1).strip()[:100]

    # Транспортное средство
    m = re.search(r'номер\s+ТС\s+(\S+)', text, re.IGNORECASE)
    if m:
        details['vehicle'] = m.group(1)
    if not details['vehicle']:
        m = re.search(r'ТС[:\s]*(\S{5,10})', text, re.IGNORECASE)
        if m:
            details['vehicle'] = m.group(1)

    # Дата
    m = re.search(r'Дата\s+начисления[:\s]*([\d\-.:\s]+)', text)
    if m:
        details['date'] = m.group(1).strip()

    # Ведомство
    m = re.search(r'Ведомство[:\s]*([^\n]+)', text)
    if m:
        details['vendor'] = m.group(1).strip()

    # СТС
    m = re.search(r'СТС[:\s]*(\S+)', text, re.IGNORECASE)
    if m:
        details['sts'] = m.group(1)

    return details


def format_fine_for_bot(details: dict, mailbox: str) -> str:
    """Форматирует детали штрафа в сообщение для Telegram-бота."""
    emoji_map = {
        'new': '🆕',
        'fined': '✅',
        'paid': '💳',
        'bill': '📄',
        'cancelled': '🟢',
    }

    title_map = {
        'new': '🚨 Новый штраф',
        'fined': '✅ Штраф оплачен',
        'paid': '💳 Оплата прошла',
        'bill': '📄 Счёт на оплату',
        'cancelled': '🟢 Штраф отменён',
        'unknown': '📨 Уведомление Госуслуг',
    }

    # Определяем, парковка это или ГИБДД
    vendor_lower = (details.get('vendor') or '').lower()
    is_parking = any(pv in vendor_lower for pv in PARKING_VENDORS) or 'парковк' in (details.get('description') or '').lower()
    category = '🅿️ Парковка' if is_parking else '🚗 ГИБДД'

    lines = [
        f"{title_map.get(details['type'], '📨 Уведомление')} | {category}",
        f"📬 {mailbox}",
    ]

    if details.get('number'):
        lines.append(f"┃ № {details['number']}")
    if details.get('amount'):
        amount_str = f"{details['amount']:,.0f}".replace(',', ' ')
        lines.append(f"┃ Сумма: {amount_str} ₽")
    if details.get('description'):
        lines.append(f"┃ {details['description'][:60]}")
    if details.get('vehicle'):
        lines.append(f"┃ ТС: {details['vehicle']}")
    if details.get('date'):
        lines.append(f"┃ Дата: {details['date']}")
    if details.get('vendor'):
        lines.append(f"┃ {details['vendor'][:60]}")

    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# Core
# ─────────────────────────────────────────────────────────────

def _fetch_headers_batch(imap, uids):
    """Быстрая загрузка Subject + Date для списка UID одним IMAP-запросом."""
    if not uids:
        return []
    # IMAP fetch batch: uid1,uid2,uid3...
    batch = ','.join(uid.decode() if isinstance(uid, bytes) else str(uid) for uid in uids)
    _, data = imap.fetch(batch, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
    results = []
    for i in range(0, len(data), 2):
        raw = data[i][1].decode('utf-8', errors='replace')
        subject = ''
        date_str = ''
        for ln in raw.split('\n'):
            ln = ln.strip()
            if ln.lower().startswith('date:'):
                date_str = ln[5:].strip()
            elif ln.lower().startswith('subject:'):
                subject = decode_subj(ln[8:].strip())
        results.append({'subject': subject, 'date_str': date_str})
    return results


def fetch_fine_emails(days: int = 7) -> list[dict]:
    """Загружает письма о штрафах от Госуслуг за N дней (batch-запросы)."""
    all_fines = []
    since_str = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%d-%b-%Y')

    for watcher in FINE_WATCHERS:
        label = watcher['label']
        password = os.environ.get(watcher['pass_env'])
        if not password:
            print(f'  ⚠️  {label}: нет пароля ({watcher["pass_env"]})')
            continue

        try:
            imap = imaplib.IMAP4_SSL('imap.mail.ru', timeout=15)
            imap.login(watcher['user'], password)
            imap.select('INBOX')
        except Exception as e:
            print(f'  ❌ {label}: IMAP connect failed: {e}')
            continue

        for sender in FINE_SENDERS:
            try:
                status, ids = imap.search(None, 'FROM', sender, 'SINCE', since_str)
                all_ids = ids[0].split() if ids[0] else []
            except Exception as e:
                print(f'  ❌ {label}: search failed: {e}')
                continue

            if not all_ids:
                print(f'    📭 {sender}: нет писем за {days} дней')
                continue

            # Batch-загрузка заголовков
            headers = _fetch_headers_batch(imap, all_ids)

            # Отбираем только штрафные письма (исключая не-штрафы)
            fine_uids = []
            fine_headers = []
            for uid, hdr in zip(all_ids, headers):
                sl = hdr['subject'].lower()
                # Сначала проверяем исключения (не штраф)
                if any(kw in sl for kw in NOT_FINE_KEYWORDS):
                    continue
                # Потом проверяем — это штраф?
                if any(kw in sl for kw in FINE_KEYWORDS):
                    fine_uids.append(uid)
                    fine_headers.append(hdr)

            if not fine_uids:
                print(f'    📭 {label}/{sender}: нет штрафных писем за {days} дней')
                continue

            print(f'  📧 {label}/{sender}: {len(fine_uids)} штрафных писем за {days} дней')

            # Batch-загрузка тел
            batch = ','.join(uid.decode() if isinstance(uid, bytes) else str(uid) for uid in fine_uids)
            try:
                _, data = imap.fetch(batch, '(BODY.PEEK[])')
                bodies = {}
                for i in range(0, len(data), 2):
                    raw = data[i][1]
                    flag = data[i][0]
                    msg = email.message_from_bytes(raw)
                    html = extract_html_body(msg)
                    text = html_to_text(html)
                    bodies[len(bodies)] = text
            except Exception as e:
                print(f'    ❌ batch fetch failed: {e}')
                # fallback: по одному
                bodies = {}
                for uid in fine_uids:
                    try:
                        _, fd = imap.fetch(uid, '(BODY.PEEK[])')
                        msg = email.message_from_bytes(fd[0][1])
                        html = extract_html_body(msg)
                        text = html_to_text(html)
                        bodies[len(bodies)] = text
                    except Exception:
                        pass

            for idx, (uid, hdr) in enumerate(zip(fine_uids, fine_headers)):
                text = bodies.get(idx, '')
                details = parse_fine_details(text, hdr['subject'])
                details['raw_subject'] = hdr['subject'][:100]
                details['raw_date'] = hdr['date_str'][:30]
                details['mailbox'] = label
                details['uid'] = uid.decode() if isinstance(uid, bytes) else str(uid)

                all_fines.append(details)
                print(f'    {details["type"]:10s} | {str(details["amount"] or ""):>8s} | {(details["description"] or "-")[:50]}')

        imap.logout()

    return all_fines


def check_new_fines(days: int = 7) -> list[dict]:
    """Проверяет новые штрафы и записывает в БД, возвращает новые."""
    db_path = os.path.join(PROJECT_DIR, 'consumption.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Создам таблицу если нет
    c.execute('''CREATE TABLE IF NOT EXISTS fines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        number TEXT,
        amount REAL,
        description TEXT,
        vehicle TEXT,
        fine_date TEXT,
        vendor TEXT,
        sts TEXT,
        mailbox TEXT,
        raw_subject TEXT,
        raw_date TEXT,
        uid TEXT UNIQUE,
        detected_at TEXT DEFAULT (datetime('now')),
        notified_at TEXT,
        paid_confirmed_at TEXT
    )''')
    conn.commit()

    fines = fetch_fine_emails(days)
    new_fines = []

    for fine in fines:
        uid = fine.get('uid', '')
        if not uid:
            continue

        existing = c.execute('SELECT id FROM fines WHERE uid = ?', (uid,)).fetchone()
        if existing:
            fine['db_id'] = existing[0]
            continue

        c.execute('''INSERT OR IGNORE INTO fines
            (type, number, amount, description, vehicle, fine_date, vendor, sts,
             mailbox, raw_subject, raw_date, uid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (fine['type'], fine['number'], fine['amount'], fine['description'],
             fine['vehicle'], fine['date'], fine['vendor'], fine['sts'],
             fine['mailbox'], fine.get('raw_subject', ''), fine.get('raw_date', ''), uid))
        # Получаем ID свежевставленной записи
        fine['db_id'] = c.lastrowid
        new_fines.append(fine)

    conn.commit()
    conn.close()
    return new_fines


def notify_fines(fines: list[dict]):
    """Отправляет уведомления о штрафах через Telegram bot с кнопкой оплаты."""
    if not fines:
        return

    bot_token = os.environ.get('CONSUMPTION_BOT_TOKEN')
    chat_id = os.environ.get('OWNER_CHAT_ID', '1477860192')

    if not bot_token:
        print('⚠️  Нет CONSUMPTION_BOT_TOKEN — уведомление не отправлено')
        return

    try:
        import httpx
        client = httpx.Client(timeout=15)
    except ImportError:
        print('⚠️  Нет httpx — уведомление не отправлено')
        return

    for fine in fines:
        msg = format_fine_for_bot(fine, fine.get('mailbox', ''))

        # Получаем ID штрафа из БД (нужен для callback)
        fine_id = fine.get('db_id')

        # Кнопка оплаты (только для новых штрафов)
        keyboard = None
        if fine['type'] in ('new', 'bill') and fine_id:
            keyboard = {
                'inline_keyboard': [[
                    {'text': '✅ Оплачено', 'callback_data': f'fine_paid:{fine_id}'}
                ]]
            }

        try:
            payload = {'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'}
            if keyboard:
                payload['reply_markup'] = keyboard
            r = client.post(
                f'https://api.telegram.org/bot{bot_token}/sendMessage',
                json=payload
            )
            if r.status_code == 200:
                print(f'  ✅ Уведомление отправлено: {fine["type"]} {fine["amount"]}₽')
                # Отмечаем, что уведомление отправлено
                _mark_notified(fine.get('uid'), f'text&button' if keyboard else 'text')
            else:
                print(f'  ⚠️  Ошибка отправки: {r.text[:100]}')
        except Exception as e:
            print(f'  ⚠️  Ошибка HTTP: {e}')


def _mark_notified(uid: str, method: str = 'text'):
    """Отмечает штраф как уведомлённый в БД."""
    if not uid:
        return
    db_path = os.path.join(PROJECT_DIR, 'consumption.db')
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('UPDATE fines SET notified_at = datetime(\'now\') WHERE uid = ?', (uid,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def print_report(fines: list[dict]):
    """Выводит отчёт в читаемом виде."""
    if not fines:
        print('Нет новых штрафов за указанный период.')
        return

    print(f'\n{"="*50}')
    print(f'📋 ШТРАФЫ И СЧЕТА ({len(fines)} шт.)')
    print(f'{"="*50}')
    print()

    for fine in fines:
        msg = format_fine_for_bot(fine, fine.get('mailbox', ''))
        print(msg)
        print()


def main():
    parser = argparse.ArgumentParser(description='Мониторинг штрафов из Госуслуг')
    parser.add_argument('--days', type=int, default=7, help='Количество дней для проверки (по умолч. 7)')
    parser.add_argument('--notify', action='store_true', help='Отправить уведомление в Telegram')
    parser.add_argument('--debug', action='store_true', help='Показать сырые темы писем')
    args = parser.parse_args()

    load_env()

    if args.debug:
        # Режим отладки: просто показать темы
        for watcher in FINE_WATCHERS:
            password = os.environ.get(watcher['pass_env'])
            if not password:
                continue
            imap = imaplib.IMAP4_SSL('imap.mail.ru')
            imap.login(watcher['user'], password)
            imap.select('INBOX')
            for sender in FINE_SENDERS:
                status, ids = imap.search(None, 'FROM', sender)
                all_ids = ids[0].split() if ids[0] else []
                print(f'{watcher["label"]}: {len(all_ids)} писем от {sender}')
                for uid in all_ids[-20:]:
                    _, fd = imap.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
                    raw = fd[0][1].decode('utf-8', errors='replace')
                    date = ''
                    subject = ''
                    for ln in raw.split('\n'):
                        ln = ln.strip()
                        if ln.lower().startswith('date:'): date = ln[5:].strip()
                        elif ln.lower().startswith('subject:'): subject = decode_subj(ln[8:].strip())
                    fine_type = ''
                    for ftype, pattern in FINE_TYPES.items():
                        if re.search(pattern, subject.lower()):
                            fine_type = ftype
                            break
                    print(f'  [{date}] {subject[:80]}')
            imap.logout()
        return

    print(f'🔍 Проверка штрафов за последние {args.days} дней...')
    print()

    new_fines = check_new_fines(args.days)
    print()
    print_report(new_fines)

    if args.notify:
        print(f'\n📨 Отправка уведомлений...')
        notify_fines(new_fines)


if __name__ == '__main__':
    main()

# ─────────────────────────────────────────────────────────────
# 6. Usage (for SKILL.md)
# ─────────────────────────────────────────────────────────────
# Запуск:
#   python3 scripts/fines_bot.py --days 7          показать штрафы за 7 дней
#   python3 scripts/fines_bot.py --days 30         показать за 30 дней
#   python3 scripts/fines_bot.py --days 7 --notify отправить новые в Telegram
#   python3 scripts/fines_bot.py --debug           показать темы писем
#
# Для cron:
#   ./check_fines.sh
#
# Типы писем:
#   "У вас новый штраф" / "Штраф от ГИБДД" — новое постановление
#   "Штраф оплачен" — подтверждение оплаты
#   "Оплата прошла успешно" — факт оплаты
#   "Счёт на оплату" — счёт (МСД/парковки)
#   "Штраф отменён" — отмена постановления
#
# Парсинг: извлекает номер, сумму, статью, ТС, дату, ведомство
# Хранение: таблица fines в consumption.db (с дедупликацией по uid)
