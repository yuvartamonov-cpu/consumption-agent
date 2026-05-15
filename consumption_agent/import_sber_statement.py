#!/usr/bin/env python3
"""Импорт выписки Сбербанка (PDF/text) в consumption.db.

Операции из выписки преобразуются в записи таблицы `purchases`.
Дедупликация: (purchase_date, total_amount, store_name).
"""
import sys
import os
import re
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')

# Маппинг категорий Сбербанка → наши категории
CATEGORY_MAP = {
    'Супермаркеты': 'cat_food',
    'Рестораны и кафе': 'cat_food',
    'Коммунальные платежи, связь, интернет': 'cat_home',
    'Образование': 'cat_other',
    'Прочие расходы': 'cat_other',
    'Прочие операции': 'cat_other',
    'Перевод с карты': None,  # переводы — не расход
    'Перевод на карту': None,
    'Перевод СБП': None,
    'Внесение наличных': None,
    'Оплата по QR–коду СБП': 'cat_other',
    'Оплата по QR-коду СБП': 'cat_other',
}

# Маппинг магазинов по ключевым словам
STORE_MAP = {
    'SAMOKAT': 'Самокат',
    'SBER*5411*SAMOKAT': 'Самокат',
    'YANDEX*7512*DRIVE': 'Яндекс Драйв',
    'YANDEX*DRIVE': 'Яндекс Драйв',
    'TAPCHAN': 'Tapchan',
    'TUTORPLACE': 'TutorPlace',
    'SBERTIPS': 'СберЧаевые',
    'SBERCHAEVYE': 'СберЧаевые',
    'Пятерочка': 'Пятёрочка',
    'WINELAB': 'WineLab',
    'МегаФон': 'МегаФон',
    'YM*ozon': 'Ozon',
    'YM*GOSUSLUGI': 'Госуслуги',
    'YM*': 'Яндекс Маркет',
    'slx': 'SLX',
    'gos': 'GOS',
    'MAPP_SBERBANK_ONL@IN_PAY': 'СберБанк Онлайн',
    'SBSCR_Телеграм': 'Telegram',
    'ГКУ "АМПП"': 'АМПП (парковка)',
    'АО "T-Банк"': 'Т-Банк',
    'Альфа-банк': 'Альфа-Банк',
    'ВТБ': 'ВТБ',
}


def parse_amount(s):
    """Парсит сумму вида '1 679,98' → Decimal."""
    if not s:
        return None
    s = s.replace(' ', '').replace(',', '.')
    try:
        return Decimal(s).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def detect_store(description):
    """Определяет магазин по описанию операции."""
    desc_upper = description.upper()
    for key, store in STORE_MAP.items():
        if key.upper() in desc_upper:
            return store
    # Fallback: берём первое слово до точки или запятой
    clean = description.split('.')[0].split(',')[0].strip()
    if len(clean) > 3:
        return clean
    return 'Прочее'


def detect_category(sber_category, store_name):
    """Определяет категорию consumption."""
    if sber_category in CATEGORY_MAP:
        cat = CATEGORY_MAP[sber_category]
        if cat:
            return cat
    # Fallback по магазину
    store_cats = {
        'Самокат': 'cat_food',
        'Пятёрочка': 'cat_food',
        'Tapchan': 'cat_food',
        'WineLab': 'cat_food',
        'Яндекс Драйв': 'cat_auto',
        'Т-Банк': 'cat_other',
        'Альфа-Банк': 'cat_other',
        'ВТБ': 'cat_other',
        'МегаФон': 'cat_home',
        'Telegram': 'cat_subscriptions',
        'TutorPlace': 'cat_other',
        'Госуслуги': 'cat_other',
        'АМПП (парковка)': 'cat_auto',
    }
    return store_cats.get(store_name, 'cat_other')


