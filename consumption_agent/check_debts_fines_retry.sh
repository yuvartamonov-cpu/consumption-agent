#!/bin/bash
# Consumption Agent — проверка кредитов и штрафов с retry до успеха.
# Запускается по cron каждый час (10-23), но реально выполняется
# только если сегодня ещё не было успешного прогона.
# Флаг успеха: /tmp/debts_fines_done_YYYY-MM-DD

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

FLAG_FILE="/tmp/debts_fines_done_$(date '+%Y-%m-%d')"

# Если сегодня уже был успешный прогон — выходим
if [ -f "$FLAG_FILE" ]; then
    exit 0
fi

source venv/bin/activate
set -a
source .env 2>/dev/null || true
set +a

LOG=/tmp/debts_fines.log
NOW=$(date '+%Y-%m-%d %H:%M:%S')

echo "" >> "$LOG"
echo "$NOW — Запуск (ежечасный retry)" >> "$LOG"

# 1. Кредитные алерты
echo "--- Кредиты ---" >> "$LOG"
timeout 30 python3 credit_alerts.py >> "$LOG" 2>&1
CREDIT_OK=$?

# 2. Штрафы
echo "--- Штрафы ---" >> "$LOG"
timeout 30 python3 scripts/fines_bot.py --days 14 --notify >> "$LOG" 2>&1
FINES_OK=$?

echo "Статусы: кредиты=$CREDIT_OK штрафы=$FINES_OK" >> "$LOG"

# Если всё успешно — ставим флаг на сегодня
if [ $CREDIT_OK -eq 0 ] || [ $FINES_OK -eq 0 ]; then
    touch "$FLAG_FILE"
    echo "$NOW — ✅ Успешно (флаг: $FLAG_FILE)" >> "$LOG"
    # отправляем результаты в Telegram как есть
    tail -3 "$LOG"
else
    echo "$NOW — ⚠️ Ошибка подключения, следующий запуск через час" >> "$LOG"
fi
