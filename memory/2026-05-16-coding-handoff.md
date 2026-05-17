# Coding Handoff - 2026-05-17

Source requested: today's coding plan and roadmap from mail.

Gmail connector search was attempted on 2026-05-16, but the connector returned `ACCESS_TOKEN_SCOPE_INSUFFICIENT`, so mail contents could not be read directly. Equivalent current plan/roadmap content is available locally after the GitHub/Paperclip sync:

- `consumption_agent/05_coding_plan_2026-05-16.md`
- `consumption_agent/04_roadmap.md`
- current synchronized commit: `073a26118749dd52bacdb58c186e207a96867f60`

## Tomorrow Start

Begin with Day 1 from the 5-day plan: observability and testability of mail scanning.

Concrete first slice:

1. Inspect current mail scan code:
   - `imap_folders.py`
   - `daily_cheque_scan.py`
   - `credit_monitor.py`
   - `scripts/fines_bot.py`
   - `tests/test_imap_folders.py`
2. Add a unified scan log shape for mail scanners.
3. Track metrics:
   - `folders_scanned`
   - `messages_seen`
   - `messages_deduped`
   - `messages_parsed`
4. Add mock IMAP integration tests for `LIST`, `SELECT`, `SEARCH`, and `FETCH`.
5. Verify scanning across INBOX, Spam/Junk/Спам, and Receipts/чеки folders.

## Guardrails

- Keep this as a small verifiable slice before moving to Memory Lane Day 2.
- Do not log secrets, email passwords, tokens, or raw personal mail bodies.
- Prefer tests around scanner behavior before broad refactors.
- Run targeted tests first, then full pytest if the slice is green.

## Roadmap Context

Roadmap current sprint remains Memory Lane MVP + Visual Search, with pending work around `/ml_find`, `/ml_profile`, structured official/distributor resolution, CLIP visual gate, reverse image search provider, and price-drop tracking.

The plan intentionally starts with mail scan observability because user-facing report/debt commands need stable diagnostics before expanding feature work.
