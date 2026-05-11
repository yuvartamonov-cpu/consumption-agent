#!/bin/bash
# Отправка волны 3 (25 писем, клиники + поставщики)
cd /home/yuri_artamonov/.openclaw/workspace/domains_sale

# Создаём временную копию next_batch из wave3
cp csv_data/wave3_batch.csv csv_data/wave2_next_batch.csv

# Отправляем
python3 scripts/send_batch_4domains.py send-all --yes

# Помечаем в логе
echo "$(date '+%Y-%m-%d %H:%M') — wave3 отправлена (клиники/поставщики, 25 писем)" >> logs/wave3_sent.log
