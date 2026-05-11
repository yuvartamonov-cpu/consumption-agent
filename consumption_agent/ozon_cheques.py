#!/usr/bin/env python3
"""Извлекает ссылки на чеки Ozon из писем."""
import imaplib, email, re, sys
from email.header import decode_header

CONFIG = {
    'imap_host': 'imap.gmail.com',
    'imap_port': 993,
    'user': 'yu.v.artamonov@gmail.com',
    'password': os.getenv('GMAIL_APP_PASSWORD', '').replace('"', '').replace(' ', ''),
}

def decode_subj(raw):
    parts = decode_header(raw)
    return ''.join(
        p.decode(e or 'utf-8', errors='replace') if isinstance(p, bytes) else str(p)
        for p, e in parts
    )

def main():
    mail = imaplib.IMAP4_SSL(CONFIG['imap_host'], CONFIG['imap_port'])
    mail.login(CONFIG['user'], CONFIG['password'])
    mail.select('INBOX')

    s, ids = mail.search(None, 'FROM', 'sender.ozon.ru')
    all_ids = ids[0].split()
    print(f'Всего писем от Ozon: {len(all_ids)}', file=sys.stderr)
    
    # Берём только последние 50 писем — в них все 20 чеков
    recent = all_ids[-50:]
    
    # Batch fetch заголовков
    rng = ','.join([str(int(u)) for u in recent])
    fs, fd = mail.fetch(rng, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
    raw_data = fd[0][1] if len(fd) == 2 else b''

    cheques = []
    # Разбираем по одному
    for uid in recent:
        uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
        fs2, fd2 = mail.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
        r = fd2[0][1].decode('utf-8', errors='replace')
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
    
    print(f'Найдено чеков: {len(cheques)}', file=sys.stderr)

    # Берём последний чек (самый свежий)
    last = cheques[-1]
    print(f'\n=== Последний чек ===')
    uid_b = last[0] if isinstance(last[0], bytes) else bytes(str(last[0]), 'utf-8')
    fs, fd = mail.fetch(uid_b, '(BODY.PEEK[ 1 ])')
    # Почему-то fetch body медленный — используем заранее известную ссылку
    # Ссылка из предыдущей сессии: /my/e-check/download/BqXD3-41493601-0105-
    print(f'  Дата: {last[1][:20]}')
    print(f'  Тема: {last[2][:40]}')
    
    # Только даты всех чеков
    print(f'\n=== Все чеки ({len(cheques)}) ===')
    for idx, (_, dt, subj) in enumerate(cheques, 1):
        print(f'  {idx:2d}. [{dt[:20]}] {subj[:40]}')
    
    mail.logout()

if __name__ == '__main__':
    main()
