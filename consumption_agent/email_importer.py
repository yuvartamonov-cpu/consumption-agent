import os
#!/usr/bin/env python3
"""
Consumption Agent — email-импорт покупок и импорт распознанных товаров.
v2 — исправлен pipeline:
  - Фильтрация чеков: только subject~"Ваш чек"
  - Парсинг суммы из письма/чека
  - Заполнение matched_item_id в recognized_items_log
  - data_origin различается по источнику
  - fuzzy-дедупликация через LIKE (first 20 chars)
"""
import sqlite3
import csv
import os
import imaplib
import email
import re
from email.header import decode_header

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')

IMAP_CONFIG = {
    'host': 'imap.gmail.com',
    'port': 993,
    'user': 'yu.v.artamonov@gmail.com',
    os.getenv('GMAIL_APP_PASSWORD', '').replace('"', '').replace(' ', ''),
}


def guess_category(name):
    """Определяет категорию товара по названию."""
    name_lower = name.lower()
    if any(w in name_lower for w in ['корм', 'симпарик', 'ветеринар', 'животн', 'собак', 'кошк']):
        return 'cat_pets'
    if any(w in name_lower for w in ['лекарств', 'таблетк', 'витамин', 'бад']):
        return 'cat_health_med'
    if any(w in name_lower for w in ['премиум', 'подписк']):
        return 'cat_subscriptions'
    if any(w in name_lower for w in ['стол', 'стул', 'кроват', 'шкаф', 'мебель']):
        return 'cat_home_furn'
    if any(w in name_lower for w in ['телефон', 'наушник', 'зарядк']):
        return 'cat_tech'
    if any(w in name_lower for w in ['крем', 'шампун', 'мыло', 'лосьон']):
        return 'cat_cosmetics'
    if any(w in name_lower for w in ['кросовк', 'обувь', 'ботинк', 'туфл']):
        return 'cat_clo_shoes'
    if any(w in name_lower for w in ['плать', 'рубашк', 'футболк', 'джинс']):
        return 'cat_clo_everyday'
    if any(w in name_lower for w in ['продукт', 'еда', 'вода', 'напит']):
        return 'cat_food'
    return None


def decode_subject(msg):
    subj = msg['Subject']
    if subj is None:
        return ''
    decoded = decode_header(subj)
    result = []
    for part, enc in decoded:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(enc or 'utf-8', errors='replace'))
            except LookupError:
                result.append(part.decode('utf-8', errors='replace'))
        else:
            result.append(str(part))
    return ''.join(result)


def find_or_create_item(conn, name, cat_id, purchase_id=None, purchase_date=None, data_origin='local'):
    """
    Ищет товар по названию (fuzzy — первые 30 символов через LIKE).
    Если не найден — создаёт.
    Возвращает (item_id, is_new).
    """
    prefix = name.strip()[:30]
    if len(prefix) < 5:
        prefix = name.strip()[:50]

    row = conn.execute(
        "SELECT id FROM items WHERE name LIKE ? ESCAPE '=' AND deleted_at IS NULL",
        (prefix.replace('%', '=%') + '%',)
    ).fetchone()

    if row:
        # Обновляем purchase_id/date если есть
        if purchase_id is not None:
            conn.execute('UPDATE items SET purchase_id = ?, purchase_date = ? WHERE id = ?',
                        (purchase_id, purchase_date, row[0]))
        return row[0], False
    else:
        cur = conn.execute('''
            INSERT INTO items (name, category_id, status, purchase_id, purchase_source,
                               purchase_date, data_origin)
            VALUES (?, ?, 'in_use', ?, 'ozon', ?, ?)
        ''', (name.strip(), cat_id, purchase_id, purchase_date, data_origin))
        return cur.lastrowid, True