def parse_statement_text(text):
    """Парсит текст выписки Сбербанка.
    
    Формат строки:
    ДАТА ОПЕРАЦИИ (МСК)  Дата обработки и код  КАТЕГОРИЯ  Описание  СУММА  ОСТАТОК
    """
    operations = []
    lines = text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Ищем дату в формате DD.MM.YYYY
        date_match = re.match(r'(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})', line)
        if not date_match:
            continue
        
        op_date = datetime.strptime(date_match.group(1), '%d.%m.%Y').date()
        op_time = date_match.group(2)
        
        # Ищем сумму — число с запятой/точкой, возможно с пробелами
        # Сумма может быть положительной (внесение) или отрицательной (списание)
        amount_match = re.search(r'([+-]?[\d\s]+[,.]\d{2})\s+([\d\s]+[,.]\d{2})\s*$', line)
        if not amount_match:
            continue
        
        amount_str = amount_match.group(1).replace(' ', '')
        amount = parse_amount(amount_str)
        
        # Пропускаем переводы и внесения (положительные суммы без минуса, но в выписке списания — отрицательные)
        # В выписке Сбербанка: списания без знака, пополнения с +
        is_income = amount_str.startswith('+')
        
        # Извлекаем описание — всё между категорией и суммой
        # Упрощённый парсинг: берём текст после времени и до суммы
        rest = line[date_match.end():].strip()
        
        # Убираем дату обработки (следующие цифры)
        rest = re.sub(r'^\d{2}\.\d{2}\.\d{4}\s+\d{6}\s+', '', rest)
        
        # Ищем категорию — известные паттерны
        category = 'Прочие расходы'
        for cat in CATEGORY_MAP.keys():
            if cat in rest:
                category = cat
                rest = rest.replace(cat, '', 1).strip()
                break
        
        # Описание — оставшийся текст до суммы
        desc = rest
        for pattern in [r'[\d\s]+[,.]\d{2}\s+[\d\s]+[,.]\d{2}\s*$']:
            desc = re.sub(pattern, '', desc).strip()
        
        store = detect_store(desc)
        our_category = detect_category(category, store)
        
        operations.append({
            'date': op_date,
            'time': op_time,
            'category': category,
            'store': store,
            'description': desc,
            'amount': amount,
            'is_income': is_income,
            'our_category': our_category,
        })
    
    return operations


def import_to_db(operations):
    """Вносит операции в БД consumption.db.
    
    Расходы → purchases, переводы → transfers.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        imported = 0
        imported_transfers = 0
        skipped = 0
        
        for op in operations:
            if op['amount'] is None:
                skipped += 1
                continue
            
            # Переводы и крупные операции → отдельная таблица
            if ('Перевод' in op['category'] or 'Перевод' in op['description'] or
                'Прочие операции' in op['category'] and op['amount'] > 10000):
                # Дедупликация переводов
                existing = conn.execute('''
                    SELECT id FROM transfers 
                    WHERE transfer_date = ? AND amount = ? AND description = ?
                ''', (op['date'].isoformat(), float(op['amount']), op['description'])).fetchone()
                
                if existing:
                    skipped += 1
                    continue
                
                conn.execute('''
                    INSERT INTO transfers (transfer_date, amount, description, source)
                    VALUES (?, ?, ?, ?)
                ''', (
                    op['date'].isoformat(),
                    float(op['amount']),
                    f"{op['description']} | {op['category']}",
                    'sber_statement'
                ))
                imported_transfers += 1
                continue
            
            # Пополнения/внесения пропускаем
            if op['is_income']:
                skipped += 1
                continue
            
            # Расходы → purchases
            existing = conn.execute('''
                SELECT id FROM purchases 
                WHERE purchase_date = ? AND total_amount = ? AND store_name = ?
            ''', (op['date'].isoformat(), float(op['amount']), op['store'])).fetchone()
            
            if existing:
                skipped += 1
                continue
            
            conn.execute('''
                INSERT INTO purchases (purchase_date, store_name, total_amount, notes, source, data_origin)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                op['date'].isoformat(),
                op['store'],
                float(op['amount']),
                f"{op['description']} | {op['category']}",
                'sber_statement',
                'sber_statement'
            ))
            imported += 1
        
        conn.commit()
        return imported, imported_transfers, skipped
    finally:
        conn.close()


if __name__ == '__main__':
    # Читаем текст из stdin или файла
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    
    operations = parse_statement_text(text)
    print(f"Найдено операций: {len(operations)}")
    
    # Фильтруем только расходы
    expenses = [op for op in operations if not op['is_income'] and op['amount'] and op['amount'] > 0]
    print(f"Расходов: {len(expenses)}")
    
    for op in expenses[:10]:
        print(f"  {op['date']} {op['time']} | {op['store']:25} | {op['amount']:>10} ₽ | {op['category']}")
    
    imported, imported_transfers, skipped = import_to_db(expenses)
    print(f"\nРасходов импортировано: {imported}")
    print(f"Переводов импортировано: {imported_transfers}")
    print(f"Пропущено (дубли/пополнения): {skipped}")
