#!/usr/bin/env bash
# Wave7 — 07.05.2026 10:00
cd "$(dirname "$0")/.."
exec python3 scripts/send_batch_4domains_generic.py csv_data/wave7_med_domains.csv logs/wave7_sent.log --send
