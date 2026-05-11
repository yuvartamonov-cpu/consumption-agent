"""
cheque_parser.py — Универсальный парсер чеков из любых источников.
Поддерживает:
  - Фискальные чеки ОФД (Самокат) — HTML
  - Чеки из писем (Ozon, Яндекс) — HTML/text
  - OCR/скриншоты из Telegram — зашумлённый текст
  - PDF-чеки (Ozon) — plain text после pdftotext

Использование:
  from cheque_parser import parse_cheque, parse_cheque_text

  # Из HTML письма
  items = parse_cheque(html_text, source='samokat_ofd')
  # или
  items = parse_cheque(html_text, source='ozon')

  # Из plain text (OCR, PDF)
  items = parse_cheque_text(plain_text)
"""

import re
import html as html_mod
from typing import Optional


def _clean_text(text: str) -> str:
    """Очищает текст от HTML-тегов, лишних пробелов, спецсимволов."""
    text = re.sub(r'<[^>]+>', '\n', text)
    text = html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _parse_ofd_text(lines: list[str]) -> list[dict]:
    """Парсит фискальный чек ОФД (самокат, по struct из таблицы)."""
    items = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'^(\d+):\s*(.+)$', line)
        if not m:
            i += 1
            continue
        
        name = m.group(2).strip()
        i += 1
        qty, price, total = 1, 0.0, 0.0
        
        while i < len(lines) and not re.match(r'^\d+:', lines[i]) and lines[i] != 'ИТОГ':
            cur = lines[i]
            if cur.isdigit() and i + 1 < len(lines) and lines[i + 1] == 'шт.':
                qty = int(cur)
            if cur == 'x' and i + 1 < len(lines) and re.match(r'^[\d.]+$', lines[i + 1]):
                price = float(lines[i + 1])
            if 'Общая стоимость' in cur and i + 1 < len(lines) and re.match(r'^[\d.]+$', lines[i + 1]):
                total = float(lines[i + 1])
            i += 1
        
        if price > 0 and len(name) > 2:
            items.append({'name': name, 'qty': qty, 'price': price, 'total': total or price * qty})
    return items


def _parse_ozon_text(text: str) -> list[dict]:
    """Парсит чек Ozon (из HTML письма или PDF)."""
    items = []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    # Ozon: "N. Название" или "1.   Товар — 1234.56"
    # Формат из письма: "1.   Товар — 1234.56" или "N. Товар 1234,56 ₽"
    for line in lines:
        # "N. Название — цена"
        m = re.match(r'^\d+[\.\)]\s+(.+?)\s*[—\-–]\s*(\d[\d\s.,]*\d)\s*(?:₽|руб|р)?\s*$', line)
        if m:
            name = m.group(1).strip()
            price_str = m.group(2).replace(' ', '').replace(',', '.')
            try:
                price = float(price_str)
                if len(name) > 2 and not re.match(r'^\d+$', name):
                    items.append({'name': name, 'qty': 1, 'price': price, 'total': price})
            except:
                pass
            continue
        
        # Альтернатива: "N. Товар" далее строка с ценой
        m2 = re.match(r'^\d+[\.\)]\s+(.+)$', line)
        if m2 and len(m2.group(1).strip()) > 2 and not re.match(r'^\d', m2.group(1)):
            items.append({'name': m2.group(1).strip()})
    
    # Если нашли названия без цен — ищем цены рядом
    # (для PDF где цена на следующей строке)
    cleaned = []
    for item in items:
        if 'price' not in item or item['price'] == 0:
            # Поищем цену после названия
            continue
        if item['price'] > 0 and len(item['name']) > 2:
            cleaned.append(item)
    
    return cleaned if cleaned else items


