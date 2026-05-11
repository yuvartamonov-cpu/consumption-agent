#!/usr/bin/env python3
"""Отправка roadmap на почту через Gmail SMTP"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

# Конфигурация
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
SENDER_EMAIL = "yu.v.artamonov@gmail.com"
# Пароль из .env
PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace('"', '')

# Получатели
RECIPIENTS = ["yu.v.artamonov@gmail.com"]

# Читаем roadmap
roadmap_path = os.path.join(os.path.dirname(__file__), "04_roadmap.md")
with open(roadmap_path, "r", encoding="utf-8") as f:
    roadmap_content = f.read()

# Создаём письмо
msg = MIMEMultipart()
msg["From"] = SENDER_EMAIL
msg["To"] = ", ".join(RECIPIENTS)
msg["Subject"] = f"📋 Consumption Agent Roadmap — {datetime.now().strftime('%d.%m.%Y')}"

# Тело письма
body = f"""Привет, CEO!

Актуальный roadmap consumption_agent во вложении.

Ключевые обновления:
• ✅ Фаза 1 — Гарантии и напоминания (выполнена)
• 🔄 Фаза 2 — Яндекс-экосистема (в процессе)
  - 2.1 Мониторинг HKID2021@yandex.ru
  - 2.2 Яндекс Еда / Лавка (3 чека импортированы)
  - 2.3 Яндекс Драйв (8 скриншотов, 6 в БД)
  - 2.4 Массовый импорт (ожидает VPN)
• ⬜ Фаза 2.5 — Дедупликация данных (новая)
• ⬜ Фаза 2.6 — Госуслуги (7 писем импортировано)
• ⬜ Фаза 2.7 — Яндекс Драйв: полная история (новая)
• ⬜ Фазы 3–8 — в планах

Git: master @ 9f5c83c (синхронизировано с Paperclip AI)

---
Отправлено автоматически consumption_agent.
"""

msg.attach(MIMEText(body, "plain", "utf-8"))

# Вложение
attachment = MIMEBase("application", "octet-stream")
attachment.set_payload(roadmap_content.encode("utf-8"))
encoders.encode_base64(attachment)
attachment.add_header(
    "Content-Disposition",
    f"attachment; filename=04_roadmap_{datetime.now().strftime('%Y%m%d')}.md"
)
msg.attach(attachment)

# Отправка
try:
    server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=20)
    server.login(SENDER_EMAIL, PASSWORD)
    server.send_message(msg)
    server.quit()
    print("✅ Roadmap отправлен на yu.v.artamonov@gmail.com")
except Exception as e:
    print(f"❌ Ошибка отправки: {e}")
