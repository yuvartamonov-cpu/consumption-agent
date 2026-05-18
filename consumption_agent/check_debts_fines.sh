#!/bin/bash
# Consumption Agent — проверка кредитов и штрафов
# Запускается из heartbeat OpenClaw.
# Каждый шаг с таймаутом — чтобы heartbeat не зависал, если почта недоступна.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

source venv/bin/activate
set -a
source .env 2>/dev/null || true
set +a

NOW=$(date '+%Y-%m-%d %H:%M:%S')
LOG=/tmp/debts_fines_heartbeat.log

echo "$NOW — Проверка кредитов и штрафов" >> "$LOG"

# 1. Кредитные алерты
echo "--- Кредиты ---" >> "$LOG"
timeout 30 python3 credit_alerts.py >> "$LOG" 2>&1 || echo "⚠️ кредиты: timeout/error ($?)" >> "$LOG"

# 2. Штрафы
echo "--- Штрафы ---" >> "$LOG"
timeout 30 python3 scripts/fines_bot.py --days 14 --notify >> "$LOG" 2>&1 || echo "⚠️ штрафы: timeout/error ($?)" >> "$LOG"

echo "$NOW — Завершено" >> "$LOG"

# Покажем последние строки (если что-то новое)
tail -3 "$LOG"
