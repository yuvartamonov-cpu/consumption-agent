#!/bin/bash
# Отправка волны 4 (10 писем, medstartup: Webiomed, Cельс, СберМедИИ, Моторика)
cd /home/yuri_artamonov/.openclaw/workspace/domains_sale

# Подменяем next_batch на wave4_startups
cp csv_data/wave4_startups.csv csv_data/wave2_next_batch.csv

# Отправляем
python3 scripts/send_batch_4domains.py send-all --yes

# Помечаем в логе
echo "$(date '+%Y-%m-%d %H:%M') — wave4 отправлена (medstartup, 10 писем)" >> logs/wave4_sent.log
