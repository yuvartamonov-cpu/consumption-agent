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

- Current synchronized commit after Day 1: `0648ad0`.
- `master`, `origin/master`, and `paperclip/master` were in sync before Day 2 started.
- `telegram_bot.py` is still large, around 3.5k lines.
- `consumption.db.connect` exists and should be the default production DB access path.
- `services/receipt_pipeline.py` exists and already uses the current matcher API.
- `bot/access.py`, `services/ocr.py`, `services/images.py`, and initial repositories exist.
- `repositories/alerts.py` and `repositories/credit.py` were added on Day 1.

## Execution Rules

1. Start with DB Access before major Telegram splitting.
2. Avoid big-bang refactors; keep each day as a testable slice.
3. After every extraction or DB migration slice, run targeted tests first.
4. Keep direct `sqlite3.connect` only in tests, `:memory:` cases, Phone Link temp DB reads, and explicitly documented legacy/import scripts.
5. Do not start Needs + Recommendation MVP until DB, Telegram split, Photo/OCR integration, and Governance seed are stable.

## When Asked For The Plan

Read `references/week_plan.md` and use it as the source of truth. If emailing the plan, send the reference file body, not this short wrapper.
