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

**Updated 18.05.2026 evening — Days 1–4 ahead of schedule.**

- Current synchronized commit: `6278ba8 feat: LLM-translate, source matcher, smart source routing`.
- `master`, `origin/master`, `github/master`, `gh-consumption/master`, `github-consumption/master` in sync.
- `telegram_bot.py` shrunk from ~3 485 → **1 274 lines** (target ≤1500 already met).
- `bot/` module structure complete: `app.py`, `callbacks.py` (865), `markdown.py`, `ui.py`, and `bot/handlers/{help,finance,items,memory_lane,carsharing}.py`.
- `bot/handlers/photos.py` is **still a 10-line placeholder** — `photo_handler` (~543 lines) lives in `telegram_bot.py`.
- `services/photo_pipeline.py` does **not exist yet** — receipt_pipeline, ocr, images are in place.
- `repositories/`: `alerts.py`, `credit.py`, `items.py`, `purchases.py`, `media.py` exist.
- `consumption.db.connect` is the default. Remaining direct `sqlite3.connect` in production code: `ml_source_matcher.py`, `ml_search_v2.py`, `daily_cheque_scan.py`, `scripts/fines_bot.py`, `sms_monitor.py`.
- `/ml_find`, `/ml_profile` — not implemented.
- Governance tables (`action_proposals`, `approvals`, `audit_events`) — not implemented.
- Bonus shipped today: `ml_translate.py` (LLM-перевод GPT-4o-mini) + `ml_source_matcher.py` (49 источников, learned ranking).
- Tests: **529 passed.**

## Status of Days 1–4 (closed today, Mon 18.05)

| Day | Plan | Status | Commit |
|---|---|---|---|
| 1 | DB Access Baseline | ✅ | `0648ad0`, `4d14a6d` |
| 2 | DB Access Completion | ✅ (closed ahead) | `4d14a6d` |
| 3 | Telegram Split Safe Start | ✅ | `f72c3ef` + `4fc25b6` |
| 4 | Telegram Commands & Callbacks | ✅ | `1d72232` |
| — | Bonus: LLM-translate + source matcher | ✅ | `6278ba8` |

The plan for Tue 19.05 → Thu 21.05 was rewritten — see `references/week_plan.md`.

## Execution Rules

1. Start with DB Access before major Telegram splitting.
2. Avoid big-bang refactors; keep each day as a testable slice.
3. After every extraction or DB migration slice, run targeted tests first.
4. Keep direct `sqlite3.connect` only in tests, `:memory:` cases, Phone Link temp DB reads, and explicitly documented legacy/import scripts.
5. Do not start Needs + Recommendation MVP until DB, Telegram split, Photo/OCR integration, and Governance seed are stable.

## When Asked For The Plan

Read `references/week_plan.md` and use it as the source of truth. If emailing the plan, send the reference file body, not this short wrapper.
