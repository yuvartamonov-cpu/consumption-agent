#!/usr/bin/env bash
# Wave9 — 12.05.2026 10:00
cd "$(dirname "$0")/.."
exec python3 scripts/send_batch_4domains_generic.py csv_data/wave9_med_domains.csv logs/wave9_sent.log --send