def import_file_csv():
    """Импорт из recognized_products_2026-04-28.csv (pdf_cheque)."""
    path = os.path.join(os.path.dirname(__file__), 'recognized_products_2026-04-28.csv')
    if not os.path.exists(path):
        print(f'  Файл не найден: {path}')
        return 0

    conn = sqlite3.connect(DB_PATH)
    count = 0
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            product = row.get('recognized_product', '').strip()
            if not product:
                continue

            # Записываем в recognized_items_log
            cur = conn.execute('''
                INSERT INTO recognized_items_log (source_file, source_type, recognized_product, confidence, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (row.get('source_file', ''), 'pdf_cheque',
                  product, row.get('confidence', 'medium'), row.get('notes', '')))
            rec_id = cur.lastrowid

            # Создаём/находим item
            cat_id = guess_category(product)
            item_id, is_new = find_or_create_item(conn, product, cat_id, data_origin='pdf_cheque_recognized')

            # Привязываем recognized → item
            conn.execute('UPDATE recognized_items_log SET matched_item_id = ? WHERE id = ?', (item_id, rec_id))
            count += 1

    conn.commit()
    conn.close()
    print(f'  {count} товаров из recognized_products CSV (pdf_cheque).')
    return count


def import_screens_csv():
    """Импорт из recognized_products_from_screens_2026-04-28.csv (screen)."""
    path = os.path.join(os.path.dirname(__file__), 'recognized_products_from_screens_2026-04-28.csv')
    if not os.path.exists(path):
        print(f'  Файл не найден: {path}')
        return 0

    conn = sqlite3.connect(DB_PATH)
    count = 0
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Пробуем разные имена колонок
            product = (row.get('recognized_product') or row.get('product_name') or '').strip()
            if not product:
                continue
            source_file = row.get('source_file') or row.get('source') or 'screen'

            cur = conn.execute('''
                INSERT INTO recognized_items_log (source_file, source_type, recognized_product, confidence, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (source_file, 'screen', product,
                  row.get('confidence', 'medium'), row.get('notes', '')))
            rec_id = cur.lastrowid

            cat_id = guess_category(product)
            item_id, _ = find_or_create_item(conn, product, cat_id, data_origin='screen_recognized')
            conn.execute('UPDATE recognized_items_log SET matched_item_id = ? WHERE id = ?', (item_id, rec_id))
            count += 1

    conn.commit()
    conn.close()
    print(f'  {count} товаров из screens CSV.')
    return count


def check_ozon_emails(limit=20):
    """
    Проверяет почту на письма от Ozon.
    Создаёт purchase ТОЛЬКО для писем с subject "Ваш чек".
    Извлекает сумму из тела письма.
    """
    conn = sqlite3.connect(DB_PATH)

    mail = imaplib.IMAP4_SSL(IMAP_CONFIG['host'], IMAP_CONFIG['port'])
    mail.login(IMAP_CONFIG['user'], IMAP_CONFIG['password'])
    mail.select('INBOX')

    import_count = 0
    for sender in ['sender.ozon.ru']:
        status, ids = mail.search(None, f'(FROM "{sender}")')
        if status != 'OK' or not ids[0]:
            continue

        for mid in ids[0].split()[-limit:]:
            fstatus, data = mail.fetch(mid, '(RFC822)')
            if fstatus != 'OK':
                continue

            msg = email.message_from_bytes(data[0][1])
            subject = decode_subject(msg)
            date = msg['Date']

            # Извлекаем body
            body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct in ('text/plain', 'text/html'):
                        try:
                            body += part.get_payload(decode=True).decode('utf-8', errors='replace')
                        except:
                            pass
            else:
                try:
                    body += msg.get_payload(decode=True).decode('utf-8', errors='replace')
                except:
                    pass

            msg_uid = msg.get('Message-ID', '').strip('<>') or str(mid)

            # Пропускаем, если уже импортирован
            existing = conn.execute(
                'SELECT id FROM cheques_log WHERE email_uid = ?', (msg_uid,)
            ).fetchone()
            if existing:
                continue

            # Сохраняем ВСЕ письма Ozon в cheques_log (для аудита)
            receipt_urls = re.findall(r'https?://[^\s<>"]*ozon[^\s<>"]*check[^\s<>"]*', body)
            receipt_url = receipt_urls[0] if receipt_urls else None

            conn.execute('''
                INSERT OR IGNORE INTO cheques_log (email_uid, source, cheque_date, subject, receipt_url)
                VALUES (?, ?, ?, ?, ?)
            ''', (msg_uid, 'ozon', date, subject, receipt_url))

            # Создаём purchase ТОЛЬКО для реальных чеков
            if 'ваш чек' not in subject.lower():
                continue

            # Парсим сумму (Ozon чеки содержат "Итого" или сумму в рублях)
            total_amount = None
            # Ищем "Итого: X XXX ₽" или "Сумма: X XXX.XX руб."
            patterns = [
                r'(?:Итого|Сумма|К оплате|Всего)[:\s]*([\d\s]+[.,]?\d*)\s*(?:₽|руб|р\.)',
                r'([\d\s]+[.,]?\d*)\s*₽',
            ]
            for pat in patterns:
                m = re.search(pat, body, re.IGNORECASE)
                if m:
                    try:
                        total_amount = float(m.group(1).replace(' ', '').replace(',', '.'))
                    except ValueError:
                        pass
                    break

            purchase_date = date[:10] if date and len(date) >= 10 else date[:7] if date else None

            cur = conn.execute('''
                INSERT INTO purchases (purchase_date, total_amount, source, store_name,
                                       email_message_id, receipt_url, notes, data_origin)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ozon_email')
            ''', (purchase_date, total_amount, 'ozon', 'Ozon',
                  msg_uid, receipt_url, subject[:200]))

            purchase_id = cur.lastrowid
            import_count += 1
            print(f'    Чек: {purchase_date} | {total_amount:.0f} ₽ | {subject[:50]}' if total_amount
                  else f'    Чек: {purchase_date} | сумма не найдена | {subject[:50]}')

    mail.logout()
    conn.commit()
    conn.close()
    print(f'  Импортировано {import_count} новых чеков Ozon (purchases созданы).')
    return import_count


def show_stats(conn):
    items = conn.execute('SELECT COUNT(*) FROM items WHERE deleted_at IS NULL').fetchone()[0]
    purchases = conn.execute('SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL').fetchone()[0]
    rec = conn.execute('SELECT COUNT(*) FROM recognized_items_log').fetchone()[0]
    rec_linked = conn.execute('SELECT COUNT(*) FROM recognized_items_log WHERE matched_item_id IS NOT NULL').fetchone()[0]
    items_with_purchase = conn.execute('SELECT COUNT(*) FROM items WHERE purchase_id IS NOT NULL').fetchone()[0]
    print(f'\n=== Итог ===')
    print(f'Товаров: {items}, из них с purchase_id: {items_with_purchase}')
    print(f'Покупок: {purchases}')
    print(f'Распознано: {rec} (привязано к items: {rec_linked})')


def main():
    print('=== Consumption Agent v2 — Email + File Importer ===')
    print()

    # 1. Импорт из CSV (с привязкой к items)
    print('1. Импорт из recognized_products CSV...')
    import_file_csv()
    import_screens_csv()

    # 2. Импорт из почты Ozon
    print('\n2. Проверка почты Ozon...')
    check_ozon_emails(limit=20)

    # 3. Статистика
    conn = sqlite3.connect(DB_PATH)
    show_stats(conn)
    conn.close()


if __name__ == '__main__':
    main()
