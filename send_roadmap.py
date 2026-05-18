#!/usr/bin/env python3
"""Отправляет roadmap задач на почту и (если настроен) в Paperclip API."""

import smtplib
import json
import os
import subprocess
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Данные SMTP (Gmail — app password)
smtp_server = "smtp.gmail.com"
smtp_port = 587
smtp_user = "yu.v.artamonov@gmail.com"
# App password для Gmail (работает и для IMAP, и для SMTP)
smtp_password = "kzjj irsr hcsm ptoc"
to_email = "yu.v.artamonov@gmail.com"

# Читаем roadmap
with open(os.path.expanduser("~/.openclaw/workspace/roadmap.md")) as f:
    roadmap = f.read()

# ========== 1. EMAIL ==========
subject = "📋 Consumption Agent — Кодинг-задачи на завтра (12.05.2026)"
body = f"""Привет, Юрий!

Это автоматическая рассылка задач на кодинг-сессию для Paperclip CEO.
Roadmap сохранён в ~/.openclaw/workspace/roadmap.md

=== План на завтра ===

{roadmap}

=== Что сделано сегодня ===
✅ Исправлен whitelist бота (add_authorized_handler)
✅ Починены /items (date.replace → add_months_safe)
✅ Убран дубль /parse
✅ Добавлена esc_md для экранирования Markdown
✅ test_easyocr.py → easyocr_diag.py (не мешает pytest)
✅ Фикс yandex_orders_scraper.py (backslash-star warning)
✅ Создан openai-vision skill (SKILL.md + .skill)
✅ Закоммичено в git
"""

msg = MIMEMultipart()
msg['From'] = smtp_user
msg['To'] = to_email
msg['Subject'] = subject
msg.attach(MIMEText(body, 'plain', 'utf-8'))

try:
    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(smtp_user, smtp_password)
    server.sendmail(smtp_user, to_email, msg.as_string())
    server.quit()
    print("✅ Email отправлен на", to_email)
except Exception as e:
    print(f"❌ Email failed: {e}")

# ========== 2. PAPERCLIP API (push) ==========
# Если есть PAPERCLIP_API_KEY и PAPERCLIP_API_BASE — отправляем задачи
api_key = os.environ.get("PAPERCLIP_API_KEY") or os.environ.get("PAPERCLIP_AGENT_KEY")
api_base = os.environ.get("PAPERCLIP_API_BASE") or "http://localhost:18789"

if api_key:
    import urllib.request
    import urllib.error
    
    # Задачи из roadmap в формате Paperclip
    tasks = [
        {"title": "[P0] JobQueue: установить python-telegram-bot[job-queue]",
         "description": "pip install 'python-telegram-bot[job-queue]', проверить, что run_daily работает. Сейчас app.job_queue is None.",
         "priority": "critical"},
        {"title": "[P0] SQLite retry-логика при блокировках БД",
         "description": "Обернуть все записи в consumption.db в retry с exponential backoff.",
         "priority": "critical"},
        {"title": "[P0] Обновить куки Ozon",
         "description": "ozon_cookies.txt протухли, импорт чеков из Ozon не работает.",
         "priority": "critical"},
        {"title": "[P1] Напоминания о замене вещей (replace_after_months)",
         "description": "Алерты за 30 дней до replace_date, кнопка '✅ Заменено'.",
         "priority": "high"},
        {"title": "[P1] /find_car — рекомендация машины в Яндекс Драйв",
         "description": "Учитывать историю поездок (39 записей) + предпочтения FAW Bestune T77.",
         "priority": "high"},
        {"title": "[P1] Credit Monitor: поддержка Тинькофф Кредит",
         "description": "Распознавание писем от Тинькофф в credit_monitor.py.",
         "priority": "high"},
        {"title": "[P2] Импорт чеков WB / Megamarket",
         "description": "Проверить почему 0 писем, добавить sender pattern если нужно.",
         "priority": "medium"},
        {"title": "[P2] PDF-отчёт: warning по \\n в DejaVu",
         "description": "gen_report.py: заменить \\n на ручной перенос.",
         "priority": "medium"},
        {"title": "[P2] Daily report: объединить лотереи, ставки, кредиты",
         "description": "Расширить daily_report.py.",
         "priority": "medium"},
        {"title": "[P3] Price tracking",
         "description": "Мониторинг цен на товары с Ozon/WB.",
         "priority": "low"},
        {"title": "[P3] Budget planning",
         "description": "Бюджеты по категориям + алерты на превышение.",
         "priority": "low"},
    ]
    
    for task in tasks:
        try:
            req = urllib.request.Request(
                f"{api_base}/api/issues",
                data=json.dumps(task).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                method="POST"
            )
            response = urllib.request.urlopen(req, timeout=5)
            print(f"✅ Paperclip: {task['title']} — {response.status}")
        except urllib.error.HTTPError as e:
            print(f"⚠️  Paperclip HTTP {e.code}: {task['title']}")
        except Exception as e:
            print(f"⚠️  Paperclip: {e}")
else:
    print("ℹ️  PAPERCLIP_API_KEY не задан — API push пропущен")
    print("   Чтобы включить: export PAPERCLIP_API_KEY=...")