def _parse_generic_text(text: str) -> list[dict]:
    """
    Универсальный парсер для любого чека (OCR, скрин, PDF).
    Ищет паттерны:
      - "N. Название — цена ₽"
      - "Название N × цена = сумма"
      - "Название цена"
      - "Итого: сумма"
    """
    items = []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    for line in lines:
        # 1. "N. Название — 123.45 ₽" или "N. Название — 123,45"
        m = re.match(r'^\d*[\.\)]?\s*(.+?)\s*[—\-–]\s*(\d[\d\s.,]*\d)\s*(?:₽|руб|р|рублей)?\s*$', line)
        if m:
            name = m.group(1).strip()
            price_str = m.group(2).replace(' ', '').replace(',', '.')
            try:
                price = float(price_str)
                if len(name) > 2 and not re.match(r'^\d+$', name) and price < 1_000_000:
                    items.append({'name': name, 'qty': 1, 'price': price, 'total': price})
                    continue
            except:
                pass
        
        # 2. "Название, N × X руб" (товар с количеством)
        m2 = re.match(r'(.+?)\s*[,:]\s*(\d+)\s*[xх×*]\s*(\d[\d\s.,]*\d)\s*(?:₽|руб|р)?\s*', line)
        if m2:
            name = m2.group(1).strip()
            qty = int(m2.group(2))
            price_str = m2.group(3).replace(' ', '').replace(',', '.')
            try:
                price = float(price_str)
                if len(name) > 2 and price < 1_000_000:
                    items.append({'name': name, 'qty': qty, 'price': price, 'total': price * qty})
                    continue
            except:
                pass
        
        # 3. "Название = 123.45" или "Название — 123.45"
        m3 = re.match(r'(.+?)\s*[=—\-–:]\s*(\d[\d\s.,]*\d)\s*(?:₽|руб|р)?\s*$', line)
        if m3:
            name = m3.group(1).strip()
            price_str = m3.group(2).replace(' ', '').replace(',', '.')
            try:
                price = float(price_str)
                if len(name) > 2 and not re.match(r'^\d+$', name) and price < 1_000_000:
                    items.append({'name': name, 'qty': 1, 'price': price, 'total': price})
                    continue
            except:
                pass
        
        # 4. OCR: "Название 123.45" (цифра в конце строки, перед ней не число)
        m4 = re.match(r'^(.{3,60}?)\s+(\d{2,6}(?:[.,]\d{1,2})?)\s*$', line)
        if m4:
            name = m4.group(1).strip()
            price_str = m4.group(2).replace(',', '.')
            # Проверяем, что это цена, а не артикул или вес
            if re.match(r'^\d{1,4}(?:[.,]\d{1,2})?$', price_str):
                try:
                    price = float(price_str)
                    if len(name) > 3 and price < 1_000_000 and price > 1:
                        # Не добавляем дубликаты
                        if not any(item['name'] == name for item in items):
                            items.append({'name': name, 'qty': 1, 'price': price, 'total': price})
                except:
                    pass
    
    return items


def _extract_total(text: str) -> Optional[float]:
    """Извлекает итоговую сумму из текста чека."""
    patterns = [
        r'ИТОГО?\s*[=:—\-–]?\s*(\d[\d\s.,]*\d)\s*(?:₽|руб|р)?',
        r'СУММА\s*[=:—\-–]?\s*(\d[\d\s.,]*\d)',
        r'Всего\s*[=:—\-–]?\s*(\d[\d\s.,]*\d)',
        r'Итог\s*[=:—\-–]?\s*(\d[\d\s.,]*\d)',
        r'Total\s*[=:—\-–]?\s*(\d[\d\s.,]*\d)',
        r'Сумма по чеку\s*[=:—\-–]?\s*(\d[\d\s.,]*\d)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(' ', '').replace(',', '.'))
            except:
                pass
    return None


def _extract_items_from_table(text: str, items: list[dict]) -> list[dict]:
    """Пост-процессинг: если итемов >0 и есть итог — проверяем сходимость."""
    total = _extract_total(text)
    if total and items:
        items_total = sum(i['total'] for i in items)
        # Если расхождение > 20%, возможно пропущены товары
        if abs(items_total - total) / total > 0.2 and len(items) < 10:
            # Попробуем докинуть метод 3 (generic)
            pass
    return items


def parse_cheque(text: str, source: str = 'auto') -> list[dict]:
    """
    Главная функция: парсит чек из любого источника.
    
    Аргументы:
        text: HTML или plain text чека
        source: 'samokat_ofd', 'ozon', 'yandex', 'auto' (определяется автоматически)
    
    Возвращает:
        list[dict]: список товаров вида {'name', 'qty', 'price', 'total'}
    """
    if source == 'auto':
        # Автоопределение по тексту
        if 'КАССОВЫЙ ЧЕК' in text and ('УМНЫЙ РИТЕЙЛ' in text or 'samokat' in text.lower()):
            source = 'samokat_ofd'
        elif 'ozon' in text.lower() or 'Интернет Решения' in text:
            source = 'ozon'
        elif 'market.yandex' in text.lower() or 'Яндекс' in text:
            source = 'yandex'
    
    # Определяем, HTML это или plain text
    is_html = bool(re.search(r'<html|<body|<table|<div', text[:500], re.I))
    
    items = []
    
    if source == 'samokat_ofd' and is_html:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, 'html.parser')
        # Ищем таблицу с полным фискальным чеком
        for table in soup.find_all('table'):
            table_text = table.get_text(separator='\n', strip=True)
            if 'КАССОВЫЙ ЧЕК' in table_text and 'ИТОГ' in table_text:
                lines = [l.strip() for l in table_text.split('\n') if l.strip()]
                items = _parse_ofd_text(lines)
                break
    
    if not items and source == 'ozon':
        # Парсим Ozon (HTML письмо или PDF)
        if is_html:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, 'html.parser')
            plain = soup.get_text(separator='\n', strip=True)
        else:
            plain = text
        items = _parse_ozon_text(plain)
    
    if not items:
        # Fallback: универсальный парсер
        if is_html:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, 'html.parser')
            plain = soup.get_text(separator='\n', strip=True)
        else:
            plain = text
        
        # Чистим
        plain = _clean_text(plain) if not plain else plain
        
        # Пробуем все методы последовательно
        items = _parse_ofd_text(plain.split('\n'))  # может сработать если структура похожа
        if not items:
            items = _parse_ozon_text(plain)
        if not items:
            items = _parse_generic_text(plain)
    
    # Пост-процессинг: проверка итога
    if items:
        items = _extract_items_from_table(text, items)
        
        # Убираем дубликаты (одинаковые названия и цены)
        seen = set()
        unique = []
        for item in items:
            key = (item['name'].lower(), round(item['price'], 2))
            if key not in seen:
                seen.add(key)
                unique.append(item)
        items = unique
    
    return items


