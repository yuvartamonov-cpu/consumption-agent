#!/usr/bin/env bash
# Wave8 — 08.05.2026 10:00
cd "$(dirname "$0")/.."
exec python3 scripts/send_batch_4domains_generic.py csv_data/wave8_med_domains.csv logs/wave8_sent.log --send
