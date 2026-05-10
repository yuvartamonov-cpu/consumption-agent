#!/bin/bash
# Отправка волны 5 (7 писем, финальные контакты)
cd /home/yuri_artamonov/.openclaw/workspace/domains_sale

cp csv_data/wave5_remaining.csv csv_data/wave2_next_batch.csv

python3 scripts/send_batch_4domains.py send-all --yes

echo "$(date '+%Y-%m-%d %H:%M') — wave5 отправлена (финал, 7 писем)" >> logs/wave5_sent.log
