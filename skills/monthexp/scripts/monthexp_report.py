#!/usr/bin/env python3
"""Скрипт для команды /monthexp — расходы с 1 числа текущего месяца.
Запускает daily_cheque_scan.py (сканирование почт + SMS за сегодня) 
и выводит отчёт с группировкой по дням.

Использование:
  python3 monthexp_report.py
"""
import subprocess, sys, os, sqlite3, json
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.join(SCRIPT_DIR, '..', '..', '..', 'consumption_agent')
DB_PATH = os.path.join(AGENT_DIR, 'consumption.db')
SCAN_SCRIPT = os.path.join(AGENT_DIR, 'daily_cheque_scan.py')

MONTH_NAMES = {
    1:'Январь', 2:'Февраль', 3:'Март', 4:'Апрель',
    5:'Май', 6:'Июнь', 7:'Июль', 8:'Август',
    9:'Сентябрь', 10:'Октябрь', 11:'Ноябрь', 12:'Декабрь'
}

SOURCE_ICONS = {
    'gmail': '📧', 'yandex': '📧', 'yandex_food': '🍽',
    'sms': '📱', 'local': '📝', 'manual': '✏️'
}


def run_scan():
    """Запустить daily_cheque_scan.py и дождаться завершения."""
    result = subprocess.run(
        [sys.executable, SCAN_SCRIPT],
        capture_output=True, text=True, timeout=120,
        cwd=AGENT_DIR
    )
    return result.stdout + result.stderr


def get_month_purchases(month_start, today_str):
    """Выбрать покупки с month_start по today_str из БД."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date >= ? AND purchase_date <= ?
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY purchase_date, total_amount DESC
        """, (month_start, today_str)).fetchall()
        return rows
    finally:
        conn.close()


def format_report(rows, month_start, today_str, month_name):
    """Сформировать текстовый отчёт с группировкой по дням."""
    if not rows:
        return f'📭 За {month_name} (с 1 по {today_str.split("-")[2]}) покупок не найдено.'

    grand_total = sum(r[1] or 0 for r in rows)
    lines = [f'📊 *Расходы с 1 {month_name.lower()} по {datetime.now().day} число*']
    lines.append(f'_{len(rows)} покупок, всего {grand_total:,.0f} ₽_\n'.replace(',', ' '))

    # Группировка по дням
    by_day = {}
    for r in rows:
        d = r[0]
        if d not in by_day:
            by_day[d] = []
        by_day[d].append(r)

    for day in sorted(by_day.keys(), reverse=True):
        day_rows = by_day[day]
        day_total = sum(r[1] or 0 for r in day_rows)
        day_label = 'Сегодня' if day == today_str else day
        lines.append(f'\n📅 *{day_label}* — {day_total:,.0f} ₽ ({len(day_rows)} покупок)'.replace(',', ' '))

        for r in day_rows:
            _, amount, store, source, notes = r
            amt = amount or 0
            icon = SOURCE_ICONS.get(source or '', '📧')
            store = store or '—'
            note = (notes or '').replace('\n', ' ').strip()[:60]
            if note:
                lines.append(f'{icon} *{store}* — {amt:,.0f} ₽ · {note}'.replace(',', ' '))
            else:
                lines.append(f'{icon} *{store}* — {amt:,.0f} ₽'.replace(',', ' '))

    # По магазинам
    by_store = {}
    for r in rows:
        s = r[2] or 'Другое'
        by_store[s] = by_store.get(s, 0) + (r[1] or 0)
    if len(by_store) > 1:
        lines.append(f'\n📌 *Всего по магазинам:*')
        for s, a in sorted(by_store.items(), key=lambda x: -x[1]):
            lines.append(f'  • {s}: {a:,.0f} ₽'.replace(',', ' '))

    return '\n'.join(lines)


def main():
    today = datetime.now()
    month_start = today.strftime('%Y-%m-01')
    today_str = today.strftime('%Y-%m-%d')
    month_name = f'{MONTH_NAMES.get(today.month, "")} {today.year}'

    # Шаг 1: сканирование почт и SMS
    print(f'🔍 Сканирую почты и SMS — собираю данные за текущий месяц ({month_name})...')
    log = run_scan()
    print(f'[log] {log[:300]}')

    # Шаг 2: формирование отчёта за месяц
    rows = get_month_purchases(month_start, today_str)
    report = format_report(rows, month_start, today_str, month_name)
    print('\n' + report)


if __name__ == '__main__':
    main()
