#!/usr/bin/env python3
"""
Быстрый импорт финансовых документов с Яндекс.Почты
"""
import sys
sys.path.insert(0, '/home/yuri_artamonov/.openclaw/workspace/consumption_agent')

import imaplib, sqlite3, re, os
from email.header import decode_header
from datetime import datetime, timedelta

DB_PATH = '/home/yuri_artamonov/.openclaw/workspace/consumption_agent/consumption.db'

def fetch_headers(mail, uid):
    """Get Subject and Date for one UID."""
    _, fd = mail.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
    if fd[0] is None or fd[0][1] is None:
        return '', '', ''
    raw = fd[0][1].decode('utf-8', errors='replace')
    ds = sj = sender = ''
    for ln in raw.split('\n'):
        ln = ln.strip()
        if ln.lower().startswith('date:'):
            ds = ln[5:].strip()
        elif ln.lower().startswith('subject:'):
            try:
                dp = decode_header(ln[8:].strip())
                sj = ''.join(p.decode(e or 'utf-8', errors='replace') if isinstance(p, bytes) else p for p, e in dp)
            except:
                sj = ln[8:].strip()
        elif ln.lower().startswith('from:'):
            em = re.search(r'<([^>]+)>', ln)
            sender = (em.group(1) if em else ln[5:].strip()).lower()
    return ds, sj, sender

def parse_imap_date(s):
    if not s:
        return ''
    from email.utils import parsedate
    parsed = parsedate(s)
    if parsed:
        try:
            return datetime(*parsed[:3]).strftime('%Y-%m-%d')
        except:
            pass
    return ''

def extract_amount(text):
    """Извлекает сумму из текста."""
    if not text:
        return None
    # Убираем HTML
    clean = re.sub(r'<[^>]+>', '\n', text)
    clean = re.sub(r'\n+', '\n', clean)
    
    patterns = [
        r'(\d+[\.\s\u00a0]?\d*)\s*[₽]',
        r'[₽]\s*(\d+[\.\s\u00a0]?\d*)',
        r'(\d+[\.\s\u00a0]?\d*)\s*руб',
        r'(\d+[\.\s\u00a0]?\d*)\s*RUB',
        r'(\d+[\.\s\u00a0]?\d*)\s*\$',
    ]
    for pat in patterns:
        m = re.search(pat, clean, re.IGNORECASE)
        if m:
            try:
                s = re.sub(r'[^\d.,]', '', m.group(1))
                return float(s.replace(',', '.'))
            except:
                pass
    return None

def main():
    mail = imaplib.IMAP4_SSL('imap.yandex.ru', 993, timeout=15)
    mail.login('HKID2021@yandex.ru', 'jmwegtxlztunrwua')
    mail.select('INBOX')
    
    conn = sqlite3.connect(DB_PATH)
    
    # Все UIDs
    _, ids = mail.search(None, 'ALL')
    all_ids = ids[0].split() if ids[0] else []
    print(f'Писем в ящике: {len(all_ids)}', flush=True)
    
    known_senders = {
        'hello@plus.yandex.ru': 'yandex_plus',
        'hello@afisha.yandex.ru': 'yandex_afisha',
        'hello@sp.yandex.ru': 'yandex_sp',
        'hello@kinopoisk.ru': 'yandex_kinopoisk',
        'hello@music.yandex.ru': 'yandex_music',
        'station@alice.yandex.ru': 'yandex_station',
        'noreply@drive.yandex.ru': 'yandex_drive',
        'noreply@id.yandex.ru': 'yandex_id',
        'info@360.yandex.ru': 'yandex_360',
        'noreply@pay.yandex.ru': 'yandex_pay',
    }
    
    imported = 0
    purchase_kw = ['чек', 'заказ', 'квитанция', 'билет', 'приобретение', 'подписка',
                   'списание', 'оплата', 'receipt', 'invoice', 'payment', 'купить',
                   'успешн', 'successful', 'order', 'purchase', 'билеты', 'билет']
    skip_kw = ['скидк', 'акци', 'новинк', 'дарим', 'промо', 'совет', 'подборк',
               'рекоменд', 'советуем', 'заглянит', 'прочита', 'топ10', 'enewsletter',
               'weekly', 'newsletter', 'unsubscribe', 'спасибо за покупку',
               'спасибо за заказ', 'ваша подписка оформлен', 'добро пожаловать']
    
    for uid in reversed(all_ids):
        uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
        
        # Пропускаем уже импортированные
        if conn.execute("SELECT id FROM cheques_log WHERE email_uid=?", (uid_s,)).fetchone():
            continue
        
        ds, sj, sender = fetch_headers(mail, uid)
        if not sender or not sj:
            continue
        
        # Проверяем, знаем ли мы отправителя
        known_id = known_senders.get(sender)
        if not known_id:
            continue
        
        # Фильтруем маркетинг
        sj_lower = sj.lower()
        if any(kw in sj_lower for kw in skip_kw):
            continue
        
        # Ищем покупки
        is_purchase = any(kw in sj_lower for kw in purchase_kw)
        if not is_purchase:
            # Для plus/music/kinopoisk — только про подписку
            if known_id in ('yandex_plus', 'yandex_music', 'yandex_kinopoisk'):
                if not any(w in sj_lower for w in ['подпис', 'списа', 'payment', 'оплат']):
                    continue
            elif known_id in ('yandex_sp', 'yandex_360'):
                continue  # это реклама, пропускаем
            elif known_id in ('yandex_id',):
                continue  # уведомления безопасности
        
        iso = parse_imap_date(ds)
        
        # Пробуем достать тело для суммы
        total_amount = None
        try:
            _, fd = mail.fetch(uid, '(BODY.PEEK[])')
            msg = email.message_from_bytes(fd[0][1]['BODY[]' if isinstance(fd[0][1], dict) else fd[0][1]], _class=email.message.EmailMessage)
            # Не будем загружать гигабайты — пока без суммы
        except:
            pass
        
        conn.execute("""INSERT OR IGNORE INTO purchases 
            (purchase_date, total_amount, source, store_name, email_message_id, notes, data_origin) 
            VALUES (?,?,?,?,?,?,?)""",
            (iso, total_amount, known_id, known_id, uid_s, sj[:80], 'yandex_mail'))
        conn.execute("""INSERT OR IGNORE INTO cheques_log 
            (email_uid, cheque_date, subject, source) VALUES (?,?,?,?)""",
            (uid_s, ds[:20] if ds else '', sj[:80], known_id))
        
        imported += 1
        if imported % 10 == 0:
            conn.commit()
            print(f'  ... {imported} импортировано', flush=True)
    
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL").fetchone()[0]
    conn.close()
    mail.logout()
    print(f'\nИмпортировано: {imported} новых. Всего покупок: {total}')

if __name__ == '__main__':
    main()
