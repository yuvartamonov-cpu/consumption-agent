#!/usr/bin/env python3
"""
Consumption Agent — ежедневное сканирование всех источников на чеки и расходы (23:30).
Источники:
  1. Gmail (yu.v.artamonov@gmail.com)
  2. Yandex (HKID2021@yandex.ru)
  3. Mail.ru Zorea (zorea@mail.ru)
  4. Mail.ru Neutrinon (neutrinon@mail.ru)
  5. SMS с двух телефонов через Phone Link /mnt/c/...
При нахождении чеков — добавляет запись в purchases.
"""

import imaplib
import email
import sqlite3
import os
import re
import sys
import shutil
import glob
import tempfile
import logging
import smtplib
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from bs4 import BeautifulSoup
from imap_folders import build_message_uid, discover_target_mailboxes
from purchase_dedup import (
    build_delivery_note,
    build_time_note,
    canonical_store_name,
    email_event_details,
    is_duplicate_purchase,
    normalize_purchase_date,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'consumption.db')

os.makedirs(os.path.join(SCRIPT_DIR, 'logs'), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s — %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(SCRIPT_DIR, 'logs/daily_cheque_scan.log'), mode='a')
    ]
)
log = logging.getLogger(__name__)

# ============================================================
# КОНФИГУРАЦИЯ ПОЧТ
# ============================================================
IMAP_CONFIGS = [
    {
        'name': 'Gmail',
        'host': 'imap.gmail.com',
        'user': 'yu.v.artamonov@gmail.com',
        'password_env': 'GMAIL_APP_PASSWORD',
        'password_fallback': 'kzjjirsrhcsmptoc',  # уже без пробелов
    },
    {
        'name': 'Yandex',
        'host': 'imap.yandex.ru',
        'user': 'HKID2021@yandex.ru',
        'password_env': 'YANDEX_APP_PASSWORD',
        'password_fallback': 'jmwegtxlztunrwua',
    },
    {
        'name': 'Mail.ru Zorea',
        'host': 'imap.mail.ru',
        'user': 'zorea2001@mail.ru',
        'password_env': 'MAILRU_ZOREA_PASSWORD',
        'password_fallback': 'ItawCaAqpeDntsL1Xeif',
    },
    {
        'name': 'Mail.ru Neutrinon',
        'host': 'imap.mail.ru',
        'user': 'neutrinon@mail.ru',
        'password_env': 'MAILRU_NEUTRINON_PASSWORD',
        'password_fallback': 'h8IXeNvXwV6aF9NdmIxY',
    },
]

# Магазины для автоопределения: (ключевые_слова, store_name)
TARGET_SENDERS = [
    (['ozon', 'sender.ozon.ru'], 'Ozon'),
    (['wildberries', 'wb.ru', 'wildberries.ru'], 'Wildberries'),
    (['я.маркет', 'yandex.market', 'market.yandex'], 'Яндекс Маркет'),
    (['cбермаркет', 'sbermarket'], 'СберМаркет'),
    (['куш', 'кушай'], 'Кушай на районе'),
    (['лавка', 'lavka'], 'Яндекс Лавка'),
    (['самокат', 'samokat.ru', 'умный ритейл'], 'Самокат'),
    (['магнит'], 'Магнит'),
    (['пятёрочка', '5ka', 'pyaterochka'], 'Пятёрочка'),
    (['вкусвилл', 'vkusvill'], 'Вкусвилл'),
    (['ашан', 'auchan'], 'Ашан'),
    (['metro'], 'METRO'),
    (['kfc', 'кфс'], 'KFC'),
    (['вкусно и точка', 'vkusnotochka'], 'Вкусно — и точка'),
    (['burger king'], 'Burger King'),
    (['китчен', 'kitchen', 'кухня на районе'], 'Яндекс Кухня'),
    (['я.еда', 'ядекс еда', 'yandex.food', 'eda.yandex'], 'Яндекс Еда'),
    (['я.плюс', 'yandex plus'], 'Яндекс Плюс'),
    (['юандекс'], 'Яндекс'),
    # Штрафы и госуслуги
    (['gosuslugi', 'госуслуг'], 'Госуслуги'),
    (['shtraf', 'гибдд', 'gibdd'], 'Штраф ГИБДД'),
    (['parking', 'ампп', 'мсд', 'московский скоростной'], 'Парковка / МСД'),
    # Платные дороги
    (['avtodor', 'автодор', 'tskk', 'цкк', 'платн'], 'Платные дороги'),
    (['transit', 'транзит', 'электронная накладная'], 'Платные дороги'),
]

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def get_password(config):
    pwd = os.environ.get(config['password_env'], '') or config.get('password_fallback', '')
    return pwd.replace('"', '').replace(' ', '')


