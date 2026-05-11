#!/usr/bin/env python3
"""Email-чекалка: раз в день проверяет почту на новые письма от Столото."""
import os
import imaplib
import email
import json
import datetime
import re
from email.header import decode_header

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

CONFIG = {
    'imap_host': 'imap.gmail.com',
    'imap_port': 993,
    'user': 'yu.v.artamonov@gmail.com',
    'password': '[REDACTED_OLD_GMAIL_APP_PASSWORD]',
    'senders': ['stoloto'],
    'ozon_senders': ['sender.ozon.ru', 'news.ozon.ru', 'ozontravel@news.ozon.ru'],
    'fonbet_senders': ['fon.bet', 'fonbet'],
}

STATE_FILE = os.path.join(os.path.dirname(__file__), '.email_check_state.json')


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'last_checked': None}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def decode_subject(msg):
    subj = msg['Subject']
    if subj is None:
        return ''
    decoded_parts = decode_header(subj)
    result = []
    for part, enc in decoded_parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(enc or 'utf-8', errors='replace'))
            except LookupError:
                result.append(part.decode('utf-8', errors='replace'))
        else:
            result.append(str(part))
    return ''.join(result)


def html_to_text(html):
    if not html:
        return ''
    if BeautifulSoup is not None:
        try:
            return ' '.join(BeautifulSoup(html, 'html.parser').get_text('\n').split())
        except Exception:
            pass
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'&nbsp;|&#160;', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def decode_part_text(part):
    payload = part.get_payload(decode=True)
    if not payload:
        return ''
    charset = part.get_content_charset() or 'utf-8'
    try:
        return payload.decode(charset, errors='replace')
    except LookupError:
        return payload.decode('utf-8', errors='replace')


def extract_money_amounts(text):
    amounts = []
    for match in re.findall(r'(\d[\d\s]*[.,]\d{2}|\d[\d\s]*)(?:\s*)(?:₽|руб\.?|RUB)', text, re.IGNORECASE):
        cleaned = re.sub(r'\s+', '', match).replace(',', '.')
        if cleaned and cleaned not in amounts:
            amounts.append(cleaned)
    return amounts


def get_result_from_body(msg):
    """Пытается извлечь информацию о выигрыше из тела письма."""
    plain_parts = []
    html_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain':
                plain_parts.append(decode_part_text(part))
            elif ct == 'text/html':
                html_parts.append(decode_part_text(part))
    else:
        ct = msg.get_content_type()
        text = decode_part_text(msg)
        if ct == 'text/html':
            html_parts.append(text)
        else:
            plain_parts.append(text)

    plain_body = ' '.join(filter(None, plain_parts)).strip()
    html_body = ' '.join(filter(None, html_parts)).strip()
    body = plain_body if plain_body else html_to_text(html_body)
    preview = body[:500] if body else html_to_text(html_body)[:500]

    amounts = []

    # Спец-кейс для Столото: письма о выплате выигрыша в кошелёк
    stoloto_context_patterns = [
        r'выигрыш успешно зачислен.{0,120}?сумма\s+(\d[\d\s]*[.,]\d{2}|\d[\d\s]*)\s*(?:₽|руб)',
        r'зачисление выигрыша.{0,120}?сумма\s+(\d[\d\s]*[.,]\d{2}|\d[\d\s]*)\s*(?:₽|руб)',
        r'лотерея:\s*[«\"]?русское\s+лото[»\"]?.{0,120}?сумма\s+(\d[\d\s]*[.,]\d{2}|\d[\d\s]*)\s*(?:₽|руб)',
    ]
    for pattern in stoloto_context_patterns:
        for match in re.findall(pattern, body, re.IGNORECASE | re.DOTALL):
            cleaned = re.sub(r'\s+', '', match).replace(',', '.')
            if cleaned not in amounts:
                amounts.append(cleaned)

    # Общий фолбэк
    generic = re.findall(r'(?:выигрыш|выплат|сумма|приз|итог)[^0-9]{0,40}(\d[\d\s]*[.,]\d{2}|\d[\d\s]*)\s*(?:₽|руб)', body, re.IGNORECASE)
    generic += re.findall(r'(\d[\d\s]*[.,]\d{2}|\d[\d\s]*)\s*(?:₽|руб).{0,40}(?:выигрыш|выплат|приз|итог)', body, re.IGNORECASE)
    for match in generic:
        cleaned = re.sub(r'\s+', '', match).replace(',', '.')
        if cleaned not in amounts:
            amounts.append(cleaned)

    if not amounts:
        amounts = extract_money_amounts(body)

    return amounts, preview  # первые 500 символов для контекста


