#!/usr/bin/env python3
"""Мониторинг расходов через SMS Сбербанка (Phone Link).

Парсит SMS вида:
- 'Списание 1679.98р Пятерочка Баланс: 13832.56р'
- 'Покупка 3985р TAPCHAN Баланс: 20176.47р'
- 'Оплата 300р ГКУ АМПП Баланс: 48532.56р'

Вносит в purchases (дедупликация по date+amount+store).
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')
from purchase_dedup import build_time_note, canonical_store_name, extract_event_time, is_duplicate_purchase

# Путь к базе Phone Link
PHONE_LINK_DB_GLOB = (
    "/mnt/c/Users/*/AppData/Local/Packages/"
    "Microsoft.YourPhone_8wekyb3d8bbwe/LocalCache/Indexed/*/System/Database/phone.db"
)

# Паттерны для расходных SMS (Сбербанк, ВТБ, Альфа, Т-Банк, Совкомбанк)
EXPENSE_PATTERNS = {
    'sberbank': [
        # Формат: "Счёт карты MIR-XXXX HH:MM Покупка 399р TUTORPLACE Баланс: 27124.11р"
        r'(?:Счёт карты|СЧЁТ)\s+\S+\s+\d{2}:\d{2}\s+(?:Покупка|Оплата|Списание|Покупка по СБП|Оплата по СБП)\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Баланс:',
        # Формат: "Счёт карты MIR-XXXX HH:MM Покупка по СБП 350р slx Баланс:"
        r'(?:Счёт карты|СЧЁТ)\s+\S+\s+\d{2}:\d{2}\s+(?:Покупка|Оплата|Списание)\s+по\s+СБП\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Баланс:',
    ],
    'vtb': [
        # ВТБ: "Списание с карты *1234 1000р МАГАЗИН Доступно: 5000р"
        r'Списание\s+с\s+карты\s+\*?\d{4}\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Доступно:',
        # ВТБ: "Покупка 500р МАГАЗИН Остаток: 10000р"
        r'(?:Покупка|Оплата)\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Остаток:',
    ],
    'alfa': [
        # Альфа: "Списание 1000р МАГАЗИН Баланс: 5000р"
        r'Списание\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Баланс:',
        # Альфа: "Оплата 500р УСЛУГА Доступно: 10000р"
        r'Оплата\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Доступно:',
    ],
    'tinkoff': [
        # Т-Банк: "Покупка 1000р МАГАЗИН Доступно: 5000р"
        r'Покупка\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Доступно:',
        # Т-Банк: "Списание 500р УСЛУГА Баланс: 10000р"
        r'Списание\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Баланс:',
    ],
    'sovcombank': [
        # Совкомбанк: "Оплата 1000р МАГАЗИН Остаток: 5000р"
        r'Оплата\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Остаток:',
        # Совкомбанк: "Списание 500р УСЛУГА Доступно: 10000р"
        r'Списание\s+([\d\s]+[,.]?\d*)\s*(?:р|руб|₽)?\s+(.+?)\s+Доступно:',
    ],
}

# Маппинг магазинов
STORE_MAP = {
    'Пятерочка': 'Пятёрочка',
    'SAMOKAT': 'Самокат',
    'YANDEX DRIVE': 'Яндекс Драйв',
    'TAPCHAN': 'Tapchan',
    'TUTORPLACE': 'TutorPlace',
    'SBERTIPS': 'СберЧаевые',
    'SBERCHAEVYE': 'СберЧаевые',
    'МегаФон': 'МегаФон',
    'WINELAB': 'WineLab',
    'ГКУ АМПП': 'АМПП (парковка)',
    'MAPP_SBERBANK': 'СберБанк Онлайн',
    'YM OZON': 'Ozon',
    'YM GOSUSLUGI': 'Госуслуги',
    'SLX': 'SLX',
    'GOS': 'GOS',
    'SBSCR Телеграм': 'Telegram',
}


def find_phone_link_dbs() -> List[Path]:
    """Находит все базы Phone Link (для двух телефонов)."""
    import glob
    paths = glob.glob(PHONE_LINK_DB_GLOB)
    if not paths:
        return []
    # Сортируем по свежести
    return sorted((Path(p) for p in paths), key=lambda p: p.stat().st_mtime, reverse=True)


def copy_db_to_temp(db_path: Path) -> Path:
    """Копирует базу во временный файл (с WAL/SHM)."""
    temp_dir = tempfile.mkdtemp(prefix='phone_link_')
    temp_db = Path(temp_dir) / 'phone.db'
    shutil.copy2(db_path, temp_db)
    # Копируем WAL и SHM если есть
    for ext in ['-wal', '-shm']:
        src = db_path.parent / (db_path.name + ext)
        if src.exists():
            shutil.copy2(src, temp_db.parent / (temp_db.name + ext))
    return temp_db


def windows_ticks_to_datetime(value: int) -> datetime:
    """Конвертирует Windows FILETIME в datetime."""
    unix_seconds = (int(value) - 116444736000000000) / 10_000_000
    return datetime.fromtimestamp(unix_seconds)


def parse_sms_body(body: str, sender: str = '') -> Optional[Dict]:
    """Парсит тело SMS на предмет расхода.
    
    Определяет банк по отправителю и применяет соответствующие паттерны.
    """
    body = body.strip().replace('\xa0', ' ')  # неразрывный пробел → обычный
    
    # Определяем банк по отправителю
    bank = 'sberbank'  # по умолчанию
    sender_lower = sender.lower()
    if 'vtb' in sender_lower or 'втб' in body.lower():
        bank = 'vtb'
    elif 'alfa' in sender_lower or 'альфа' in body.lower():
        bank = 'alfa'
    elif 'tinkoff' in sender_lower or 't-bank' in sender_lower or 'тинькофф' in body.lower() or 'т-банк' in body.lower():
        bank = 'tinkoff'
    elif 'sovcom' in sender_lower or 'совком' in body.lower() or 'halva' in sender_lower:
        bank = 'sovcombank'
    
    # Пробуем паттерны для определённого банка, затем общие
    patterns = EXPENSE_PATTERNS.get(bank, [])
    if bank != 'sberbank':
        patterns = patterns + EXPENSE_PATTERNS.get('sberbank', [])
    
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                amount_str, store = groups
            elif len(groups) == 3:
                amount_str, store = groups[1], groups[2]
            else:
                continue
            
            # Парсим сумму
            amount_str = amount_str.replace(' ', '').replace(',', '.')
            try:
                amount = float(amount_str)
            except ValueError:
                continue
            
            # Определяем магазин
            store_upper = store.upper()
            for key, mapped in STORE_MAP.items():
                if key.upper() in store_upper:
                    store = mapped
                    break
            
            return {
                'amount': amount,
                'store': canonical_store_name(store.strip()),
                'raw': body,
                'bank': bank,
                'event_time': extract_event_time(body),
            }
    
    return None


def scan_sms_expenses(days_back: int = 7) -> List[Dict]:
    """Сканирует SMS за последние N дней на предмет расходов."""
    db_paths = find_phone_link_dbs()
    if not db_paths:
        print("❌ База Phone Link не найдена")
        return []
    
    expenses = []
    seen_bodies = set()  # дедупликация по телу SMS
    
    for db_path in db_paths:
        temp_db = copy_db_to_temp(db_path)
        
        try:
            conn = sqlite3.connect(str(temp_db))
            conn.row_factory = sqlite3.Row
            
            # Ищем SMS от Сбербанка (отправитель 900 или Sberbank)
            since_ticks = int((datetime.now() - timedelta(days=days_back)).timestamp() * 10_000_000 + 116444736000000000)
            
            rows = conn.execute("""
                SELECT m.body, m.timestamp, m.from_address as sender
                FROM message m
                WHERE (m.from_address IN ('900', 'VTB', 'Альфа-Банк', 'Tinkoff', 'T-Bank', 'Совкомбанк', 'Халва')
                       OR m.from_address LIKE '%sber%'
                       OR m.from_address LIKE '%сбер%'
                       OR m.from_address LIKE '%vtb%'
                       OR m.from_address LIKE '%альфа%'
                       OR m.from_address LIKE '%tinkoff%'
                       OR m.from_address LIKE '%t-bank%'
                       OR m.from_address LIKE '%совком%'
                       OR m.from_address LIKE '%halva%'
                       OR m.body LIKE '%Списание%'
                       OR m.body LIKE '%Покупка%'
                       OR m.body LIKE '%Оплата%'
                       OR m.body LIKE '%Списано%')
                  AND m.timestamp > ?
                  AND m.body IS NOT NULL
                ORDER BY m.timestamp DESC
            """, (since_ticks,)).fetchall()
            
            for row in rows:
                body = row['body']
                if body in seen_bodies:
                    continue
                seen_bodies.add(body)
                
                parsed = parse_sms_body(body, row['sender'])
                if parsed:
                    # Фильтруем переводы и крупные операции
                    if 'перевод' in body.lower() and parsed['amount'] > 5000:
                        continue
                    # Чаевые — это расходы
                    if 'SBERTIPS' in body or 'SBERCHAEVYE' in body:
                        parsed['store'] = 'СберЧаевые'
                    parsed['date'] = windows_ticks_to_datetime(row['timestamp']).date()
                    parsed['sender'] = row['sender']
                    expenses.append(parsed)
            
            conn.close()
        finally:
            # Чистим temp
            shutil.rmtree(temp_db.parent, ignore_errors=True)
    
    return expenses


def import_expenses(expenses: List[Dict]) -> tuple:
    """Вносит расходы в БД."""
    conn = sqlite3.connect(DB_PATH)
    try:
        imported = 0
        skipped = 0
        
        for exp in expenses:
            # Дедупликация
            if is_duplicate_purchase(
                conn,
                exp['date'].isoformat(),
                exp['amount'],
                exp['store'],
                event_time=exp.get('event_time'),
            ):
                skipped += 1
                continue
            
            note_suffix = build_time_note(exp.get('event_time'))
            note = f"SMS: {exp['raw'][:100]}"
            if note_suffix:
                note += f" ({note_suffix})"
            conn.execute('''
                INSERT INTO purchases (purchase_date, store_name, total_amount, notes, source, data_origin)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                exp['date'].isoformat(),
                exp['store'],
                exp['amount'],
                note,
                'sms_sber',
                'sms_sber'
            ))
            imported += 1
        
        conn.commit()
        return imported, skipped
    finally:
        conn.close()


if __name__ == '__main__':
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    
    print(f"📱 Сканирование SMS-расходов за {days} дней...")
    expenses = scan_sms_expenses(days)
    print(f"Найдено SMS-расходов: {len(expenses)}")
    
    for exp in expenses[:10]:
        print(f"  {exp['date']} | {exp['store']:25} | {exp['amount']:>10.2f} ₽")
    
    imported, skipped = import_expenses(expenses)
    print(f"\nИмпортировано: {imported}, пропущено (дубли): {skipped}")