def decode_mime(s):
    if not s: return ''
    parts = decode_header(s)
    return ''.join(
        part.decode(charset or 'utf-8', errors='replace') if isinstance(part, bytes) else part
        for part, charset in parts
    )


def normalize_sender(from_val):
    m = re.search(r'<([^>]+)>', from_val)
    return (m.group(1) if m else from_val).lower().strip()


def is_already_imported(conn, date_str, amount, store_name, *, event_time=None, email_msg_id=None, delivery_fee=None):
    return is_duplicate_purchase(
        conn,
        date_str,
        amount,
        store_name,
        event_time=event_time,
        email_msg_id=email_msg_id,
        delivery_fee=delivery_fee,
    )


def add_purchase(conn, date_str, total_amount, store_name, items, source_name, notes_suffix='', payment_method='card', email_msg_id=None):
    if not date_str or not total_amount or not store_name:
        return None
    d = normalize_purchase_date(date_str)
    store_name = canonical_store_name(store_name)
    
    item_names = [it[0] if isinstance(it, (list, tuple)) else it for it in items[:6]]
    if len(items) > 6:
        item_names.append(f"... +{len(items) - 6}")
    notes = f"{store_name}: {', '.join(item_names)}"
    if notes_suffix:
        notes += f" ({notes_suffix})"

    try:
        cur = conn.execute("""
            INSERT INTO purchases (purchase_date, total_amount, payment_method, source, store_name, notes, email_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (d, float(total_amount), payment_method, source_name, store_name, notes[:500], email_msg_id))
        pid = cur.lastrowid
        log.info(f"   ✅ {d} | {float(total_amount):.0f} ₽ | {store_name} ({len(items)} товаров)")
        return pid
    except Exception as e:
        log.error(f"   ❌ Ошибка: {e}")
        return None


# ============================================================
# ПАРСИНГ ЧЕКОВ
# ============================================================

def parse_ofd_cheque(html):
    """Парсит HTML чека Платформа ОФД или Яндекс Чека."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    text = soup.get_text(separator='\n')
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    res = {'store': None, 'date': None, 'total': None, 'delivery': None, 'org': '', 'items': [], 'is_cheque': False}
    
    if 'кассовый чек' in text.lower():
        res['is_cheque'] = True
    if 'check.yandex' in html:
        res['is_cheque'] = True
    
    if not res['is_cheque']:
        # может быть письмо Яндекс Плюс
        if 'яндекс плюс' in text.lower() or 'yandex plus' in html.lower():
            res['store'] = 'Яндекс Плюс'
        # письмо от магазина (Wildberries, Ozon) — отдаём без парсинга
        return res
    
    # Дата
    for line in lines:
        m = re.search(r'(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})', line)
        if m:
            res['date'] = m.group(1); break
    
    # ИТОГ
    for i, line in enumerate(lines):
        if line in ('ИТОГ', 'Итого', 'Сколько'):
            for j in range(i+1, min(i+5, len(lines))):
                m = re.search(r'([\d\s]+[.,]?\d*)', lines[j])
                if m:
                    res['total'] = m.group(1).strip(); break
            if res['total']: break

    # Доставка / сервисный сбор
    for i, line in enumerate(lines):
        lower = line.lower()
        if not any(token in lower for token in ('доставк', 'курьер', 'service fee', 'сервисный сбор')):
            continue
        candidates = [line]
        candidates.extend(lines[i + 1:i + 4])
        for candidate in candidates:
            m = re.search(r'(\d[\d\s]*[.,]\d{1,2}|\d[\d\s]*)', candidate)
            if not m:
                continue
            try:
                value = float(m.group(1).replace(' ', '').replace(',', '.'))
            except Exception:
                continue
            if value > 0:
                res['delivery'] = value
                break
        if res['delivery']:
            break
    
    # Организация → магазин
    for line in lines:
        if 'www.samokat.ru' in line: res['store'] = 'Самокат'; break
        if 'stoloto.ru' in line: res['store'] = 'Столото'; break
        if 'УМНЫЙ РЕТЕЙЛ' in text or 'УМНЫЙ РИТЕЙЛ' in text: res['store'] = 'Самокат'; break
    
    return res


def extract_amount_from_body(body, html):
    """Извлекает сумму из тела письма (для магазинов без ОФД)."""
    text = f"{body} {html}"
    patterns = [
        r'(?:Итого|Сумма|К? ?оплате|Всего|Order total)[:\s]*([\d\s]+[.,]?\d*)\s*(?:₽|руб|р\.|rub)',
        r'(?:к оплате|оплачено)[:\s]*([\d\s]+[.,]?\d*)\s*(?:₽|руб|р\.)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try: return float(m.group(1).replace(' ', '').replace(',', '.'))
            except: pass
    
    # просто первое число с ₽
    nums = re.findall(r'([\d\s]+[.,]?\d*)\s*(?:₽|руб|р\.)', text)
    if nums:
        try:
            parsed = [float(n.replace(' ', '').replace(',', '.')) for n in nums if n.strip()]
            return max(parsed) if parsed else None
        except: pass
    return None


# ============================================================
# СКАНИРОВАНИЕ ПОЧТЫ
# ============================================================

def scan_mailbox(config, conn):
    password = get_password(config)
    if not password:
        log.warning(f"   ⚠️ Нет пароля для {config['name']}")
        return 0
    
    added = 0
    log.info(f"📧 {config['name']} ({config['user']})...")
    
    try:
        imap = imaplib.IMAP4_SSL(config['host'], timeout=30)
        imap.login(config['user'], password)
        mailboxes = discover_target_mailboxes(imap)
        log.info(f"   Папки: {', '.join(mailboxes)}")
        seen_ids = set()

        today_str = datetime.now().strftime('%d-%b-%Y')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%d-%b-%Y')
        scanned_any = False

        for mailbox_name in mailboxes:
            try:
                status, _ = imap.select(f'"{mailbox_name}"', readonly=True)
                if status != 'OK':
                    log.warning(f"   ⚠️ Не удалось открыть папку {mailbox_name}")
                    continue
            except Exception as e:
                log.warning(f"   ⚠️ Не удалось открыть папку {mailbox_name}: {e}")
                continue

            result, data = imap.search(None, f'(ON {today_str})')
            ids = data[0].split() if data and data[0] else []

            if not ids:
                result, data = imap.search(None, f'(ON {yesterday})')
                ids = data[0].split() if data and data[0] else []

            if not ids:
                continue

            scanned_any = True
            log.info(f"   {mailbox_name}: {len(ids)} писем")

            for num in ids:
                try:
                    _, msg_data = imap.fetch(num, '(RFC822)')
                    msg = email.message_from_bytes(msg_data[0][1])
                    dedup_key = build_message_uid(msg.get('Message-ID', ''), config['name'], mailbox_name, num)
                    if dedup_key in seen_ids:
                        continue
                    seen_ids.add(dedup_key)
                
                    from_val = msg.get('From', '')
                    subj_val = decode_mime(msg.get('Subject', ''))
                    sender_email = normalize_sender(from_val)

                    # Пропускаем письма от самого consumption_agent (отчёты, уведомления)
                    if any(skip in (subj_val + ' ' + from_val + ' ' + sender_email).lower() for skip in ['отчёт о расходах', 'собираем чемоданы', 'consumption agent', 'consumption_agent']):
                        log.info(f"   ⏭ Пропущено (self): {subj_val[:60]}")
                        continue

                    # Извлекаем тело
                    body = ''
                    html = ''
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            try:
                                pl = part.get_payload(decode=True)
                                if pl:
                                    d = pl.decode('utf-8', errors='replace')
                                    if ct == 'text/html': html += d
                                    elif ct == 'text/plain': body += d
                            except: pass
                    else:
                        try:
                            pl = msg.get_payload(decode=True)
                            if pl: body += pl.decode('utf-8', errors='replace')
                        except: pass

                    full_text = f"{from_val} {subj_val} {body} {html}".lower()
                    
                    store_name = None
                    total = None
                    delivery_fee = None
                    items = []
                    # Парсинг даты из Email-заголовка
                    raw_date = msg.get('Date', '')
                    date_str = ''
                    if raw_date:
                        for fmt in ['%d %b %Y', '%Y-%m-%d', '%d.%m.%Y']:
                            try:
                                parsed = datetime.strptime(raw_date[:11], fmt)
                                date_str = parsed.strftime('%Y-%m-%d')
                                break
                            except:
                                pass
                        if not date_str:
                            # Пробуем другие форматы
                            m = re.search(r'(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})', raw_date)
                            if m:
                                try:
                                    parsed = datetime.strptime(m.group(1), '%d %b %Y')
                                    date_str = parsed.strftime('%Y-%m-%d')
                                except: pass
                        if not date_str:
                            date_str = datetime.now().strftime('%Y-%m-%d')
                    else:
                        date_str = datetime.now().strftime('%Y-%m-%d')

                    # 1. Платформа ОФД — детальный парсинг чека
                    if 'chek.pofd' in sender_email or 'pofd' in sender_email or '1-ofd' in sender_email:
                        if 'кассовый чек' in html.lower() or 'кассовый чек' in body.lower():
                            parsed = parse_ofd_cheque(html or body)
                            if parsed['is_cheque'] and parsed['store'] and parsed['total']:
                                store_name = canonical_store_name(parsed['store'])
                                total = float(re.sub(r'[^\d.,]', '', parsed['total']).replace(',', '.'))
                                delivery_fee = parsed.get('delivery')
                                items = parsed.get('items', [])
                                date_str = parsed.get('date') or date_str
                            else:
                                delivery_fee = None
                        else:
                            delivery_fee = None
                    
                    # 2. Яндекс Чеки
                    if not store_name and ('check.yandex' in html or 'check.yandex' in body):
                        parsed = parse_ofd_cheque(html or body)
                        if parsed['is_cheque'] and parsed['total']:
                            total = float(re.sub(r'[^\d.,]', '', parsed['total']).replace(',', '.'))
                            store_name = canonical_store_name(parsed.get('store') or 'Яндекс')
                            delivery_fee = parsed.get('delivery')
                            items = parsed.get('items', [])
                            date_str = parsed.get('date') or date_str
                        else:
                            delivery_fee = None
                    
                    # 3. Яндекс Плюс
                    if not store_name and ('yandex plus' in full_text or 'яндекс плюс' in full_text):
                        store_name = canonical_store_name('Яндекс Плюс')
                        total = extract_amount_from_body(body, html)
                        if not total:
                            if 'подписк' in full_text or 'plus' in full_text:
                                total = 449.0
                        delivery_fee = None
                    
                    # 4. Определение магазина по отправителю
                    if not store_name:
                        sender_subj = (sender_email + ' ' + subj_val.lower()).lower()
                        for keywords, sname in TARGET_SENDERS:
                            if any(kw in sender_subj for kw in keywords):
                                store_name = canonical_store_name(sname)
                                total = extract_amount_from_body(body, html)
                                delivery_fee = None
                                break
                    
                    # 5. Если магазин определён — добавляем
                    if store_name and total:
                        email_id = msg.get('Message-ID', '').strip()
                        normalized_date, event_time = email_event_details(date_str, raw_date)
                        if not is_already_imported(
                            conn,
                            normalized_date,
                            total,
                            store_name,
                            event_time=event_time,
                            email_msg_id=email_id,
                            delivery_fee=delivery_fee,
                        ):
                            items_data = items if items else [(subj_val[:100], '')]
                            note_parts = []
                            delivery_note = build_delivery_note(delivery_fee)
                            time_note = build_time_note(event_time)
                            if delivery_note:
                                note_parts.append(delivery_note)
                            if time_note:
                                note_parts.append(time_note)
                            notes_suffix = '; '.join(note_parts)
                            add_purchase(conn, normalized_date, total, store_name, items_data, config['name'], notes_suffix, email_msg_id=email_id)
                            added += 1
                        else:
                            event_mark = f' {event_time}' if event_time else ''
                            log.info(f"   ⏭ Уже есть: {normalized_date}{event_mark} {total:.0f} ₽ {store_name}")
                    elif store_name:
                        log.info(f"   📄 {store_name}: сумма не найдена в письме")
                    
                except Exception as e:
                    log.error(f"   ❌ Ошибка письма {num} в папке {mailbox_name}: {e}")
                    continue

        if not scanned_any:
            log.info(f"   Нет писем за последние 2 дня")
            imap.logout()
            return 0
        
        imap.logout()
        conn.commit()
    except imaplib.IMAP4.error as e:
        log.error(f"   ❌ IMAP: {e}")
    except Exception as e:
        log.error(f"   ❌ {e}")
    
    return added


# ============================================================
# СКАНИРОВАНИЕ SMS (Phone Link)
# ============================================================

WINDOWS_PHONE_LINK_DB_GLOB = (
    "/mnt/c/Users/*/AppData/Local/Packages/"
    "Microsoft.YourPhone_8wekyb3d8bbwe/LocalCache/Indexed/*/System/Database/phone.db"
)

# SMS, которые нас интересуют (чеки, расходы, покупки)
INTERESTING_SMS_BANKS = {
    '900': 'sberbank',
}

INTERESTING_SMS_KEYWORDS = [
    r'покупк[аи]',
    r'списани[ея]',
    r'оплата',
    r'оплачен',
    r'дебет',
    r'дебетовая',
    r'карт[аы]',
    r'терминал',
    r'автоплат',
    r'подписк',
    r'получен',
    r'штраф',
    r'гибдд',
    r'госуслуг',
    r'платн[аяо]',
    r'проезд',
    r'дорог[аи]',
    r'транспондер',
    r'парковк',
]


def windows_ticks_to_datetime(value: int) -> Optional[datetime]:
    try:
        unix_seconds = (int(value) - 116444736000000000) / 10_000_000
        return datetime.fromtimestamp(unix_seconds)
    except: return None


def find_phone_link_dbs():
    matches = glob.glob(WINDOWS_PHONE_LINK_DB_GLOB)
    matches = [m for m in matches if os.path.exists(m)]
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return matches


def copy_db_bundle(src_db: str) -> str:
    tmp_dir = tempfile.mkdtemp(prefix='phone_link_')
    base = Path(src_db)
    for suffix in ('', '-wal', '-shm'):
        s = str(base) + suffix
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(tmp_dir, os.path.basename(s)))
    return os.path.join(tmp_dir, os.path.basename(src_db))


def scan_sms_today(parent_conn):
    """Сканирует SMS из всех БД Phone Link (Z Fold 3 + Z Fold 4) за сегодня.
    Возвращает количество добавленных записей.
    """
    db_paths = find_phone_link_dbs()
    if not db_paths:
        log.warning("   ⚠️ Базы Phone Link не найдены")
        return 0
    
    total_added = 0
    for db_path in db_paths:
        log.info(f"   📱 Phone Link: {db_path}")
        local_db = copy_db_bundle(db_path)
        try:
            sms_conn = sqlite3.connect(local_db)
            sms_conn.row_factory = sqlite3.Row

            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_ft = (int(today_start.timestamp()) + 11644473600) * 10_000_000

            rows = sms_conn.execute(
                'SELECT message_id, from_address, body, timestamp, type FROM message WHERE timestamp >= ? ORDER BY timestamp',
                (today_ft,)
            ).fetchall()

            if not rows:
                two_days_ago = today_start - timedelta(days=2)
                two_days_ft = (int(two_days_ago.timestamp()) + 11644473600) * 10_000_000
                rows = sms_conn.execute(
                    'SELECT message_id, from_address, body, timestamp, type FROM message WHERE timestamp >= ? ORDER BY timestamp',
                    (two_days_ft,)
                ).fetchall()

            sms_conn.close()

            if not rows:
                log.info(f"   Нет SMS за последние 2 дня")
                continue

            added = 0
            sms_found = 0

            for row in rows:
                body = row['body'] or ''
                from_addr = str(row['from_address'] or '')
                dt = windows_ticks_to_datetime(row['timestamp'])

                if not body.strip():
                    continue

                text = f"{from_addr} {body}".lower()

                is_charge = False

                if '900' in from_addr:
                    if any(re.search(p, text, re.IGNORECASE) for p in INTERESTING_SMS_KEYWORDS):
                        is_charge = True

                if any(kw in text for kw in ['покупка', 'списание', 'оплата', 'дебет']):
                    is_charge = True

                if not is_charge:
                    if any(p in text for p in ['код', 'пополнен', 'зачислен', 'поступил', 'баланс', 'никому не сообщай', 'перевод', 'зачисление']):
                        continue
                    if re.search(r'(\d[\d\s]*[.,]?\d*)\s*(?:₽|руб|р\.)', text):
                        if any(kw in text for kw in ['карт', 'терминал', 'спис', 'оплат']):
                            is_charge = True

                if not is_charge:
                    continue

                amount = None
                m = re.search(r'(-?\d[\d\s]*[.,]?\d*)\s*(?:₽|руб|р\.)', text)
                if m:
                    try:
                        amount = abs(float(m.group(1).replace(' ', '').replace(',', '.')))
                    except:
                        pass

                if not amount:
                    continue

                store = None
                m = re.search(r'(?:покупка|терминал|оплата)\s+([А-Яа-яA-Za-z][А-Яа-яA-Za-z\s.\-&\d]{1,40})', text)
                if m:
                    store = m.group(1).strip()
                    store = re.sub(r'\s+\d+[.,]?\d*$', '', store)
                    store = re.sub(r'\s+₽.*$', '', store)
                    if len(store) < 3:
                        store = None

                if not store:
                    if any(p in text for p in ['штраф', 'гибдд', 'постановление']):
                        store = 'Штраф ГИБДД'
                    elif any(p in text for p in ['платн', 'проезд', 'дорог', 'транспондер', 'автодор']):
                        store = 'Платные дороги'
                    elif any(p in text for p in ['парковк', 'ампп', 'мсд']):
                        store = 'Парковка / МСД'
                    elif any(p in text for p in ['госуслуг', 'gosuslugi']):
                        store = 'Госуслуги'
                    else:
                        store = 'SMS payment'

                store = canonical_store_name(store)

                if amount > 10000 and store == 'SMS payment':
                    log.info(f"   ⏭ SMS пропущен (крупная сумма, не расход): {amount:.0f} ₽")
                    continue

                sms_found += 1
                date_str = dt.strftime('%Y-%m-%d') if dt else datetime.now().strftime('%Y-%m-%d')
                event_time = dt.strftime('%H:%M') if dt else None

                if not is_already_imported(parent_conn, date_str, amount, store, event_time=event_time):
                    notes_suffix = build_time_note(event_time)  # 'время HH:MM'
                    # Используем короткое описание, без полного body и без баланса
                    item_label = f'SMS: {store} {amount:.0f}₽'
                    add_purchase(parent_conn, date_str, amount, store, [(item_label, '')], 'sms', notes_suffix)
                    added += 1
                else:
                    event_mark = f' {event_time}' if event_time else ''
                    log.info(f"   ⏭ SMS уже есть: {date_str}{event_mark} {amount:.0f} ₽ {store}")

            parent_conn.commit()
            total_added += added
            log.info(f"   📱 SMS: всего найдено {sms_found}, добавлено новых: {added}")

        except Exception as e:
            log.error(f"   ❌ SMS ошибка ({db_path}): {e}")
        finally:
            if local_db and os.path.exists(local_db):
                shutil.rmtree(os.path.dirname(local_db), ignore_errors=True)

    return total_added


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("=" * 60)
    log.info(f"🚀 Ежедневное сканирование всех источников")
    log.info(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    total = 0
    
    # 1. Почты
    for cfg in IMAP_CONFIGS:
        try:
            total += scan_mailbox(cfg, conn)
        except Exception as e:
            log.error(f"❌ {cfg['name']}: {e}")
    
    # 2. SMS (Phone Link) — старая логика
    try:
        log.info("📱 SMS (Phone Link)...")
        total += scan_sms_today(conn)
    except Exception as e:
        log.error(f"❌ SMS: {e}")
    
    # 2.1. SMS через sms_expense_monitor (новая логика для всех банков)
    try:
        log.info("📱 SMS Expense Monitor (все банки)...")
        # Импортируем и запускаем
        sys.path.insert(0, SCRIPT_DIR)
        from sms_expense_monitor import scan_sms_expenses, import_expenses
        expenses = scan_sms_expenses(days_back=2)
        if expenses:
            imported, skipped = import_expenses(expenses)
            log.info(f"   📱 SMS Monitor: найдено {len(expenses)}, импортировано {imported}, пропущено {skipped}")
            total += imported
        else:
            log.info("   📱 SMS Monitor: новых расходов не найдено")
    except Exception as e:
        log.error(f"❌ SMS Expense Monitor: {e}")
    
    log.info("=" * 60)
    if total:
        log.info(f"📊 Добавлено новых записей: {total}")
    else:
        log.info(f"📊 Новых записей не найдено")
    log.info("=" * 60)
    
    # Отправляем отчёт на почту (ДО закрытия БД)
    send_daily_report(conn, total)
    
    conn.close()
    return total


def build_report(conn, new_count):
    """Формирует HTML-отчёт о расходах за сегодня."""
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Покупки за сегодня
    rows = conn.execute("""
        SELECT purchase_date, total_amount, source, store_name, notes
        FROM purchases 
        WHERE purchase_date = ? 
          AND total_amount IS NOT NULL
          AND total_amount > 0
        ORDER BY total_amount DESC
    """, (today,)).fetchall()
    
    # Покупки за вчера (на случай если сегодня нет, но есть вчерашние отложенные)
    if not rows:
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        rows = conn.execute("""
            SELECT purchase_date, total_amount, source, store_name, notes
            FROM purchases 
            WHERE purchase_date = ? 
              AND total_amount IS NOT NULL
              AND total_amount > 0
            ORDER BY total_amount DESC
        """, (yesterday,)).fetchall()
    
    # Всего за месяц
    month_start = datetime.now().strftime('%Y-%m-01')
    month_total = conn.execute("""
        SELECT COALESCE(SUM(total_amount), 0) FROM purchases 
        WHERE purchase_date >= ? 
          AND total_amount IS NOT NULL
          AND total_amount > 0
          AND source NOT IN ('rusconcert', 'yandex_plus', 'yandex_sp')
    """, (month_start,)).fetchone()[0]
    
    # По магазинам за месяц
    by_store = conn.execute("""
        SELECT store_name, COUNT(*), COALESCE(SUM(total_amount), 0)
        FROM purchases 
        WHERE purchase_date >= ?
          AND total_amount IS NOT NULL
          AND total_amount > 0
          AND source NOT IN ('rusconcert', 'yandex_plus', 'yandex_sp')
        GROUP BY store_name
        ORDER BY SUM(total_amount) DESC
    """, (month_start,)).fetchall()
    
    # Формируем HTML
    html = f"""<html><body style="font-family:sans-serif;padding:20px;">
<h2>📊 Отчёт о расходах — {today}</h2>
"""
    
    if new_count:
        html += f'<p style="color:green;">✅ Добавлено новых записей: {new_count}</p>'
    
    if rows:
        html += '<h3>Сегодняшние покупки</h3><table border="1" cellpadding="6" style="border-collapse:collapse;">'
        html += '<tr><th>Дата</th><th>Магазин</th><th>Сумма</th><th>Описание</th></tr>'
        for r in rows:
            notes = (r[4] or '')[:120]
            html += f'<tr><td>{r[0]}</td><td>{r[3] or r[2]}</td><td align="right">{r[1]:.0f} ₽</td><td>{notes}</td></tr>'
        html += '</table>'
    else:
        html += '<p>Сегодня покупок нет.</p>'
    
    html += f'<h3>Всего за месяц: <strong>{month_total:.0f} ₽</strong></h3>'
    
    if by_store:
        html += '<h3>По магазинам</h3><table border="1" cellpadding="6" style="border-collapse:collapse;">'
        html += '<tr><th>Магазин</th><th>Кол-во</th><th>Сумма</th></tr>'
        for s in by_store:
            html += f'<tr><td>{s[0] or "другое"}</td><td align="center">{s[1]}</td><td align="right">{s[2]:.0f} ₽</td></tr>'
        html += '</table>'
    
    html += '<hr><p style="color:#888;font-size:small;">Сформировано Consumption Agent</p></body></html>'
    return html


def send_daily_report(conn, new_count):
    """Отправляет отчёт на почту через Gmail SMTP."""
    try:
        gmail_pwd = get_password({
            'name': 'Gmail',
            'user': 'yu.v.artamonov@gmail.com',
            'password_env': 'GMAIL_APP_PASSWORD',
            'password_fallback': 'kzjjirsrhcsmptoc',
        })
        if not gmail_pwd:
            log.warning("⚠️ Нет пароля Gmail для отправки отчёта")
            return
        
        html_body = build_report(conn, new_count)
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'📊 Отчёт о расходах — {datetime.now().strftime("%d.%m.%Y")}'
        msg['From'] = 'yu.v.artamonov@gmail.com'
        msg['To'] = 'yu.v.artamonov@gmail.com'
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30)
        server.login('yu.v.artamonov@gmail.com', gmail_pwd)
        server.send_message(msg)
        server.quit()
        log.info(f"📧 Отчёт отправлен на yu.v.artamonov@gmail.com")
    except Exception as e:
        log.error(f"❌ Ошибка отправки отчёта: {e}")


if __name__ == '__main__':
    main()