def parse_cheque_text(plain_text: str) -> list[dict]:
    """Упрощённый вход для plain text (OCR, PDF, скриншоты)."""
    return parse_cheque(plain_text, source='auto')


def extract_metadata(text: str) -> dict:
    """Извлекает метаданные чека: дата, итог, магазин."""
    meta = {'total': None, 'date': None, 'store': None}
    
    # Итог
    meta['total'] = _extract_total(text)
    
    # Дата
    date_patterns = [
        r'(\d{2}\.\d{2}\.\d{4})',
        r'(\d{4}-\d{2}-\d{2})',
        r'(\d{2}/\d{2}/\d{4})',
    ]
    for pat in date_patterns:
        m = re.search(pat, text)
        if m:
            meta['date'] = m.group(1)
            break
    
    # Магазин
    store_patterns = [
        (r'(ozon|OZON)', 'Ozon'),
        (r'(samokat|Самокат|УМНЫЙ РИТЕЙЛ)', 'Самокат'),
        (r'(Яндекс|Yandex|market\.yandex)', 'Яндекс'),
        (r'(WB|Wildberries)', 'Wildberries'),
    ]
    for pat, name in store_patterns:
        if re.search(pat, text, re.I):
            meta['store'] = name
            break
    
    return meta


# ===== CLI =====
if __name__ == '__main__':
    import sys, json
    
    if len(sys.argv) > 1 and sys.argv[1] == '--file':
        with open(sys.argv[2], 'r') as f:
            text = f.read()
        items = parse_cheque(text, source=sys.argv[3] if len(sys.argv) > 3 else 'auto')
        meta = extract_metadata(text)
        print(json.dumps({'items': items, 'meta': meta}, ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == '--test':
        print("Running tests...")
        # Тесты
        tests = [
            ("1: Томаты Бархатный мёд, 400 г\n1\nшт.\nx\n349.00\nОбщая стоимость позиции\n349.00\n2: Харчо, 300 мл\n1\nшт.\nx\n199.00\nОбщая стоимость позиции\n199.00\nИТОГ\n=\n548.00\n", 'samokat_ofd', 2),
            ("1. Томаты Бархатный мёд — 349 ₽\n2. Харчо — 199 ₽", 'ozon', 2),
            ("Томаты Бархатный мёд — 349.00\nХарчо = 199", 'auto', 2),
            ("1.   Томаты Бархатный мёд — 349.00\n2.   Харчо — 199.00", 'auto', 2),
        ]
        for text, source, expected in tests:
            items = parse_cheque(text, source=source)
            ok = '✅' if len(items) >= expected else '❌'
            print(f"{ok} {source}: found {len(items)} items (expected ≥{expected})")
            for item in items:
                print(f"    {item['name']} — {item['price']} ₽ × {item['qty']}")
    elif len(sys.argv) > 1 and sys.argv[1] == '--test-ofd':
        # Тест на реальных самокат-чеках
        import glob
        files = sorted(glob.glob('cheques_html_ofd/samokat_*.html'))
        for f in files:
            with open(f) as fh:
                html = fh.read()
            items = parse_cheque(html, source='samokat_ofd')
            meta = extract_metadata(html)
            print(f"{f.split('/')[-1]}: {len(items)} items, total {sum(i['total'] for i in items):.0f} ₽ (file: {f.split('_')[-1].replace('.html','')} ₽)")
    else:
        print("Использование:")
        print("  python3 cheque_parser.py --file <path> [source]")
        print("  python3 cheque_parser.py --test")
        print("  python3 cheque_parser.py --test-ofd")
