#!/usr/bin/env python3
"""
Парсинг HTML-страницы заказов Яндекс Еды / Лавки и импорт в БД
"""
import sys
sys.path.insert(0, '/home/yuri_artamonov/.openclaw/workspace/consumption_agent')

import sqlite3, re, json
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path

DB_PATH = '/home/yuri_artamonov/.openclaw/workspace/consumption_agent/consumption.db'
HTML_PATH = '/home/yuri_artamonov/.openclaw/workspace/consumption_agent/Yandex_Lavka_orders_files.html'

def parse_price(text):
    """Извлекает число из строки вида '2 737 ₽'"""
    if not text:
        return None
    clean = re.sub(r'[^\d]', '', text)
    try:
        return int(clean)
    except:
        return None

def parse_date(text):
    """Пытается распарсить дату из текста"""
    if not text:
        return None
    # Примеры: "12 мая 2025", "вчера", "сегодня", "3 апр"
    months = {
        'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'мая': 5, 'июн': 6,
        'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12
    }
    text = text.lower().strip()
    if 'сегодня' in text:
        return datetime.now().strftime('%Y-%m-%d')
    if 'вчера' in text:
        return (datetime.now() - __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d')
    
    m = re.search(r'(\d{1,2})\s+(янв|фев|мар|апр|мая|июн|июл|авг|сен|окт|ноя|дек)\w*\s*(\d{4})?', text)
    if m:
        day = int(m.group(1))
        mon = months.get(m.group(2)[:3], 1)
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        try:
            return datetime(year, mon, day).strftime('%Y-%m-%d')
        except:
            pass
    return None

def main():
    print(f"Читаю {HTML_PATH}...")
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')
    
    # Ищем все элементы с ценой
    price_spans = soup.find_all('span', class_=lambda x: x and 'UiKitPrice' in ' '.join(x) if isinstance(x, list) else 'UiKitPrice' in str(x))
    print(f"Найдено элементов с ценой: {len(price_spans)}")
    
    orders = []
    seen = set()
    
    for span in price_spans:
        price = parse_price(span.get_text())
        if not price or price < 100:
            continue
        
        # Поднимаемся вверх, ищем карточку заказа
        parent = span.parent
        for _ in range(8):
            if not parent:
                break
            text = parent.get_text(" ", strip=True)
            # Ищем признаки карточки заказа
            if ('доставлен' in text.lower() or 'готов' in text.lower() or 
                'отменён' in text.lower() or 'в пути' in text.lower()):
                
                # Извлекаем дату
                date_text = ""
                date_el = parent.find(string=re.compile(r'(доставлен|готов|отмен|вчера|сегодня|\d{1,2}\s+(янв|фев|мар|апр|мая|июн|июл|авг|сен|окт|ноя|дек))', re.I))
                if date_el:
                    date_text = str(date_el).strip()
                
                # Извлекаем название ресторана/магазина
                title = ""
                for el in parent.find_all(['h3', 'h4', 'div', 'span']):
                    t = el.get_text(strip=True)
                    if t and len(t) > 3 and len(t) < 80 and not t[0].isdigit():
                        title = t
                        break
                
                # Состав заказа
                items = []
                for item_el in parent.find_all(string=re.compile(r'^\d+×')):
                    items.append(item_el.strip())
                
                order_key = f"{date_text}_{price}_{title[:30]}"
                if order_key in seen:
                    continue
                seen.add(order_key)
                
                orders.append({
                    'date': parse_date(date_text) or '',
                    'price': price,
                    'title': title[:100],
                    'items': items[:10],
                    'raw': text[:300],
                    'source': 'yandex_eda_html'
                })
                break
            parent = parent.parent
    
    print(f"Распознано заказов: {len(orders)}")
    
    if not orders:
        print("Не удалось распознать заказы. Возможно, нужно доработать парсер.")
        return
    
    # Импорт в БД
    conn = sqlite3.connect(DB_PATH)
    imported = 0
    
    for o in orders:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO purchases 
                (purchase_date, total_amount, source, store_name, notes, data_origin)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                o['date'],
                o['price'],
                o['source'],
                o['title'] or 'Яндекс Еда',
                json.dumps(o['items'], ensure_ascii=False) if o['items'] else o['raw'],
                'yandex_eda_html'
            ))
            imported += 1
        except Exception as e:
            print(f"  Ошибка: {e}")
    
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL").fetchone()[0]
    conn.close()
    
    print(f"\nИмпортировано: {imported} новых заказов")
    print(f"Всего покупок в БД: {total}")

if __name__ == '__main__':
    main()
