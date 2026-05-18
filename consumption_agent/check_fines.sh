#!/bin/bash
# Fines Bot — проверка штрафов из Госуслуг
# Запуск через cron (например, в 12:00 ежедневно)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Активируем venv
[ -d venv/bin ] && source venv/bin/activate

# Загружаем .env
[ -f .env ] && set -a && source .env && set +a

# Проверка за 7 дней + отправка в Telegram
python3 scripts/fines_bot.py --days 7 --notify
