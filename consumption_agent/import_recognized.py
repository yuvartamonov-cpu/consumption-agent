#!/usr/bin/env python3
"""
Импорт распознанных товаров в consumption.db

- чеки/PDF → purchase_items
- скрины → recognized_items
"""
import csv, sqlite3, os
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # purchase_items — из parse_cheque.py
    c.execute('''
        CREATE TABLE IF NOT EXISTS purchase_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER REFERENCES purchases(id),
            name TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            price REAL,
            total REAL,
            seller TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    
    # recognized_items — для скринов/сырых распознаваний
    c.execute('''
        CREATE TABLE IF NOT EXISTS recognized_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            source_type TEXT NOT NULL,
            recognized_product TEXT NOT NULL,
            confidence TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    
    conn.commit()
    return conn


def import_purchase_items():
    conn = init_db()
    c = conn.cursor()
    
    # Чеки → purchase_items
    csv_path = os.path.join(os.path.dirname(__file__), 'recognized_products_2026-04-28.csv')
    with open(csv_path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    
    # Сначала создадим покупки для чеков
    for row in rows:
        if row['source_type'] != 'pdf_cheque':
            continue
        
        # Ищем purchase_id по email_uid или создаём
        email_uid = row['source_file'].split('---')[1].split('.pdf')[0]
        c.execute('SELECT id FROM purchases WHERE email_uid = ?', (email_uid,))
        purchase = c.fetchone()
        if not purchase:
            c.execute('''
                INSERT INTO purchases (purchase_date, source, store_name, email_uid, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', ('2026-04-28', 'ozon', 'Ozon', email_uid, 'Импортировано из чека'))
            purchase_id = c.lastrowid
        else:
            purchase_id = purchase[0]
        
        # Добавляем товар
        c.execute('''
            INSERT INTO purchase_items (purchase_id, name, quantity, price, total)
            VALUES (?, ?, ?, ?, ?)
        ''', (purchase_id, row['recognized_product'], 1, None, None))
    
    conn.commit()
    print(f'Импортировано {len(rows)} товаров из чеков в purchase_items')


def import_recognized_items():
    conn = init_db()
    c = conn.cursor()
    
    # Скрины → recognized_items
    csv_path = os.path.join(os.path.dirname(__file__), 'recognized_products_from_screens_2026-04-28.csv')
    with open(csv_path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    
    # Удаляем старые записи с тем же source_file
    for row in rows:
        c.execute('DELETE FROM recognized_items WHERE source_file = ?', (row['source'],))
    
    # Добавляем новые
    for row in rows:
        c.execute('''
            INSERT INTO recognized_items (source_file, source_type, recognized_product, confidence, notes)
            VALUES (?, ?, ?, ?, ?)
        ''', (row['source'], 'screen', row['recognized_product'], row['confidence'], row.get('notes', '')))
    
    conn.commit()
    print(f'Импортировано {len(rows)} товаров со скринов в recognized_items')


if __name__ == '__main__':
    import_purchase_items()
    import_recognized_items()
