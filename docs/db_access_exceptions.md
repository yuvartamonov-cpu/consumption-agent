# DB Access Exceptions

Day 2 DB Access Completion moves important production code to
`consumption.db.connect`, which sets WAL, `foreign_keys`, `busy_timeout`,
`row_factory`, and retry behavior.

Raw `sqlite3.connect` is allowed only in these cases:

- `consumption/consumption.db.py`: the shared helper implementation itself.
- `tests/**` and `test_*.py`: isolated fixtures, monkeypatches, and `:memory:`
  scenarios.
- `consumption_agent_full_030526.py`: legacy monolith kept until the legacy
  retirement sprint.
- Phone Link / SMS temp database reads, with an inline comment next to the raw
  connect. These are copied external SQLite databases, not `consumption.db`.
- One-off import, cleanup, migration, and diagnostic scripts that are not on the
  main bot/runtime path. Convert them to `consumption.db.connect` when they
  graduate into production workflows.

Important production files migrated in Day 2:

- `daily_cheque_scan.py`
- `email_importer.py`
- `ml_search.py`
- `matcher.py`
- `warranty_check.py`
- `scripts/fines_bot.py`
