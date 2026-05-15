#!/usr/bin/env python3
"""Скрипт для команды /dayexp [N] — расходы за N дней (включая сегодня).
Запускает daily_cheque_scan.py (сканирование почт + SMS) и выводит отчёт.

Использование:
  python3 dayexp_report.py        # только сегодня
  python3 dayexp_report.py -n 7   # последние 7 дней
"""
import subprocess, sys, os, sqlite3, argparse
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


def get_purchases(n_days=1):
    """Выбрать покупки за N дней (включая сегодня) из БД."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date >= date('now', ?)
              AND purchase_date <= date('now')
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY purchase_date DESC, total_amount DESC
        """, (f'-{n_days - 1} days',)).fetchall()
        return rows
    finally:
        conn.close()


def format_report(rows, today_str, n_days=1):
    """Сформировать текстовый отчёт."""
    if not rows:
        label = f'сегодня ({today_str})' if n_days == 1 else f'последние {n_days} дн. (по {today_str})'
        return f'📭 За {label} покупок не найдено.'

    total = sum(r[1] or 0 for r in rows)
    title = f'📊 *Расходы за сегодня ({today_str})*' if n_days == 1 else f'📊 *Расходы за последние {n_days} дн. (по {today_str})*'
    lines = [title]
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
    parser = argparse.ArgumentParser(description='Отчёт по расходам за N дней')
    parser.add_argument('-n', type=int, default=1, help='Количество дней (включая сегодня), по умолчанию 1')
    args = parser.parse_args()
    n_days = max(args.n, 1)

    today_str = datetime.now().strftime('%d.%m.%Y')
    day_label = f'последние {n_days} дн.' if n_days > 1 else 'сегодня'

    # Шаг 1: сканирование почт и SMS
    print(f'🔍 Сканирую почты и SMS за {day_label} ({today_str})...')
    log = run_scan()
    print(f'[log] {log[:300]}')

    # Шаг 2: формирование отчёта
    rows = get_purchases(n_days)
    report = format_report(rows, today_str, n_days)
    print('\n' + report)


if __name__ == '__main__':
    main()
