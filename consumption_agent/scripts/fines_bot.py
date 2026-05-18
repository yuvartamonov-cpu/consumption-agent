#!/usr/bin/env python3
"""
Fines Bot — мониторинг штрафов (ГИБДД, парковки, МСД, платные дороги)
из писем Госуслуг и Автодора на всех почтах + SMS Phone Link.

Запуск:
  python3 scripts/fines_bot.py --days 7              показать за 7 дней
  python3 scripts/fines_bot.py --days 7 --notify     отправить новые в Telegram
  python3 scripts/fines_bot.py --summary             обязательный отчёт в 18:00
  python3 scripts/fines_bot.py --debug               сырые темы
"""

import argparse
import glob as glob_mod
import imaplib
import json
import os
import re
import shutil
import sqlite3
import sys
import email
import tempfile
from datetime import datetime, timedelta, timezone
from email.header import decode_header as email_decode_header
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, '..') if os.path.basename(SCRIPT_DIR) == 'scripts' else SCRIPT_DIR
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import logging
from consumption.db import connect as db_connect
from imap_folders import ScanMetrics, build_message_uid, discover_target_mailboxes

_log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

FINE_WATCHERS = [
    {'label': 'zorea',     'imap': 'imap.mail.ru',   'user': 'zorea2001@mail.ru',          'pass_env': 'MAILRU_ZOREA_PASSWORD'},
    {'label': 'neutrinon', 'imap': 'imap.mail.ru',   'user': 'neutrinon@mail.ru',          'pass_env': 'MAILRU_NEUTRINON_PASSWORD'},
    {'label': 'gmail',     'imap': 'imap.gmail.com',  'user': 'yu.v.artamonov@gmail.com',   'pass_env': 'GMAIL_APP_PASSWORD'},
    {'label': 'yandex',    'imap': 'imap.yandex.ru',  'user': 'HKID2021@yandex.ru',         'pass_env': 'YANDEX_APP_PASSWORD'},
]

FINE_SENDERS = [
    'no-reply@gosuslugi.ru',
    'info@news.avtodor-tr.ru',
    'info@send.avtodor-tr.ru',
]

FINE_KEYWORDS = ['штраф', 'счёт на опл', 'цкад', 'платн', 'автодор', 'м-12', 'м12']

NOT_FINE_KEYWORDS = [
    'егрн', 'кадастров', 'сведений из', 'предоставление публично',
    'узнайте подробности', 'результат ок', 'отзыв по про',
    'предоставление', 'годовой отчёт', 'ознакомьтесь со счетом',
    'test domain', 'спам', 'реклама',
]

FINE_TYPES = {
    'new': r'(?:новый\s+)?штраф(?:\s+от\s+гибдд|\s+гибдд)?$|наложен\s+штраф|вынесен\s+штраф|постановление',
    'fined': r'штраф\s+оплачен|оплачен\s+штраф',
    'paid': r'оплата\s+прошла\s+успешно',
    'bill': r'счёт\s+на\s+оплату',
    'cancelled': r'штраф\s+отмен[её]н|отмен[её]н\s+штраф',
}

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
    text = html
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'</?tr[^>]*>', '\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&[lg]t;', '', text)
    text = '\n'.join(l.strip() for l in text.split('\n') if l.strip())
    return text


def parse_fine_details(text: str, subject: str) -> dict:
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

    for ftype, pattern in FINE_TYPES.items():
        if re.search(pattern, subject.lower()):
            details['type'] = ftype
            break

    m = re.search(r'№\s*(\d{10,30})', text)
    if m:
        details['number'] = m.group(1)

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

    m = re.search(r'(?:Назначение платежа|Комментарий|Информация\s+о\s+штрафе)[:\s]*([^\n]+)', text)
    if m:
        details['description'] = m.group(1).strip()
    if not details['description']:
        m = re.search(r'\d+\.\d+\s*ч\.?\d*\s*[-–]\s*(.+?)(?:\n|$)', text)
        if m:
            details['description'] = m.group(1).strip()[:100]

    m = re.search(r'номер\s+ТС\s+(\S+)', text, re.IGNORECASE)
    if m:
        details['vehicle'] = m.group(1)
    if not details['vehicle']:
        m = re.search(r'ТС[:\s]*(\S{5,10})', text, re.IGNORECASE)
        if m:
            details['vehicle'] = m.group(1)

    m = re.search(r'Дата\s+начисления[:\s]*([\d\-.:\s]+)', text)
    if m:
        details['date'] = m.group(1).strip()

    m = re.search(r'Ведомство[:\s]*([^\n]+)', text)
    if m:
        details['vendor'] = m.group(1).strip()

    m = re.search(r'СТС[:\s]*(\S+)', text, re.IGNORECASE)
    if m:
        details['sts'] = m.group(1)

    return details


