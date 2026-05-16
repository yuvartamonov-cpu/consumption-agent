#!/usr/bin/env python3
"""
Сканирует все SMS из Windows Phone Link за последние 3 месяца,
пропускает через алгоритм фильтрации из cleanup_alerts.py
и показывает результат.

Запуск: python3 scan_sms_3mo.py
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

# Добавляем cleanup_alerts в путь
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from cleanup_alerts import classify_alert, Category, AD_SUBJECT_PATTERNS

# ─────────────────────────────────────────────────────────────
# Функции из sms_monitor.py (copied to avoid circular import)
# ─────────────────────────────────────────────────────────────

WINDOWS_PHONE_LINK_DB = (
    "/mnt/c/Users/Yuri Artamonov/AppData/Local/Packages/"
    "Microsoft.YourPhone_8wekyb3d8bbwe/LocalCache/Indexed/"
    "4b9bd4e5-8205-4f9d-9ab4-881fe8732128/System/Database/phone.db"
)

BANK_SMS_SENDERS = {
    '900': 'sberbank',
    'sberid': 'sberbank',
    'vtb': 'vtb',
    'alfa-bank': 'alfa',
    'alfabank': 'alfa',
    'tinkoff': 'tinkoff',
    't-bank': 'tinkoff',
    'sovcombank': 'sovcombank',
    'halva': 'sovcombank',
}

BODY_SENDER_PATTERNS = {
    'sberbank': ['сбер', 'sber'],  # 900 только в from_address
    'vtb': ['втб', 'vtb'],
    'alfa': ['альфа', 'alfa', 'alfa-bank'],
    'tinkoff': ['тинькофф', 'tinkoff', 't-bank', 'т-банк'],
    'sovcombank': ['совком', 'sovcom', 'халва', 'halva'],
    'joy_finance': ['joy finance', 'джой финанс'],
    'turbozaim': ['turbozaim', 'турбозайм'],
    'nebus': ['nebus', 'небус'],
    'boostra': ['boostra', 'бустра'],
    'ekvazaim': ['эквазайм', 'ekvazaim'],
    'webzaim': ['webzaim', 'вебзайм'],
    'dengi_srazu': ['деньги сразу', 'dengisrazu', 'dengi-srazu'],
}

# Отправители, которые умеют маскироваться под банки
SPOOF_SENDERS = {
    'iswis.ru', 'kapytal.ru', 'c-m0ney.ru', 'speedcrru', 'l0anpayru',
    'hotloan.ru', 'iamzaem.ru', 'my-cred.ru',
}

# Короткие номера банков (только from_address, не body)
SHORT_CODE_BANKS = {
    '900': 'sberbank',
}

NOT_CREDIT_SENDERS = {
    'unknown', 'yandex', 'google', 'amazon', 'ozon', 'wb', 'wildberries',
    'megamarket', 'rozetka', 'aliexpress',
}


def windows_ticks_to_datetime(value: int) -> Optional[datetime]:
    try:
        unix_seconds = (int(value) - 116444736000000000) / 10_000_000
        return datetime.fromtimestamp(unix_seconds)
    except Exception:
        return None


def detect_sender_name(from_address: str, body: str) -> str:
    sender_lower = (from_address or '').lower()
    
    # 1. Проверка отправителя на спам-МФО (заглушка)
    for spam in SPOOF_SENDERS:
        if spam in sender_lower:
            return 'spam_mfo'
    
    # 2. Проверка коротких номеров (только from_address)
    for code, bank_id in SHORT_CODE_BANKS.items():
        if sender_lower == code or sender_lower.startswith(code + ' ') or sender_lower.startswith(code + '-'):
            return bank_id
    for code, bank_id in SHORT_CODE_BANKS.items():
        if code in sender_lower.split():
            return bank_id
    if sender_lower == '900':
        return 'sberbank'
    
    # 3. Проверка по отправителю
    for key, bank_id in BANK_SMS_SENDERS.items():
        if key in sender_lower:
            # Отсекаем спам-МФО с похожими названиями
            from_from = sender_lower.replace(key, '').strip()
            if from_from and not any(c.isalpha() for c in from_from):
                return bank_id
    
    # 4. Проверка по body — только если отправитель не спамер
    is_spoof = any(spam in sender_lower for spam in ['iswis', 'kapytal', 'c-m0ney', 'speedcr', 'l0anpay', 'hotloan', 'iamzaem', 'my-cred', 'bistroz', 'fingis', 'techrub', 'banki.ru', 'mon-now', 'fast-tut', 'easycred', 'gostzaim', 'vivus', 'zaymer', 'webzaim', 'cred-rf', 'dk-pay', 'ekapusta', 'greenmoney'])
    if is_spoof:
        return 'spam_mfo'
    
    text = f"{from_address} {body}".lower()
    for bank_id, patterns in BODY_SENDER_PATTERNS.items():
        for pattern in patterns:
            if pattern in text:
                return bank_id
    return 'unknown'


def extract_sms_payment_date(body: str) -> Optional[str]:
    months = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
        'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
        'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
    }
    patterns = [
        r'до\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4})',
        r'до\s+(\d{1,2}[./]\d{1,2})',
        r'(\d{1,2}[./]\d{1,2}[./]\d{2,4})',
        r'до\s+(\d{1,2}\s+[а-я]+(?:\s+\d{4})?)',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if not m:
            continue
        s = m.group(1)
        try:
            if '.' in s or '/' in s:
                parts = re.split(r'[./]', s)
                day, month = int(parts[0]), int(parts[1])
                year = datetime.now().year if len(parts) < 3 or not parts[2] else int('20' + parts[2] if len(parts[2]) == 2 else parts[2])
                return datetime(year, month, day).strftime('%Y-%m-%d')
            parts = s.split()
            day = int(parts[0])
            month = months.get(parts[1].lower())
            year = int(parts[2]) if len(parts) > 2 else datetime.now().year
            if month:
                return datetime(year, month, day).strftime('%Y-%m-%d')
        except Exception:
            continue
    return None


def extract_sms_payment_amount(body: str) -> Optional[float]:
    patterns = [
        r'внести\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'плат[её]ж(?:\s+по\s+кредитке)?\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'к оплате\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'спишем\s+(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
        r'(\d{1,3}(?:[ \u00A0]?\d{3})*(?:[.,]\d{2})?)\s*(?:rur|rub|руб|₽|р)',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if not m:
            continue
        try:
            return float(m.group(1).replace(' ', '').replace('\u00A0', '').replace(',', '.'))
        except Exception:
            continue
    return None


def is_bank_related(from_address: str, body: str) -> bool:
    """Проверяет, относится ли SMS к банковской сфере (включая рекламу)."""
    text = f"{from_address} {body}".lower()
    sender_name = detect_sender_name(from_address, body)
    if sender_name != 'unknown':
        return True
    # Банковские ключевые слова
    bank_words = [
        'банк', 'кредит', 'займ', 'мфо', 'мкк', 'альфа', 'сбер', 'втб',
        'тинькофф', 'совком', 'почта банк', 'ренессанс', 'открытие',
        'росбанк', 'халва', 'карта', 'процент', 'ставк', 'платёж',
        'перевод', 'дебет', 'кредитк', 'денег', 'долг', 'задолж',
        'платеж', 'рассрочк', 'кешбэк', 'кэшбэк',
    ]
    return any(pat in text for pat in bank_words)


def count_messages():
    """Считает общее количество SMS в базе."""
    if not os.path.exists(WINDOWS_PHONE_LINK_DB):
        return 0
    tmp_dir = tempfile.mkdtemp(prefix='phone_link_')
    try:
        for suffix in ('', '-wal', '-shm'):
            src = WINDOWS_PHONE_LINK_DB + suffix
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp_dir, os.path.basename(src)))
        local_db = os.path.join(tmp_dir, os.path.basename(WINDOWS_PHONE_LINK_DB))
        conn = sqlite3.connect(local_db)
        count = conn.execute('SELECT COUNT(*) FROM message').fetchone()[0]
        conn.close()
        return count
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def scan_and_classify(days_back: int = 90) -> dict:
    """Сканирует SMS и классифицирует каждое."""
    if not os.path.exists(WINDOWS_PHONE_LINK_DB):
        print(f'❌ База Phone Link не найдена: {WINDOWS_PHONE_LINK_DB}')
        return {}

    tmp_dir = tempfile.mkdtemp(prefix='phone_link_')
    try:
        for suffix in ('', '-wal', '-shm'):
            src = WINDOWS_PHONE_LINK_DB + suffix
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp_dir, os.path.basename(src)))
        local_db = os.path.join(tmp_dir, os.path.basename(WINDOWS_PHONE_LINK_DB))
        conn = sqlite3.connect(local_db)
        conn.row_factory = sqlite3.Row

        cutoff = datetime.now() - timedelta(days=days_back)
        
        # Сначала посмотрим статистику по таблице
        total_msgs = conn.execute('SELECT COUNT(*) FROM message').fetchone()[0]
        print(f'Всего SMS в базе: {total_msgs}')
        
        # Проверим временной диапазон
        if total_msgs > 0:
            first = conn.execute('SELECT MIN(timestamp) as t FROM message').fetchone()['t']
            last = conn.execute('SELECT MAX(timestamp) as t FROM message').fetchone()['t']
            first_dt = windows_ticks_to_datetime(first)
            last_dt = windows_ticks_to_datetime(last)
            if first_dt:
                print(f'Диапазон: {first_dt.strftime("%Y-%m-%d")} — {last_dt.strftime("%Y-%m-%d")}')
            else:
                print(f'Диапазон (raw): {first} — {last}')

        # Получаем все SMS за период
        rows = conn.execute(
            'SELECT message_id, from_address, body, timestamp, type FROM message ORDER BY timestamp DESC'
        ).fetchall()
        
        conn.close()
    except Exception as e:
        print(f'❌ Ошибка при чтении БД: {e}')
        return {}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    cutoff_dt = datetime.now() - timedelta(days=days_back)
    
    # Статистика
    stats = {
        'total': 0,
        'in_period': 0,
        'bank_related': 0,
        Category.REAL_CREDIT: 0,
        Category.AD: 0,
        Category.RECEIPT: 0,
        Category.SUBSCRIPTION: 0,
        Category.GOV: 0,
        Category.ROADMAP: 0,
        Category.UNKNOWN: 0,
    }

    credit_sms = []
    ad_sms = []
    unknown_sms = []

    for row in rows:
        stats['total'] += 1
        dt = windows_ticks_to_datetime(row['timestamp'])
        if dt and dt < cutoff_dt:
            continue
        if dt is None:
            continue
        stats['in_period'] += 1

        from_address = row['from_address'] or ''
        body = row['body'] or ''
        if not body.strip():
            continue

        # Отсекаем не-банковские SMS (личные переписки, доставки и т.д.)
        if not is_bank_related(from_address, body):
            continue
        stats['bank_related'] += 1

        sender_name = detect_sender_name(from_address, body)
        payment_amount = extract_sms_payment_amount(body)

        cat = classify_alert(
            row['message_id'],
            'sms',
            sender_name,
            body[:200],
            body,
            payment_amount,
        )
        stats[cat] = stats.get(cat, 0) + 1

        item = {
            'id': row['message_id'],
            'from': from_address,
            'sender_name': sender_name,
            'body': body[:120],
            'amount': payment_amount,
            'date': dt.strftime('%Y-%m-%d %H:%M'),
            'category': cat,
        }

        if cat == Category.REAL_CREDIT:
            credit_sms.append(item)
        elif cat == Category.UNKNOWN:
            unknown_sms.append(item)
        else:
            ad_sms.append(item)

    return {
        'stats': stats,
        'credit': credit_sms,
        'ad': ad_sms,
        'unknown': unknown_sms,
        'cutoff': cutoff_dt.strftime('%Y-%m-%d'),
    }


def print_report(result: dict):
    if not result:
        return
    stats = result['stats']
    
    print(f'Отчёт за 3 месяца (с {result["cutoff"]})')
    print('=' * 60)
    print(f'Всего SMS в БД:        {stats["total"]}')
    print(f'За период:              {stats["in_period"]}')
    print(f'Банковские:             {stats["bank_related"]}')
    print(f'Исключено (не банк):    {stats["in_period"] - stats["bank_related"]}')
    print()
    print('Классификация банковских:')
    print(f'  ✅ Реальные кредиты:    {stats[Category.REAL_CREDIT]}')
    print(f'  📢 Реклама/чеки/мусор:  {stats[Category.AD]}')
    print(f'  ❓ Неопределено:        {stats[Category.UNKNOWN]}')
    print()
    
    if result['credit']:
        print('🟢 РЕАЛЬНЫЕ КРЕДИТЫ:')
        for s in result['credit']:
            print(f'  #{s["id"]} | {s["date"]} | {s["from"]:12s}→{s["sender_name"]:12s} | {s["body"][:80]} | {s["amount"]}')
    
    if result['unknown']:
        print()
        print('🟡 НЕОПРЕДЕЛЁННЫЕ (нужна ручная проверка):')
        for s in result['unknown']:
            print(f'  #{s["id"]} | {s["date"]} | {s["from"]:12s}→{s["sender_name"]:12s} | {s["body"][:80]} | {s["amount"]}')
    
    if result['ad']:
        print()
        print(f'🔴 Реклама/мусор ({len(result["ad"])} шт):')
        # Сгруппируем по отправителю
        by_sender = {}
        for s in result['ad']:
            key = f'{s["from"]}→{s["sender_name"]}'
            if key not in by_sender:
                by_sender[key] = []
            by_sender[key].append(s)
        for key, items in sorted(by_sender.items()):
            print(f'  📱 {key}: {len(items)} сообщений')
            # Покажем максимум 2 примера
            for s in items[:2]:
                print(f'      #{s["id"]} | {s["date"]} | {s["body"][:80]} | {s["amount"]}')
            if len(items) > 2:
                print(f'      ... и ещё {len(items) - 2}')


def main():
    # Проверяем, существует ли БД
    if not os.path.exists(WINDOWS_PHONE_LINK_DB):
        print(f'❌ Phone Link DB не найдена: {WINDOWS_PHONE_LINK_DB}')
        print('Проверьте что Phone Link установлен и синхронизирован.')
        sys.exit(1)
    
    print('Сканирую Phone Link БД...')
    result = scan_and_classify(days_back=90)
    
    if not result:
        sys.exit(1)
    
    print_report(result)

    # Сохраняем JSON
    out_path = os.path.join(SCRIPT_DIR, '..', 'reports', 'sms_3mo_report.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    
    # Преобразуем в сериализуемый вид
    serializable = {
        'generated_at': datetime.now().isoformat(),
        'cutoff': result['cutoff'],
        'stats': {k: v for k, v in result['stats'].items()},
        'credit_count': len(result['credit']),
        'ad_count': len(result['ad']),
        'unknown_count': len(result['unknown']),
        'credit': result['credit'],
        'unknown': result['unknown'],
    }
    with open(out_path, 'w') as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n📄 Отчёт сохранён: {out_path}')


if __name__ == '__main__':
    main()
