#!/usr/bin/env bash
# Wave6 — 06.05.2026 10:00 — первая партия новой базы
cd "$(dirname "$0")/.."
exec python3 scripts/send_batch_4domains_generic.py csv_data/wave6_med_domains.csv logs/wave6_sent.log
