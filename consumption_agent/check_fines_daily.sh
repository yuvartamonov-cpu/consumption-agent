#!/bin/bash
# Ежедневная проверка штрафов на всех 4 почтах + SMS
# Запускается в 18:00 по cron
# Отправляет сводный отчёт в Telegram (обязательно)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

source venv/bin/activate 2>/dev/null
set -a
source .env 2>/dev/null || true
set +a

LOG="${SCRIPT_DIR}/logs/fines_daily.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Ежедневная проверка штрафов" >> "$LOG"

# Проверка всех почт (4 шт) + SMS + обязательный отчёт
timeout 120 python3 scripts/fines_bot.py --days 7 --summary --check-sms >> "$LOG" 2>&1
RC=$?

echo "$(date '+%Y-%m-%d %H:%M:%S') — Завершено (код: $RC)" >> "$LOG"

# Покажем последние строки
tail -3 "$LOG"
exit $RC
