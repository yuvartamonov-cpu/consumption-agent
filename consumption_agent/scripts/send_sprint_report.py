#!/usr/bin/env python3
"""Отправка отчёта спринта 13–17 мая + bot_commands.md на email."""
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

PROJECT_DIR = '/home/yuri_artamonov/.openclaw/workspace/consumption_agent'
DOCS_FILE = os.path.join(PROJECT_DIR, 'docs', 'bot_commands.md')


def read_env_password(env_file: str) -> str:
    with open(env_file) as f:
        for line in f:
            m = re.match(r'GMAIL_APP_PASSWORD=(.*)', line.strip())
            if m:
                return m.group(1).strip().strip('"').strip("'").replace(' ', '')
    raise RuntimeError('GMAIL_APP_PASSWORD не найден в .env')


def main():
    password = read_env_password(os.path.join(PROJECT_DIR, '.env'))
    smtp_user = 'yu.v.artamonov@gmail.com'
    to_email = 'yu.v.artamonov@gmail.com'

    body = """Привет!

Завершён 5-дневный спринт consumption_agent (13–17 мая 2026).
Все 5 дней выполнены, +92 теста, итого 513 проходят.

Прилагаю подробные инструкции по всем командам @ConsumptionAgentBot.

═══════════════════════════════════════════════════
ИТОГИ СПРИНТА
═══════════════════════════════════════════════════

День 1 — IMAP observability (commit 11ccc27)
  • ScanMetrics в imap_folders.py
  • Унифицированные логи в daily_cheque_scan, credit_monitor, fines_bot
  • +23 mock-теста (INBOX + Spam + Receipts покрыты)

День 2 — Official/distributor resolver (commit f86175c)
  • ml_official_sites.py: 25+ брендов
  • Tier ordering: official > distributor > authorized > brand_page
  • +18 тестов

День 3 — Translation + geolocation (commit edf5119)
  • QUERY_TRANSLATIONS: 70 → 200+ слов
  • Стемминг русских прилагательных
  • GEO_FOREIGN_SOURCES — гео-фильтр маркетплейсов
  • +21 теста (включая обратную совместимость)

День 4 — Telegram pagination (commit a2bfa84)
  • format_search_pages() — разбивка по 5/4096
  • Кнопка «Продолжить вывод (N ещё)»
  • Сквозная нумерация
  • +11 тестов

День 5 — Price-drop watchlist (commit ae3c0c2)
  • ml_watchlist.py + 2 таблицы
  • Кнопка «🔔 Следить за ценой (топ-3)»
  • Команды /ml_watch, /ml_unwatch
  • Cron 10:00: проверка цен через WB card v2 API
  • Telegram-уведомление при падении ≥10%
  • +20 тестов

═══════════════════════════════════════════════════
СИНХРОНИЗАЦИЯ
═══════════════════════════════════════════════════

✓ WSL workspace: a2bfa84 → ae3c0c2
✓ Bare-репо Windows (CLaudeCodeConsumption): a2bfa84 → ae3c0c2
✓ GitHub (yuvartamonov-cpu/consumption-agent): a2bfa84 → ae3c0c2

В приложении — полный гайд по всем командам бота:
• 12 разделов (базовые, инвентарь, расходы, долги, гарантии,
  каршеринг, Memory Lane, поиск, watchlist, темы, фото, система)
• Сводка callback-кнопок
• Cron-расписание и логи

Удачи!
— Claude (CEO)
"""

    msg = MIMEMultipart()
    msg['From'] = smtp_user
    msg['To'] = to_email
    msg['Subject'] = '[consumption_agent] Спринт 13–17 мая завершён — bot_commands.md'
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # Attach bot_commands.md
    if not os.path.exists(DOCS_FILE):
        print(f'⚠️ файл не найден: {DOCS_FILE}')
        sys.exit(1)

    with open(DOCS_FILE, 'rb') as attachment:
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment.read())
    encoders.encode_base64(part)
    part.add_header(
        'Content-Disposition',
        f'attachment; filename="bot_commands.md"',
    )
    msg.attach(part)

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(smtp_user, password)
            server.sendmail(smtp_user, to_email, msg.as_string())
        print(f'✅ Письмо отправлено на {to_email}')
        print(f'   Прикреплён: {DOCS_FILE} ({os.path.getsize(DOCS_FILE)} байт)')
    except Exception as e:
        print(f'❌ Ошибка отправки: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
