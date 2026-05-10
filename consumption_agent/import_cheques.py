#!/usr/bin/env python3
"""
Consumption Agent — import parsed PDF cheques into DB with per-item prices.
Uses pdfplumber for precise price extraction from PDF layout.
Also handles email-parsed Ozon cheques (IMAP + HTML).
"""
import sqlite3, os, re

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')
TEXT_DIR = os.path.join(os.path.dirname(__file__), 'incoming_cheques_text')
CHEQUE_DIR = os.path.join(os.path.dirname(__file__), 'incoming_cheques')

# Try to import pdfplumber; fallback to pdftotext parsing
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False


def parse_pdf_with_pdfplumber(filepath):
    """
    Parse PDF with pdfplumber — extracts (item_name, price) tuples
    and the total amount. Filters out delivery/service lines.
    Returns items, total.
    """
    items = []
    total = None
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                words = page.extract_words(keep_blank_chars=True, x_tolerance=3)
                lines = {}
                for w in words:
                    y = round(w['top'], 0)
                    lines.setdefault(y, []).append((w['x0'], w['text']))

                current_name = None
                for y in sorted(lines):
                    l = sorted(lines[y], key=lambda x: x[0])
                    text = '  '.join(t for _, t in l)

                    # Item line: "N. Name"
                    m = re.match(r'^(\d+)\.\s+(.+)', text)
                    if m:
                        current_name = m.group(2).strip()
                        continue

                    # Price line: "1 x 359,24  ≡359,24"
                    pm = re.match(r'1 x ([\d\s]+[.,]\d{2})\s*[≡=]\s*([\d\s]+[.,]\d{2})', text)
                    if pm and current_name:
                        price = float(pm.group(1).replace(' ', '').replace(',', '.'))
                        if 'доставк' not in current_name.lower() and \
                           'компенсация' not in current_name.lower() and \
                           'обработк' not in current_name.lower():
                            items.append((current_name, price))
                        current_name = None
                        continue

                    # Total
                    if text.startswith('ИТОГ'):
                        tm = re.search(r'([\d\s]+[.,]\d{2})', text)
                        if tm:
                            total = float(tm.group(1).replace(' ', '').replace(',', '.'))
    except Exception as e:
        print(f'    ERR pdfplumber: {e}')

    return items, total


def parse_cheque_text(text):
    """Fallback: parse pdftotext output (less precise)."""
    date_m = re.search(r'(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})', text)
    if not date_m:
        return None, None, None
    dt = f'{date_m.group(1)} {date_m.group(2)}'

    items_raw = re.findall(r'^\d+\.\s+(.+)', text, re.MULTILINE)
    items = [i.strip() for i in items_raw if i.strip()
             and 'доставк' not in i.lower()
             and 'компенсация' not in i.lower()
             and 'обработк' not in i.lower()]

    total_m = re.search(r'ИТОГО[:\s]*([\d\s]+[.,]?\d*)', text)
    total = None
    if total_m:
        try:
            total = float(total_m.group(1).strip().replace(' ', '').replace(',', '.'))
        except:
            pass

    return items, total, dt


def guess_category(name):
    name_l = name.lower()
    if any(w in name_l for w in ['корм', 'симпарик', 'ветеринар', 'животн', 'собак', 'кошк',
                                  'грандорф', 'мосенда']):
        return 'cat_pets'
    if any(w in name_l for w in ['лекарств', 'таблетк', 'витамин', 'бад']):
        return 'cat_health_med'
    if any(w in name_l for w in ['премиум', 'подписк']):
        return 'cat_subscriptions'
    if any(w in name_l for w in ['стол', 'стул', 'кроват', 'шкаф', 'мебель', 'стремян',
                                  'насосн', 'камин', 'ёлка', 'елк']):
        return 'cat_home_furn'
    if any(w in name_l for w in ['телефон', 'наушник', 'зарядк']):
        return 'cat_tech'
    if any(w in name_l for w in ['крем', 'шампун', 'мыло', 'лосьон']):
        return 'cat_cosmetics'
    if any(w in name_l for w in ['кросовк', 'обувь', 'ботинк', 'туфл']):
        return 'cat_clo_shoes'
    if any(w in name_l for w in ['плать', 'рубашк', 'футболк', 'джинс', 'бель', 'трус',
                                  'костюм', 'лиф']):
        return 'cat_clo_everyday'
    if any(w in name_l for w in ['питер пэн', 'гарри поттер', 'бытие', 'тошнота',
                                  'вгляд', 'дар психотер', 'смысл жизни', 'мамочка']):
        return 'cat_culture_books'
    if any(w in name_l for w in ['секс', 'вибратор', 'пробк', 'фаллоимит', 'бдсм',
                                  'анальн', 'эротич']):
        return 'cat_sexual'
    if any(w in name_l for w in ['мяч', 'коврик', 'валик', 'массажн', 'йог', 'пилатес',
                                  'диск', 'блок для йоги']):
        return 'cat_sport'
    if any(w in name_l for w in ['продукт', 'еда', 'вода', 'напит', 'зефир', 'торт',
                                  'шоколад', 'конфет', 'коркунов']):
        return 'cat_food'
    if any(w in name_l for w in ['пакет', 'предохрани', 'автомоби']):
        return 'cat_auto'
    if any(w in name_l for w in ['ватрушк', 'тюбинг']):
        return 'cat_other'
    if any(w in name_l for w in ['турка', 'кофевар']):
        return 'cat_home_kitchen'
    if any(w in name_l for w in ['книг']):
        return 'cat_culture_books'
    return 'cat_other'