# ─────────────────────────────────────────────────────────────
# IMAP
# ─────────────────────────────────────────────────────────────

def _fetch_headers_batch(imap, uids):
    if not uids:
        return []
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
    all_fines = []
    since_str = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%d-%b-%Y')

    for watcher in FINE_WATCHERS:
        label = watcher['label']
        password = os.environ.get(watcher['pass_env'], '').replace('"', '').replace(' ', '')
        if not password:
            print(f'  -- {label}: нет пароля')
            continue

        metrics = ScanMetrics(scanner='fines_bot', account=label).start()

        imap_host = watcher.get('imap', 'imap.mail.ru')
        try:
            imap = imaplib.IMAP4_SSL(imap_host, timeout=15)
            imap.login(watcher['user'], password)
        except Exception as e:
            print(f'  XX {label}: IMAP failed: {e}')
            metrics.errors += 1
            metrics.stop().log_summary(_log)
            continue

        mailboxes = discover_target_mailboxes(imap, account_label=label)
        seen_ids = set()

        for mailbox_name in mailboxes:
            folder_seen = 0
            folder_deduped = 0
            folder_parsed = 0
            folder_error = None

            try:
                status, _ = imap.select(f'"{mailbox_name}"', readonly=True)
                if status != 'OK':
                    folder_error = f'SELECT failed: {status}'
                    print(f'  XX {label}: cannot open {mailbox_name}')
                    metrics.record_folder(mailbox_name, error=folder_error)
                    continue
            except Exception as e:
                folder_error = str(e)
                print(f'  XX {label}: cannot open {mailbox_name}: {e}')
                metrics.record_folder(mailbox_name, error=folder_error)
                continue

            for sender in FINE_SENDERS:
                try:
                    status, ids = imap.search(None, 'FROM', sender, 'SINCE', since_str)
                    all_ids = ids[0].split() if ids and ids[0] else []
                except Exception as e:
                    print(f'  XX {label}/{mailbox_name}: search failed: {e}')
                    continue

                if not all_ids:
                    continue

                folder_seen += len(all_ids)

                headers = _fetch_headers_batch(imap, all_ids)
                fine_uids = []
                fine_headers = []
                for uid, hdr in zip(all_ids, headers):
                    sl = hdr['subject'].lower()
                    if any(kw in sl for kw in NOT_FINE_KEYWORDS):
                        continue
                    if any(kw in sl for kw in FINE_KEYWORDS):
                        fine_uids.append(uid)
                        fine_headers.append(hdr)

                if not fine_uids:
                    continue

                print(f'  {label}/{mailbox_name}/{sender}: {len(fine_uids)} писем')

                for uid, hdr in zip(fine_uids, fine_headers):
                    try:
                        _, fd = imap.fetch(uid, '(BODY.PEEK[])')
                        msg = email.message_from_bytes(fd[0][1])
                        dedup_key = build_message_uid(msg.get('Message-ID', ''), label, mailbox_name, uid)
                        if dedup_key in seen_ids:
                            folder_deduped += 1
                            continue
                        seen_ids.add(dedup_key)

                        html = extract_html_body(msg)
                        text = html_to_text(html)
                        details = parse_fine_details(text, hdr['subject'])
                        details['raw_subject'] = hdr['subject'][:100]
                        details['raw_date'] = hdr['date_str'][:30]
                        details['mailbox'] = f'{label}/{mailbox_name}'
                        details['uid'] = dedup_key
                        all_fines.append(details)
                        folder_parsed += 1
                    except Exception as e:
                        print(f'    ошибка письма {label}/{mailbox_name}: {e}')
                        continue

            metrics.record_folder(mailbox_name, seen=folder_seen,
                                  deduped=folder_deduped, parsed=folder_parsed)

        imap.logout()
        metrics.stop().log_summary(_log)

    return all_fines


# ─────────────────────────────────────────────────────────────
# SMS Phone Link check
# ─────────────────────────────────────────────────────────────

