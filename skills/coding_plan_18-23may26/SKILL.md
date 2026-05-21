---
name: coding_plan_18-23may26
description: Use this skill when planning or executing the consumption_agent coding week of 18-23 May 2026, especially DB Access, Telegram decomposition, Photo/OCR pipeline integration, Memory Lane completion, Governance seed, and operational hardening.
---

# Coding Plan 18-23 May 2026

Use this skill as the current weekly execution plan for `consumption_agent`.

The detailed plan lives in:

```text
skills/coding_plan_18-23may26/references/week_plan.md
```

## Current Baseline

**Updated 21.05.2026 afternoon.**

- Telegram split moved further: `telegram_bot.py` is down to ~720 lines and acts mainly as entrypoint/shared wiring.
- `bot/handlers/photos.py` is no longer a placeholder; photo/tag/text flow lives there, with heavy reusable logic in `services/photo_pipeline.py`.
- `services/photo_pipeline.py` exists and is part of the active production path.
- `repositories/`: `alerts.py`, `credit.py`, `items.py`, `purchases.py`, `media.py` exist.
- `consumption.db.connect` is the default. Remaining direct `sqlite3.connect` in production/legacy code: `ml_source_matcher.py`, `ml_search_v2.py`, `daily_cheque_scan.py`, `scripts/fines_bot.py`, `sms_monitor.py`, plus legacy branches in `consumption_agent_full_030526.py`.
- `consumption_agent_full_030526.py` is now clearly legacy CLI glue: duplicated `init` and `report` logic removed; they delegate to `init_db.initialize_database(...)` and `gen_report.generate_report(...)`.
- Email/SMS dedup rules were tightened on 21.05.2026; authoritative search/anti-dup rules live in `skills/email-access/SKILL.md`.
- Historical duplicate purchases since `2026-05-01` were soft-deleted in DB after backup (`consumption.db.bak_2026-05-21_pre_dedup`).
- `/ml_find`, `/ml_profile` — not implemented.
- Governance tables (`action_proposals`, `approvals`, `audit_events`) — not implemented.
- Tests: full `pytest -q` green on 21.05.2026 after monolith cleanup + dedup fixes.

## Status of Days 1–4

| Day | Plan | Status | Commit |
|---|---|---|---|
| 1 | DB Access Baseline | ✅ | `0648ad0`, `4d14a6d` |
| 2 | DB Access Completion | ✅ (closed ahead) | `4d14a6d` |
| 3 | Telegram Split Safe Start | ✅ | `f72c3ef` + `4fc25b6` |
| 4 | Telegram Commands & Callbacks | ✅ | `1d72232` |
| — | Bonus: LLM-translate + source matcher | ✅ | `6278ba8` |

Tue 19.05 → Thu 21.05 follow-up execution changed the baseline further:
- photo handler extraction completed enough for production use;
- monolith cleanup started with safe delegation instead of big-bang removal;
- dedup/search rules for email + SMS were hardened and documented in skills.

## Execution Rules

1. Start with DB Access before major Telegram splitting.
2. Avoid big-bang refactors; keep each day as a testable slice.
3. After every extraction or DB migration slice, run targeted tests first.
4. Keep direct `sqlite3.connect` only in tests, `:memory:` cases, Phone Link temp DB reads, and explicitly documented legacy/import scripts.
5. Do not start Needs + Recommendation MVP until DB, Telegram split, Photo/OCR integration, and Governance seed are stable.

## When Asked For The Plan

Read `references/week_plan.md` and use it as the source of truth. If emailing the plan, send the reference file body, not this short wrapper.
