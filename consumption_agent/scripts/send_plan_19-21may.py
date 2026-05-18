#!/usr/bin/env python3
"""Отправка обновлённого плана 19–21 мая на email через skill email-send."""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path('/home/yuri_artamonov/.openclaw/workspace/consumption_agent')
WORKSPACE = Path('/home/yuri_artamonov/.openclaw/workspace')
SKILL_DIR = WORKSPACE / 'skills' / 'email-send' / 'scripts'

WEEK_PLAN_FILE = WORKSPACE / 'skills' / 'coding_plan_18-23may26' / 'references' / 'week_plan.md'
BODY_FILE = PROJECT_DIR / 'docs' / 'plan_19-21may_body.txt'

sys.path.insert(0, str(SKILL_DIR))
import send_email_smtp  # type: ignore


def main():
    if not BODY_FILE.exists():
        print(f'⚠️ не найден: {BODY_FILE}')
        sys.exit(1)
    if not WEEK_PLAN_FILE.exists():
        print(f'⚠️ не найден: {WEEK_PLAN_FILE}')
        sys.exit(1)

    os.chdir(WORKSPACE)

    body = BODY_FILE.read_text(encoding='utf-8')

    send_email_smtp.send_email(
        to='yu.v.artamonov@gmail.com',
        subject='[consumption_agent] План 19–21 мая — Photo Pipeline, Memory Lane, Governance Seed',
        body=body,
        attachments=[WEEK_PLAN_FILE],
    )
    print(f'✅ Письмо отправлено на yu.v.artamonov@gmail.com', flush=True)
    print(f'   Прикреплён: {WEEK_PLAN_FILE} ({WEEK_PLAN_FILE.stat().st_size} байт)', flush=True)


if __name__ == '__main__':
    main()