def import_all():
    conn = sqlite3.connect(DB_PATH)
    for suffix in ['', 's']:
        pass  # iterate both dirs

    txt_files = sorted([f for f in os.listdir(TEXT_DIR) if f.endswith('.txt')])
    total_purchases = 0
    total_items = 0
    total_skipped = 0

    for fname in txt_files:
        txt_path = os.path.join(TEXT_DIR, fname)

        # Read text
        with open(txt_path, 'r') as f:
            text = f.read()

        # Extract cheque number
        ch_num = re.search(r'Кассовый чек №\s*(\d+)', text)
        cheque_date_str = None

        # Try pdfplumber first
        pdf_name = fname.replace('.txt', '.pdf')
        pdf_path = os.path.join(CHEQUE_DIR, pdf_name)
        items = []
        total_amount = None
        date_str = None
        used_pdfplumber = False

        if HAS_PDFPLUMBER and os.path.exists(pdf_path):
            items, total_amount = parse_pdf_with_pdfplumber(pdf_path)
            if items or total_amount:
                used_pdfplumber = True
                # Extract date from text (pdfplumber may not have it)
                dm = re.search(r'(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})', text)
                if dm:
                    date_str = f'{dm.group(1)} {dm.group(2)}'
                    cheque_date_str = dm.group(1)

        if not used_pdfplumber:
            # Fallback to text parsing
            items_text, total_amount, date_str = parse_cheque_text(text)
            if date_str:
                cheque_date_str = date_str[:10]
            items = [(i, None) for i in (items_text or [])]

        if not date_str:
            print(f'  SKIP {fname}: no date')
            continue

        cheque_id = f"ozon_cheque_{ch_num.group(1)}_{date_str}" if ch_num else fname

        # Check if already imported
        existing = conn.execute(
            "SELECT id FROM cheques_log WHERE email_uid = ?", (cheque_id,)
        ).fetchone()
        if existing:
            total_skipped += 1
            continue

        # Refund?
        if 'Возврат прихода' in text or 'Возврат расхода' in text:
            conn.execute("""
                INSERT INTO cheques_log (email_uid, source, cheque_date, subject, receipt_url)
                VALUES (?, ?, ?, ?, ?)
            """, (cheque_id, 'ozon_pdf', date_str,
                  f'Возврат чек №{ch_num.group(1) if ch_num else ""}', txt_path))
            continue

        # Create purchase
        cur = conn.execute("""
            INSERT INTO purchases (purchase_date, total_amount, source, store_name,
                                   email_message_id, receipt_url, notes, data_origin)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ozon_pdf_cheque')
        """, (date_str[:10], total_amount, 'ozon', 'Ozon',
              cheque_id, txt_path, f'Чек №{ch_num.group(1) if ch_num else ""}'))
        purchase_id = cur.lastrowid

        # Log cheque
        conn.execute("""
            INSERT INTO cheques_log (email_uid, source, cheque_date, subject, receipt_url)
            VALUES (?, ?, ?, ?, ?)
        """, (cheque_id, 'ozon_pdf', date_str,
              f'Чек №{ch_num.group(1) if ch_num else ""} — {date_str}', txt_path))

        # Create items with prices
        for item_data in items:
            if used_pdfplumber:
                item_name, item_price = item_data
            else:
                item_name, item_price = item_data[0], None

            if not item_name or len(item_name) < 3:
                continue

            cat_id = guess_category(item_name)
            date_short = date_str[:10]

            prefix = item_name[:30].replace('%', '=%')
            existing_item = conn.execute(
                "SELECT id FROM items WHERE name LIKE ? AND deleted_at IS NULL LIMIT 1",
                (prefix + '%',)
            ).fetchone()

            if existing_item:
                item_id = existing_item[0]
                conn.execute("""
                    UPDATE items SET purchase_id = ?, purchase_date = ?, purchase_price = ?,
                                     purchase_source = 'ozon', data_origin = 'ozon_pdf_cheque'
                    WHERE id = ?
                """, (purchase_id, date_short, item_price, item_id))
            else:
                cur2 = conn.execute("""
                    INSERT INTO items (name, category_id, status, purchase_id, purchase_source,
                                       purchase_date, purchase_price, data_origin)
                    VALUES (?, ?, 'in_use', ?, 'ozon', ?, ?, 'ozon_pdf_cheque')
                """, (item_name, cat_id, purchase_id, date_short, item_price))
                item_id = cur2.lastrowid

            # Log in recognized
            conn.execute("""
                INSERT INTO recognized_items_log (source_file, source_type, recognized_product,
                                                   confidence, notes, matched_item_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (fname, 'ozon_pdf_fiscal', item_name, 'high',
                  f'Из чека №{ch_num.group(1) if ch_num else ""} ({date_str})',
                  item_id))
            total_items += 1

        total_purchases += 1
        price_str = f'{total_amount:.0f} ₽' if total_amount else '? ₽'
        print(f'  OK {fname}: {date_str[:10]} | {len(items)} товаров | {price_str}' +
              (' [pdfplumber]' if used_pdfplumber else ''))

    conn.commit()
    conn.close()
    print(f'\nСоздано покупок: {total_purchases}, товаров: {total_items}, пропущено (уже есть): {total_skipped}')


def check_ozon_email_cheques(limit=10):
    """
    Parse Ozon email cheques (HTML-based) from IMAP inbox.
    Extracts items + prices from structured HTML tables.
    """
    import imaplib
    import email
    from email.header import decode_header
    import html
    from bs4 import BeautifulSoup

    conn = sqlite3.connect(DB_PATH)

    mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
    mail.login('yu.v.artamonov@gmail.com', '[REDACTED_OLD_GMAIL_APP_PASSWORD]')
    mail.select('INBOX')

    imported = 0

    for sender in ['sender.ozon.ru']:
        status, ids = mail.search(None, f'(FROM "{sender}")')
        if status != 'OK' or not ids[0]:
            continue

        for mid in ids[0].split()[-limit:]:
            fstatus, data = mail.fetch(mid, '(RFC822)')
            if fstatus != 'OK':
                continue

            msg = email.message_from_bytes(data[0][1])
            subject_raw = msg['Subject']
            subject = ''
            if subject_raw:
                parts = decode_header(subject_raw)
                for p, enc in parts:
                    if isinstance(p, bytes):
                        try:
                            subject += p.decode(enc or 'utf-8', errors='replace')
                        except:
                            subject += p.decode('utf-8', errors='replace')
                    else:
                        subject += str(p)

            if 'ваш чек' not in subject.lower():
                continue

            msg_uid = msg.get('Message-ID', '').strip('<>') or str(mid)
            date = msg['Date']

            # Already imported?
            existing = conn.execute(
                "SELECT id FROM purchases WHERE email_message_id = ? AND deleted_at IS NULL",
                (msg_uid,)
            ).fetchone()
            if existing:
                continue

            # Extract HTML body
            html_body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/html':
                        try:
                            html_body += part.get_payload(decode=True).decode('utf-8', errors='replace')
                        except:
                            pass
            else:
                try:
                    content = msg.get_payload(decode=True)
                    if content:
                        html_body += content.decode('utf-8', errors='replace')
                except:
                    pass

            if not html_body:
                continue

            # Parse HTML
            soup = BeautifulSoup(html_body, 'html.parser')
            items_found = []
            total_found = None

            # Try tables
            tables = soup.find_all('table')
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    cell_texts = [c.get_text(strip=True) for c in cells]

                    # Look for price patterns
                    for i, ct in enumerate(cell_texts):
                        pm = re.search(r'([\d\s]+[.,]\d{2})\s*(?:₽|руб)', ct)
                        if pm and i > 0:
                            # Previous cell might be item name
                            name = cell_texts[i - 1] if i > 0 else ''
                            if name and len(name) > 3:
                                price = float(pm.group(1).replace(' ', '').replace(',', '.'))
                                if 'доставк' not in name.lower():
                                    items_found.append((name, price))

                    # Total line
                    for ct in cell_texts:
                        tm = re.search(r'(?:Итого|Всего|Сумма)\s*([\d\s]+[.,]\d{2})', ct, re.IGNORECASE)
                        if tm:
                            total_found = float(tm.group(1).replace(' ', '').replace(',', '.'))

            # Fallback: parse entire text for prices
            if not items_found:
                all_text = soup.get_text()
                lines = all_text.split('\n')
                current_name = None
                for line in lines:
                    line = line.strip()
                    pm = re.search(r'(?:1|2|3|4|5|6|7|8|9)\s*x\s*([\d\s]+[.,]\d{2})', line)
                    if pm and current_name:
                        price = float(pm.group(1).replace(' ', '').replace(',', '.'))
                        if 'доставк' not in current_name.lower():
                            items_found.append((current_name, price))
                        current_name = None
                        continue
                    if line and len(line) > 5 and not re.match(r'^[\d\s.,₽руб]+$', line):
                        current_name = line

            if not items_found:
                continue

            # Create purchase
            purchase_date = date[:10] if date and len(date) >= 10 else None
            cur = conn.execute("""
                INSERT INTO purchases (purchase_date, total_amount, source, store_name,
                                       email_message_id, notes, data_origin)
                VALUES (?, ?, ?, ?, ?, ?, 'ozon_email')
            """, (purchase_date, total_found, 'ozon', 'Ozon', msg_uid, subject[:200]))
            purchase_id = cur.lastrowid

            # Log cheque
            conn.execute("""
                INSERT INTO cheques_log (email_uid, source, cheque_date, subject, receipt_url)
                VALUES (?, ?, ?, ?, ?)
            """, (msg_uid, 'ozon_email', date, subject, None))

            # Create items
            for item_name, item_price in items_found:
                cat_id = guess_category(item_name)
                prefix = item_name[:30].replace('%', '=%')
                existing_item = conn.execute(
                    "SELECT id FROM items WHERE name LIKE ? AND deleted_at IS NULL LIMIT 1",
                    (prefix + '%',)
                ).fetchone()

                if existing_item:
                    item_id = existing_item[0]
                    conn.execute("""
                        UPDATE items SET purchase_id = ?, purchase_date = ?, purchase_price = ?,
                                         purchase_source = 'ozon', data_origin = 'ozon_email'
                        WHERE id = ?
                    """, (purchase_id, purchase_date, item_price, item_id))
                else:
                    cur2 = conn.execute("""
                        INSERT INTO items (name, category_id, status, purchase_id, purchase_source,
                                           purchase_date, purchase_price, data_origin)
                        VALUES (?, ?, 'in_use', ?, 'ozon', ?, ?, 'ozon_email')
                    """, (item_name, cat_id, purchase_id, purchase_date, item_price))
                    item_id = cur2.lastrowid

                conn.execute("""
                    INSERT INTO recognized_items_log (source_file, source_type, recognized_product,
                                                       confidence, notes, matched_item_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (f'email_{msg_uid[:20]}', 'ozon_email_html', item_name, 'high',
                      f'Из email-чека Ozon ({date})', item_id))

            imported += 1
            print(f'  OK email: {purchase_date} | {len(items_found)} товаров | {total_found:.0f} ₽' if total_found
                  else f'  OK email: {purchase_date} | {len(items_found)} товаров')

    mail.logout()
    conn.commit()
    conn.close()
    print(f'\nИмпортировано email-чеков: {imported}')
    return imported


def main():
    print('=== Consumption Agent — PDF/Email Cheque Importer v2 (with prices) ===\n')

    print('1. Импорт PDF-чеков...')
    import_all()

    print('\n2. Импорт email-чеков...')
    try:
        check_ozon_email_cheques(limit=10)
    except ImportError:
        print('  Пропущено: нужен beautifulsoup4 (pip install beautifulsoup4)')


if __name__ == '__main__':
    main()
