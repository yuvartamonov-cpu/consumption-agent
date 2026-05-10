#!/usr/bin/env python3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os

# Настройки SMTP
smtp_server = "smtp.gmail.com"
smtp_port = 587
smtp_user = "yu.v.artamonov@gmail.com"
smtp_password = "[REDACTED_OLD_GMAIL_APP_PASSWORD]"  # App password из TOOLS.md

# Получатель
to_email = "yu.v.artamonov@gmail.com"

# Тема и тело письма
subject = "Paperclip WSL2 Setup Instructions"
body = "Инструкции по установке Paperclip в отдельном WSL2 и настройке OpenClaw, Claude и Codex как агентов."

# Файл для отправки
file_path = "/home/yuri_artamonov/.openclaw/workspace/Paperclip_WSL2_Setup.txt"

# Создание письма
msg = MIMEMultipart()
msg['From'] = smtp_user
msg['To'] = to_email
msg['Subject'] = subject

msg.attach(MIMEText(body, 'plain'))

# Прикрепление файла
with open(file_path, "rb") as attachment:
    part = MIMEBase("application", "octet-stream")
    part.set_payload(attachment.read())

encoders.encode_base64(part)
part.add_header(
    "Content-Disposition",
    f"attachment; filename= {os.path.basename(file_path)}",
)

msg.attach(part)

# Отправка письма
try:
    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(smtp_user, smtp_password)
    server.sendmail(smtp_user, to_email, msg.as_string())
    server.quit()
    print("Письмо успешно отправлено!")
except Exception as e:
    print(f"Ошибка при отправке письма: {e}")