def check_fonbet(mail):
    """Проверка писем от fonbet (пополнения, результаты ставок)."""
    import re
    result = []
    for sender in CONFIG['fonbet_senders']:
        status, ids = mail.search(None, '(FROM "' + sender + '")')
        if status == 'OK' and ids[0]:
            ids_list = ids[0].split()
            for i in ids_list[-10:]:
                fstatus, data = mail.fetch(i, '(RFC822)')
                if fstatus != 'OK':
                    continue
                msg = email.message_from_bytes(data[0][1])
                subject = decode_subject(msg)
                date = msg['Date']
                
                # Извлекаем тело письма
                body = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct == 'text/plain':
                            try:
                                body += part.get_payload(decode=True).decode('utf-8', errors='replace')
                            except:
                                pass
                        elif ct == 'text/html':
                            try:
                                html = part.get_payload(decode=True).decode('utf-8', errors='replace')
                                body += re.sub(r'<[^>]+>', ' ', html)
                                body += '\n'
                            except:
                                pass
                else:
                    try:
                        body += msg.get_payload(decode=True).decode('utf-8', errors='replace')
                    except:
                        pass
                
                amounts = re.findall(r'(\d+[\.\,]\d{1,2})\s*(?:руб|\₽)', body)
                
                # Определяем тип письма
                msg_type = 'unknown'
                if 'Результат платежа' in subject or 'чек' in subject.lower():
                    msg_type = 'payment'
                elif 'ставк' in body.lower() or 'пари' in body.lower():
                    msg_type = 'bet'
                elif 'результат' in body.lower() or 'рассчитан' in body.lower():
                    msg_type = 'result'
                
                result.append({
                    'date': date,
                    'subject': subject,
                    'type': msg_type,
                    'amounts': amounts,
                    'preview': body[:300],
                })
    return result


def check_stoloto(mail):
    """Проверка лотерейных писем."""
    today = datetime.date.today().strftime('%d-%b-%Y')
    import re
    
    # Последние 30 дней
    search_criteria = '(OR ' + ' '.join(f'FROM "{s}"' for s in CONFIG['senders']) + ' SINCE ' + (datetime.date.today() - datetime.timedelta(days=30)).strftime('%d-%b-%Y') + ')'
    
    status, ids = mail.search(None, search_criteria)
    found = []
    if status == 'OK' and ids[0]:
        ids_list = ids[0].split()
        for i in ids_list[-50:]:
            fstatus, data = mail.fetch(i, '(RFC822)')
            if fstatus != 'OK':
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            subject = decode_subject(msg)
            date = msg['Date']
            amounts, body_preview = get_result_from_body(msg)
            found.append({
                'date': date,
                'subject': subject,
                'amounts': amounts,
                'preview': body_preview[:200],
            })
    return found


def scan_ozon(mail, limit=20):
    """Последние N писем от Ozon с кратким содержимым."""
    status, ids = mail.search(None, 'FROM', 'sender.ozon.ru')
    if status != 'OK' or not ids[0]:
        return []
    ids_list = ids[0].split()
    
    import re
    found = []
    for uid in ids_list[-limit:]:
        fstatus, fdata = mail.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])')
        if fstatus != 'OK':
            continue
        raw = fdata[0][1].decode('utf-8', errors='replace')
        
        subj = ''
        date = ''
        for line in raw.split('\n'):
            l = line.strip()
            if l.lower().startswith('subject:'):
                sr = l[8:].strip()
                parts = decode_header(sr)
                subj = ''.join(p.decode(e or 'utf-8', errors='replace') if isinstance(p, bytes) else str(p) for p,e in parts)
            elif l.lower().startswith('date:'):
                date = l[5:].strip()
        
        found.append({'uid': uid.decode() if isinstance(uid, bytes) else str(uid), 'date': date, 'subject': subj})
    
    return found


def check_email(show_ozon=False):
    state = load_state()
    last_checked = state.get('last_checked')

    try:
        mail = imaplib.IMAP4_SSL(CONFIG['imap_host'], CONFIG['imap_port'])
        mail.login(CONFIG['user'], CONFIG['password'])
        mail.select('INBOX')
    except Exception as e:
        print(json.dumps({
            "error": "IMAP connection failed",
            "details": str(e),
            "stoloto": [],
            "fonbet": [],
            "ozon_summary": {"total_from_sender_ozon": None, "cheque_count": None}
        }, ensure_ascii=False, indent=2))
        return

    result = {}
    
    result['stoloto'] = check_stoloto(mail)
    
    if show_ozon or '--ozon' in __import__('sys').argv:
        result['ozon'] = scan_ozon(mail)
    
    result['ozon_summary'] = {
        'total_from_sender_ozon': None,
        'cheque_count': None,
    }
    
    # Получим статистику если --ozon
    if '--ozon' in __import__('sys').argv:
        status, ids = mail.search(None, 'FROM', 'sender.ozon.ru')
        if status == 'OK' and ids[0]:
            result['ozon_summary']['total_from_sender_ozon'] = len(ids[0].split())
            
            cheque_count = 0
            for uid in ids[0].split():
                if cheque_count > 100:
                    break
                fstatus, fdata = mail.fetch(uid, '(BODY.PEEK[HEADER.FIELDS (SUBJECT)])')
                if fstatus != 'OK':
                    continue
                r = fdata[0][1].decode('utf-8', errors='replace')
                for line in r.split('\n'):
                    if line.strip().lower().startswith('subject:'):
                        sr = line[8:].strip()
                        parts = decode_header(sr)
                        subj = ''.join(p.decode(e or 'utf-8', errors='replace') if isinstance(p, bytes) else str(p) for p,e in parts)
                        if 'ваш чек' in subj.lower():
                            cheque_count += 1
                        break
            result['ozon_summary']['cheque_count'] = cheque_count

    result['fonbet'] = check_fonbet(mail)

    mail.logout()
    save_state({'last_checked': datetime.datetime.now().isoformat()})
    
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    check_email()