def check_sms_fines():
    sms_fines = []
    db_glob = "/mnt/c/Users/*/AppData/Local/Packages/Microsoft.YourPhone_8wekyb3d8bbwe/LocalCache/Indexed/*/System/Database/phone.db"
    files = glob_mod.glob(db_glob)
    if not files:
        return sms_fines
    for phone_db_path in files:
        if not os.path.exists(phone_db_path):
            continue
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
            shutil.copy2(phone_db_path, tmp.name)
            # Allowed raw sqlite3.connect exception: copied Phone Link DB, not consumption.db.
            conn = sqlite3.connect(tmp.name)
            rows = conn.execute("""
                SELECT body, from_address, timestamp FROM message
                WHERE body LIKE '%шраф%'
                   OR body LIKE '%гибдд%'
                   OR body LIKE '%платная дорога%'
                   OR body LIKE '%цкад%'
                   OR body LIKE '%автодор%'
                   OR body LIKE '%нарушение%'
                   OR body LIKE '%постановление%'
                   OR body LIKE '%пдд%'
                   OR body LIKE '%ам пп%'
                ORDER BY timestamp DESC LIMIT 10
            """).fetchall()
            conn.close()
            os.unlink(tmp.name)
            for body, sender, ts in rows:
                sms_fines.append({
                    'source': 'SMS',
                    'sender': sender,
                    'body': body[:100],
                    'timestamp': ts,
                })
        except Exception as e:
            print(f'  ошибка SMS ({phone_db_path}): {e}')
    return sms_fines


# ─────────────────────────────────────────────────────────────
# DB
# ─────────────────────────────────────────────────────────────

def check_new_fines(days: int = 7) -> list[dict]:
    db_path = os.path.join(PROJECT_DIR, 'consumption.db')
    conn = db_connect(db_path)
    c = conn.cursor()

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
        fine['db_id'] = c.lastrowid
        new_fines.append(fine)

    conn.commit()
    conn.close()
    return new_fines


# ─────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────

def format_fine_for_bot(details: dict, mailbox: str) -> str:
    title_map = {
        'new': '🆕 Новый штраф',
        'fined': '✅ Штраф оплачен',
        'paid': '✅ Оплата прошла',
        'bill': '📄 Счёт на оплату',
        'cancelled': '❌ Штраф отменён',
        'unknown': '📋 Уведомление',
    }

    vendor_lower = (details.get('vendor') or '').lower()
    is_parking = any(pv in vendor_lower for pv in PARKING_VENDORS) or 'parkovk' in (details.get('description') or '').lower()
    category = '🅿️ Парковка' if is_parking else '🚦 ГИБДД'

    lines = [
        f"{title_map.get(details['type'], '📋 Уведомление')} | {category}",
        f"📬 Почта: {mailbox}",
    ]

    if details.get('number'):
        lines.append(f"№ {details['number']}")
    if details.get('amount'):
        amount_str = f"{details['amount']:,.0f}".replace(',', ' ')
        lines.append(f"💰 Сумма: {amount_str} ₽")
    if details.get('description'):
        lines.append(f"{details['description'][:60]}")
    if details.get('vehicle'):
        lines.append(f"🚗 ТС: {details['vehicle']}")
    if details.get('date'):
        lines.append(f"📅 Дата: {details['date']}")
    if details.get('vendor'):
        lines.append(f"{details['vendor'][:60]}")

    return '\n'.join(lines)


def format_summary_for_bot(new_fines, all_fines_in_db):
    lines = ['📋 ЕЖЕДНЕВНАЯ ПРОВЕРКА ШТРАФОВ']
    lines.append(datetime.now().strftime('%d.%m.%Y %H:%M'))
    lines.append('')

    if new_fines:
        lines.append(f'🆕 НОВЫЕ ({len(new_fines)}):')
        for f in new_fines:
            mb = f.get('mailbox', '')
            amt = f.get('amount', 0)
            desc = (f.get('description') or '')[:40]
            amt_s = f'{amt:,.0f}'.replace(',', ' ') if amt else '?'
            lines.append(f'  - {mb}: {amt_s} ₽ — {desc}')
    else:
        lines.append('✅ Новых штрафов нет')
    lines.append('')

    unpaid = [f for f in all_fines_in_db if f.get('type') in ('new', 'bill')]
    if unpaid:
        lines.append(f'🔴 Неоплаченные ({len(unpaid)}):')
        for f in unpaid:
            mb = f.get('mailbox', '')
            amt = f.get('amount', 0)
            desc = (f.get('description') or '')[:40]
            d = (f.get('fine_date') or f.get('raw_date', ''))[:10]
            amt_s = f'{amt:,.0f}'.replace(',', ' ') if amt else '?'
            lines.append(f'  - [{d}] {mb}: {amt_s} ₽ — {desc}')
        lines.append('')

    lines.append('📫 Проверенные почты:')
    for w in FINE_WATCHERS:
        lines.append(f'  - {w["label"]} ({w["user"]})')

    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# Notification
# ─────────────────────────────────────────────────────────────

