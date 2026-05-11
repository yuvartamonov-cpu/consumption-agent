#!/usr/bin/env python3
"""Импорт чеков Ozon в базу consumption_agent."""
import imaplib, email, re, json, os, sqlite3
from email.header import decode_header
from datetime import datetime

DB_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(DB_DIR, 'consumption.db')
CONFIG = {
    'imap_host': 'imap.gmail.com', 'imap_port': 993,
    'user': 'yu.v.artamonov@gmail.com',
    os.getenv('GMAIL_APP_PASSWORD', '').replace('"', '').replace(' ', ''),
}

def decode_subj(raw):
    parts = decode_header(raw)
    return ''.join(
        p.decode(e or 'utf-8', errors='replace') if isinstance(p, bytes) else str(p)
        for p, e in parts
    )

def parse_date(imap_date):
    """Парсит IMAP-дату в ISO."""
    try:
        # Sat, 24 Jan 2026 03:12:05 +0000
        dt = datetime.strptime(imap_date[:25], '%a, %d %b %Y %H:%M:%S')
        return dt.strftime('%Y-%m-%d')
    except:
        return imap_date[:10]

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL,
            source TEXT DEFAULT 'ozon',
            store_name TEXT DEFAULT 'Ozon',
            order_number TEXT,
            email_uid TEXT,
            receipt_url TEXT,
            notes TEXT,
            data_origin TEXT DEFAULT 'local',
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cheques_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_uid TEXT UNIQUE,
            cheque_date TEXT,
            subject TEXT,
            receipt_url TEXT,
            imported_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    return conn

def main():
    print('Подключаюсь к почте...')
    mail = imaplib.IMAP4_SSL(CONFIG['imap_host'], CONFIG['imap_port'])
    mail.login(CONFIG['user'], CONFIG['password'])
    mail.select('INBOX')

    s, ids = mail.search(None, 'FROM', 'sender.ozon.ru')
    all_ids = ids[0].split()
    recent = all_ids[-60:]

    cheques = []
    for uid in recent:
        fs, fd = mail.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
        r = fd[0][1].decode('utf-8', errors='replace')
        date = ''
        subj = ''
        for ln in r.split('\n'):
            li = ln.strip()
            if li.lower().startswith('date:'):
                date = li[5:].strip()
            elif li.lower().startswith('subject:'):
                subj = decode_subj(li[8:].strip())
        if 'ваш чек' in subj.lower():
            cheques.append((uid, date, subj))

    print(f'Найдено чеков: {len(cheques)}')

    conn = init_db()
    imported = 0
    
    for uid, date, subj in reversed(cheques):
        iso_date = parse_date(date)
        
        # Проверяем, не импортирован ли уже
        uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
        exists = conn.execute('SELECT id FROM cheques_log WHERE email_uid = ?', (uid_str,)).fetchone()
        if exists:
            continue
        
        # Ищем ссылку на чек в теле
        uid_b = uid if isinstance(uid, bytes) else bytes(uid_str, 'utf-8')
        try:
            fs, fd = mail.fetch(uid_b, '(BODY.PEEK[])')
            msg = email.message_from_bytes(fd[0][1])
            html = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/html':
                        pl = part.get_payload(decode=True)
                        if pl:
                            html += pl.decode('utf-8', errors='replace')
            else:
                pl = msg.get_payload(decode=True)
                if pl:
                    html += pl.decode('utf-8', errors='replace')
            
            dl_links = re.findall(r'href=["\']([^"\']*/e-check/download/[^"\']+)["\']', html)
            receipt_url = dl_links[0].split('?')[0] if dl_links else ''
        except:
            receipt_url = ''
        
        # Сохраняем
        conn.execute(
            'INSERT OR IGNORE INTO purchases (purchase_date, source, email_uid, receipt_url, notes) VALUES (?, ?, ?, ?, ?)',
            (iso_date, 'ozon', uid_str, receipt_url, subj[:60])
        )
        conn.execute(
            'INSERT OR IGNORE INTO cheques_log (email_uid, cheque_date, subject, receipt_url) VALUES (?, ?, ?, ?)',
            (uid_str, date[:20], subj[:60], receipt_url)
        )
        imported += 1
    
    conn.commit()
    
    # Итог
    total = conn.execute('SELECT COUNT(*) FROM purchases').fetchone()[0]
    print(f'\nИмпортировано: {imported}')
    print(f'Всего в БД: {total}')
    
    print('\n=== Последние покупки ===')
    rows = conn.execute('SELECT purchase_date, source, receipt_url FROM purchases ORDER BY id DESC LIMIT 5').fetchall()
    for dt, src, url in rows:
        url_short = url[:40] + '...' if len(url) > 40 else (url or '-')
        print(f'  {dt} | {src} | {url_short}')
    
    conn.close()
    mail.logout()

if __name__ == '__main__':
    main()
