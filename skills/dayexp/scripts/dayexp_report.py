#!/usr/bin/env python3
"""Скрипт для команды /dayexp — расходы за сегодня.
Запускает daily_cheque_scan.py (сканирование почт + SMS) и выводит отчёт за текущий день.

Использование:
  python3 dayexp_report.py
"""
import subprocess, sys, os, sqlite3, json
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.join(SCRIPT_DIR, '..', '..', '..', 'consumption_agent')
DB_PATH = os.path.join(AGENT_DIR, 'consumption.db')
SCAN_SCRIPT = os.path.join(AGENT_DIR, 'daily_cheque_scan.py')

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


def get_today_purchases():
    """Выбрать покупки за сегодня из БД."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date = date('now')
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY total_amount DESC
        """).fetchall()
        return rows
    finally:
        conn.close()


def format_report(rows, today_str):
    """Сформировать текстовый отчёт."""
    if not rows:
        return f'📭 За сегодня ({today_str}) покупок не найдено.'

    total = sum(r[1] or 0 for r in rows)
    lines = [f'📊 *Расходы за {today_str}*']
    lines.append(f'_{len(rows)} покупок, всего {total:,.0f} ₽_\n'.replace(',', ' '))

    for r in rows:
        _, amount, store, source, notes = r
        amt = amount or 0
        icon = SOURCE_ICONS.get(source or '', '📧')
        store = store or '—'
        note = (notes or '').replace('\n', ' ').strip()[:80]
        lines.append(f'{icon} *{store}* — {amt:,.0f} ₽'.replace(',', ' '))
        if note:
            lines.append(f'   {note}')

    # По магазинам
    by_store = {}
    for r in rows:
        s = r[2] or 'Другое'
        by_store[s] = by_store.get(s, 0) + (r[1] or 0)
    if len(by_store) > 1:
        lines.append(f'\n📌 *По магазинам:*')
        for s, a in sorted(by_store.items(), key=lambda x: -x[1]):
            lines.append(f'  • {s}: {a:,.0f} ₽'.replace(',', ' '))

    return '\n'.join(lines)


def main():
    today_str = datetime.now().strftime('%d.%m.%Y')

    # Шаг 1: сканирование почт и SMS
    print(f'🔍 Сканирую почты и SMS за сегодня ({today_str})...')
    log = run_scan()
    print(f'[log] {log[:300]}')

    # Шаг 2: формирование отчёта
    rows = get_today_purchases()
    report = format_report(rows, today_str)
    print('\n' + report)


if __name__ == '__main__':
    main()