def notify_fines(fines: list[dict]):
    if not fines:
        return
    bot_token = os.environ.get('CONSUMPTION_BOT_TOKEN')
    chat_id = os.environ.get('OWNER_CHAT_ID', '1477860192')
    if not bot_token:
        print('Нет токена')
        return
    try:
        import httpx
        client = httpx.Client(timeout=15)
    except ImportError:
        return

    for fine in fines:
        msg = format_fine_for_bot(fine, fine.get('mailbox', ''))
        fine_id = fine.get('db_id')
        keyboard = None
        if fine['type'] in ('new', 'bill') and fine_id:
            keyboard = {
                'inline_keyboard': [[
                    {'text': '✅ Оплачено', 'callback_data': f'fine_paid:{fine_id}'}
                ]]
            }
        try:
            payload = {'chat_id': chat_id, 'text': msg}
            if keyboard:
                payload['reply_markup'] = keyboard
            r = client.post(f'https://api.telegram.org/bot{bot_token}/sendMessage', json=payload)
            if r.status_code == 200:
                _mark_notified(fine.get('uid'))
        except Exception:
            pass


def send_telegram_msg(text):
    bot_token = os.environ.get('CONSUMPTION_BOT_TOKEN')
    chat_id = os.environ.get('OWNER_CHAT_ID', '1477860192')
    if not bot_token:
        print('Нет токена')
        return
    try:
        import httpx
        r = httpx.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': text},
            timeout=15
        )
        if r.status_code == 200:
            print('Отправлено')
        else:
            print(f'Ошибка: {r.text[:100]}')
    except Exception as e:
        print(f'Ошибка HTTP: {e}')


def _mark_notified(uid: str):
    if not uid:
        return
    db_path = os.path.join(PROJECT_DIR, 'consumption.db')
    try:
        conn = db_connect(db_path)
        conn.execute('UPDATE fines SET notified_at = datetime(\'now\') WHERE uid = ?', (uid,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Fines monitor')
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--notify', action='store_true', help='Notify about new')
    parser.add_argument('--summary', action='store_true', help='Daily summary (always sends)')
    parser.add_argument('--check-sms', action='store_true', help='Check SMS Phone Link')
    parser.add_argument('--debug', action='store_true', help='Raw subjects')
    args = parser.parse_args()

    load_env()

    if args.debug:
        for watcher in FINE_WATCHERS:
            password = os.environ.get(watcher['pass_env'], '').replace('"', '').replace(' ', '')
            if not password:
                continue
            imap_host = watcher.get('imap', 'imap.mail.ru')
            imap = imaplib.IMAP4_SSL(imap_host)
            imap.login(watcher['user'], password)
            mailboxes = discover_target_mailboxes(imap)
            for mailbox_name in mailboxes:
                status, _ = imap.select(f'"{mailbox_name}"', readonly=True)
                if status != 'OK':
                    continue
                for sender in FINE_SENDERS:
                    status, ids = imap.search(None, 'FROM', sender)
                    all_ids = ids[0].split() if ids[0] else []
                    print(f'{watcher["label"]}/{mailbox_name}: {len(all_ids)} от {sender}')
                    for uid in all_ids[-10:]:
                        _, fd = imap.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
                        raw = fd[0][1].decode('utf-8', errors='replace')
                        date = ''
                        subject = ''
                        for ln in raw.split('\n'):
                            ln = ln.strip()
                            if ln.lower().startswith('date:'): date = ln[5:].strip()
                            elif ln.lower().startswith('subject:'): subject = decode_subj(ln[8:].strip())
                        print(f'  [{date}] {subject[:80]}')
            imap.logout()
        return

    print(f'Проверка штрафов за {args.days} дней...')
    new_fines = check_new_fines(args.days)

    if args.check_sms:
        print('Проверка SMS...')
        for s in check_sms_fines():
            new_fines.append({
                'type': 'new', 'amount': None,
                'description': f'SMS: {s["body"][:80]}',
                'mailbox': f'SMS ({s["sender"]})',
                'uid': f'sms_{s["timestamp"]}_{s["sender"]}',
            })

    if args.summary:
        all_in_db = []
        db_path = os.path.join(PROJECT_DIR, 'consumption.db')
        try:
            conn = db_connect(db_path)
            rows = conn.execute('SELECT type,number,amount,description,mailbox,fine_date,raw_date FROM fines ORDER BY detected_at DESC').fetchall()
            for r in rows:
                all_in_db.append(dict(zip(['type','number','amount','description','mailbox','fine_date','raw_date'], r)))
            conn.close()
        except Exception as e:
            print(f'Ошибка БД: {e}')
        msg = format_summary_for_bot(new_fines, all_in_db)
        print(msg)
        print('\nОтправка...')
        send_telegram_msg(msg)
    else:
        if new_fines:
            for f in new_fines:
                print(format_fine_for_bot(f, f.get('mailbox', '')))
                print()
        else:
            print('Новых штрафов нет')

        if args.notify:
            print('Отправка уведомлений...')
            notify_fines(new_fines)


if __name__ == '__main__':
    main()
