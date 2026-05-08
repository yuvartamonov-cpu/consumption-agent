#!/usr/bin/env bash
# Daily consumption agent update
# Runs: import + parse + match + enrich + report
# Expected in crontab at 10:00

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if exists
[ -d venv/bin ] && source venv/bin/activate

# Load secrets from .env (600 permissions)
[ -f .env ] && set -a && source .env && set +a

python3 consumption_agent_full_030526.py all
