#!/bin/bash
# Consumption Agent — проверка кредитных платежей
# Запускается через cron в 10:00 и 18:00

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Активируем виртуальное окружение
source venv/bin/activate

# Загружаем переменные окружения
set -a
source .env 2>/dev/null || true
set +a

# Запускаем проверку
echo "$(date '+%Y-%m-%d %H:%M:%S') - Проверка кредитных платежей"
python3 credit_alerts.py

echo "$(date '+%Y-%m-%d %H:%M:%S') - Проверка завершена"
