#!/usr/bin/env python3
"""Отправка отчёта спринта 13–17 мая + bot_commands.md на email
через skill email-send (smtp.gmail.com SSL 465)."""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path('/home/yuri_artamonov/.openclaw/workspace/consumption_agent')
WORKSPACE = Path('/home/yuri_artamonov/.openclaw/workspace')
SKILL_DIR = WORKSPACE / 'skills' / 'email-send' / 'scripts'

DOCS_FILE = PROJECT_DIR / 'docs' / 'bot_commands.md'
BODY_FILE = PROJECT_DIR / 'docs' / 'sprint_report_body.txt'

# Импортируем функцию send_email из скила
sys.path.insert(0, str(SKILL_DIR))
import send_email_smtp  # type: ignore


def main():
    if not DOCS_FILE.exists():
        print(f'⚠️ не найден: {DOCS_FILE}')
        sys.exit(1)
    if not BODY_FILE.exists():
        print(f'⚠️ не найден: {BODY_FILE}')
        sys.exit(1)

    # Меняем cwd чтобы load_env подхватил .env
    os.chdir(WORKSPACE)

    body = BODY_FILE.read_text(encoding='utf-8')

    send_email_smtp.send_email(
        to='yu.v.artamonov@gmail.com',
        subject='[consumption_agent] Спринт 13–17 мая завершён — bot_commands.md',
        body=body,
        attachments=[DOCS_FILE],
    )
    print(f'✅ Письмо отправлено на yu.v.artamonov@gmail.com', flush=True)
    print(f'   Прикреплён: {DOCS_FILE} ({DOCS_FILE.stat().st_size} байт)', flush=True)


if __name__ == '__main__':
    main()
