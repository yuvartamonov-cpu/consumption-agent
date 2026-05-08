#!/usr/bin/env python3
"""Парсинг PDF-чека Ozon и запись в БД consumption_agent."""
import re, sys, os, sqlite3

DB_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(DB_DIR, 'consumption.db')

def parse_cheque(text):
    """Извлекает данные из текста PDF-чека Ozon."""
    result = {}
    
    # Дата: "14.02.2026 16:49"
    m = re.search(r'Кассовый чек №\S+\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})', text)
    if m:
        parts = m.group(1).split('.')
        result['date'] = f'{parts[2]}-{parts[1]}-{parts[0]}'
        result['time'] = m.group(2)
    
    # ИТОГО
    m = re.search(r'ИТОГ.*?(\d+[\d\s]*\d)\s', text)
    if m:
        result['total'] = float(m.group(1).replace(' ', ''))
    
    # Товары
    items = []
    for match in re.finditer(r'(\d+)\.\s*(.*?)\s+(\d+)\s*x\s*(\d+[\d\s]*\d)\s*≡(\d+[\d\s]*\d)', text):
        items.append({
            'num': int(match.group(1)),
            'name': match.group(2).strip(),
            'qty': int(match.group(3)),
            'price': float(match.group(4).replace(' ', '')),
            'total': float(match.group(5).replace(' ', '')),
        })
    
    result['items'] = items
    result['item_count'] = len(items)
    
    return result

def save_purchase(data):
    """Сохраняет в SQLite."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL,
            source TEXT DEFAULT 'ozon',
            store_name TEXT DEFAULT 'Ozon',
            total_amount REAL,
            source_text TEXT,
            data_origin TEXT DEFAULT 'local',
            created_at TEXT DEFAULT (datetime("now"))
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS purchase_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER REFERENCES purchases(id),
            name TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            price REAL,
            total REAL,
            seller TEXT,
            created_at TEXT DEFAULT (datetime("now"))
        )
    ''')
    
    c.execute(
        'INSERT INTO purchases (purchase_date, source, store_name, total_amount, source_text) VALUES (?, ?, ?, ?, ?)',
        (data['date'], 'ozon', 'Ozon', data.get('total'), data.get('raw_text', '')[:500])
    )
    purchase_id = c.lastrowid
    
    for item in data.get('items', []):
        c.execute(
            'INSERT INTO purchase_items (purchase_id, name, quantity, price, total) VALUES (?, ?, ?, ?, ?)',
            (purchase_id, item['name'], item['qty'], item['price'], item['total'])
        )
    
    conn.commit()
    conn.close()
    return purchase_id

def main():
    if len(sys.argv) < 2:
        print('Использование: python3 parse_cheque.py <файл.pdf> [файл2.pdf ...]')
        sys.exit(1)
    
    for path in sys.argv[1:]:
        with open(path, 'rb') as f:
            raw = f.read()
        
        # Извлекаем текст из PDF (простой способ — если PDF текстовый)
        text = raw.decode('latin-1', errors='replace')
        # Чистим мусор
        text = re.sub(r'[^\x20-\x7E\u0400-\u04FF\u0500-\u052F\n]', '', text)
        
        data = parse_cheque(text)
        data['raw_text'] = text
        
        pid = save_purchase(data)
        print(f'✓ {data["date"]} | {data.get("item_count", 0)} товаров | {data.get("total", "?")} ₽ | id={pid}')
        for item in data.get('items', []):
            print(f'   {item["name"][:60]} — {item["price"]} ₽ x {item["qty"]}')

if __name__ == '__main__':
    main()
