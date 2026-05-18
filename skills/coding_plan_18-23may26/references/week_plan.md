# План кодирования consumption_agent на неделю 18-23 мая 2026

Дата подготовки: 17.05.2026
Обновлено: 18.05.2026, 20:30 — Days 1–4 закрыты досрочно (Codex прошёл Day 3+4 за один день), Days 5–6 заменены на трёхдневный план 19–21.05.

Актуальный коммит: `6278ba8 feat: LLM-translate, source matcher, smart source routing`.

Актуальная база:

- `master`, `origin/master` и `paperclip/master` синхронизированы.
- Базовый коммит на момент плана: `bf8b445 fix: make work email sending use smtp skill`.
- `telegram_bot.py` всё ещё около 3 485 строк.
- `consumption.db.connect` уже есть и включает WAL, `foreign_keys`, `busy_timeout`, `row_factory`, retry.
- `services/receipt_pipeline.py` уже есть, использует актуальную сигнатуру `match_record` через `_build_normalized_index` и `norm_item_cache`.
- Уже есть `bot/access.py`, `services/ocr.py`, `services/images.py`, `repositories/items.py`, `repositories/purchases.py`, `repositories/media.py`.
- За предыдущие дни уже закрыты: IMAP ScanMetrics, official/distributor resolver, RU to EN translation/stemming/geolocation, `/ml_search` pagination, Memory Lane price-drop watchlist, SMTP skill для рабочей почты.

## Что было сделано за последние три дня / спринта

1. DB foundation частично готов:
   - создан `consumption.db.connect`;
   - включены WAL, `foreign_keys`, `busy_timeout`, `row_factory`;
   - есть retry helper `execute_with_retry`;
   - часть нового pipeline уже использует общий DB helper.

2. Telegram decomposition начат, но не завершён:
   - вынесен `bot/access.py`;
   - access guard переведён на env/config-friendly подход;
   - тяжёлая обработка фото частично вынесена в services/repositories;
   - основной `telegram_bot.py` всё ещё остаётся большим entrypoint/monolith.

3. Photo/OCR pipeline существенно продвинут:
   - есть `services/receipt_pipeline.py`;
   - поддержаны image/pdf/text входы;
   - есть OCR/parser/structured receipt/matcher/persistence flow;
   - есть Vision fallback после слабого OCR;
   - delivery/service fee нормализуется как first-class output;
   - есть dry-run режим и тесты на receipt pipeline.

4. Memory Lane search закрыла предыдущую пятидневку:
   - official/distributor resolver;
   - перевод foreign queries;
   - source ordering;
   - pagination;
   - price-drop watchlist.

5. Mail/reporting stability улучшена:
   - ScanMetrics;
   - обход INBOX, Spam/Junk/Спам, Receipts/чеки;
   - интеграционные тесты IMAP scan path.

## Что осталось критичным

- В production-коде всё ещё много прямых `sqlite3.connect`.
- Не хватает `repositories/alerts.py` и `repositories/credit.py`.
- Schema guards и `ALTER TABLE` разбросаны по handlers/services: `credit_monitor.py`, `memory_lane.py`, `ml_attributes.py`, `warranty_check.py`, repositories.
- `telegram_bot.py` всё ещё содержит большинство handlers/callbacks.
- `/ml_find` и `/ml_profile` ещё не реализованы.
- Governance MVP ещё не начат: нет `action_proposals`, `approvals`, `audit_events`, policy engine.
- Нет единого воспроизводимого dependency/test baseline (`pyproject.toml` или `requirements-dev.txt`).

---

## ✅ День 1 - Понедельник 18.05 - DB Access Baseline (ЗАКРЫТ)

Цель: убрать хаос с SQLite до большого Telegram split.

Задачи:

1. Добавить воспроизводимый test/dependency baseline:
   - `pyproject.toml` или `requirements-dev.txt`;
   - `python-telegram-bot[job-queue]`;
   - `pytest`;
   - `pytest-asyncio`;
   - `rapidfuzz`.
2. Добавить тесты на `consumption.db.connect`:
   - `PRAGMA foreign_keys=ON`;
   - `journal_mode=WAL`;
   - `busy_timeout`;
   - `row_factory=sqlite3.Row`;
   - retry path на transient `database is locked`.
3. Создать repositories:
   - `repositories/alerts.py`;
   - `repositories/credit.py`.
4. Перевести high-risk production files на `consumption.db.connect`:
   - `receipt_parser.py`;
   - `daily_report.py`;
   - `credit_alerts.py`;
   - `memory_lane.py`;
   - `backfill_ozon_items.py`.
5. Убрать дублирующие локальные DB helpers там, где общий helper уже покрывает WAL, timeout и row factory.

Acceptance:

- Targeted tests green: DB helper, receipt pipeline, migrated modules.
- Новые repository-модули не дублируют raw SQL по handlers.
- Нет регрессии в dry-run receipt pipeline.

Коммит:

```text
refactor: centralize core db access
```

---

## ✅ День 2 - Вторник 19.05 - DB Access Completion (ЗАКРЫТ ДОСРОЧНО, commit `4d14a6d`)

Цель: довести DB Access Sprint до измеримого состояния.

Задачи:

1. Перевести оставшиеся важные production files:
   - `daily_cheque_scan.py`;
   - `email_importer.py`;
   - `ml_search.py`;
   - `matcher.py`;
   - `warranty_check.py`;
   - `scripts/fines_bot.py`.
2. Оставить прямой `sqlite3.connect` только в допустимых местах:
   - tests;
   - `:memory:` scenarios;
   - Phone Link temp DB reads;
   - legacy monolith до retirement;
   - одноразовые migration/import scripts с явным комментарием.
3. Вынести повторяющиеся `ALTER TABLE` и schema guards в:
   - `init_db.py`;
   - migration helpers;
   - repository-level `ensure_*` только если это temporary compatibility layer.
4. Свести `telegram_bot.py:get_db_with_retry` к wrapper вокруг `consumption.db.connect` или удалить, если больше не нужен.
5. Добавить documented exceptions для оставшихся raw connects.

Acceptance:

```powershell
rg sqlite3.connect consumption_agent -g "*.py"
```

показывает только разрешённые места и documented exceptions.

Коммит:

```text
refactor: complete db access migration
```

---

## ✅ День 3 - Среда 20.05 - Telegram Split Safe Start (ЗАКРЫТ ДОСРОЧНО, commit `f72c3ef` + `4fc25b6`)

Цель: начать decomposition без big bang и без поломки бота.

Задачи:

1. Создать структуру:
   - `bot/app.py`;
   - `bot/handlers/help.py`;
   - `bot/handlers/finance.py`;
   - `bot/handlers/items.py`;
   - `bot/handlers/memory_lane.py`;
   - `bot/handlers/photos.py`;
   - `bot/handlers/__init__.py`.
2. Перенести только самые независимые команды:
   - `/start`;
   - `/help`;
   - `/check`;
   - `/alerts`.
3. Ввести единый pattern:
   - каждый handler-модуль экспортирует `register_handlers(app, deps=None)`;
   - `bot/app.py` собирает регистрацию.
4. Создать `bot/markdown.py`:
   - `esc_md`;
   - safe send helper;
   - fallback на plain text, если Markdown ломается.
5. Оставить compatibility imports в `telegram_bot.py`, чтобы не делать одномоментный разрыв.

Acceptance:

- Бот стартует.
- `/start`, `/help`, `/check`, `/alerts` отвечают.
- `telegram_bot.py` уменьшается без массового переноса рискованных команд.

Коммит:

```text
refactor: extract basic telegram handlers
```

---

## ✅ День 4 - Четверг 21.05 - Telegram Commands And Callbacks (ЗАКРЫТ ДОСРОЧНО, commit `1d72232`)

Цель: вынести основную массу handlers/callbacks и оставить `telegram_bot.py` тоньше.

Задачи:

1. Перенести finance handlers:
   - `/dayexp`;
   - `/monthexp`;
   - `/debts`;
   - `/fines`.
2. Перенести item handlers:
   - `/list`;
   - `/items`;
   - `/items_full`;
   - `/add`;
   - `/add_item`.
3. Перенести Memory Lane handlers:
   - `/ml_last`;
   - `/ml_search`;
   - `/ml_stats`;
   - `/ml_watch`;
   - `/ml_unwatch`.
4. Создать `bot/callbacks.py`:
   - credit/fine callbacks;
   - item callbacks;
   - Memory Lane callbacks;
   - dedup callbacks;
   - vision callbacks.
5. Создать `bot/ui.py`:
   - inline keyboards;
   - callback data builders;
   - reusable message formatting blocks.
6. Не углубляться в OCR/photo internals в этот день, только перенести wiring.

Acceptance:

- `telegram_bot.py` <= 1 500 строк.
- handler-файлы желательно <= 500 строк.
- Smoke-test проходит для `/help`, `/list`, `/dayexp`, `/ml_search`, `/ml_watch`, `/debts`.

Коммит:

```text
refactor: split telegram commands and callbacks
```

---

## 🔄 Обновлённый план 19–21 мая (Days 5/6 заменены)

После того как Codex закрыл Days 3–4 одним коммитом (`f72c3ef` + `1d72232`), а также шипнут бонусный `6278ba8` с LLM-translate / source matcher, реальный список задач на оставшиеся три дня переписан так:

---

## День 1 нового плана — Вторник 19.05 — Photo Pipeline + Extraction

Цель: вынести `photo_handler` (≈543 строки, последний крупный кусок `telegram_bot.py`) и завести независимый pipeline.

Задачи:

1. Создать `services/photo_pipeline.py`:
   - `classify(image_path, caption) → 'memory_lane' | 'receipt' | 'tag' | 'item'`
   - `extract_receipt(image_path)` → items / total / store / delivery_fee через OCR + QR + Vision fallback
   - `extract_memory_lane(image_path, caption)` → vision_info (name/brand/description/style_tags/topic)
   - `extract_item(image_path)` → vision_item.recognize_item
   - `persist(result, profile_id, conn)` → распределение по таблицам (items, purchases, memory_lane_items, media_assets)
2. Перенести из `telegram_bot.py` в `bot/handlers/photos.py` (сейчас 10-строчная заглушка):
   - `photo_handler` (line 592 → ~1135)
   - `add_photo` (line 551–555)
   - `text_handler` со state-машиной `vision_awaiting_notes` (line 558–590)
3. Pipeline зовётся через `asyncio.to_thread`, чтобы не блокировать event loop.
4. Добавить таблицу `ocr_attempts` (id, image_sha, engine, status, elapsed_ms, error) + helper `log_ocr_attempt(...)`.
5. Sanitized fixtures для тестов: Ozon, Samokat OFD, Yandex, SMS-derived, blurry photo.
6. Delivery/service fee — first-class output, отделён от items.

Acceptance:

- `telegram_bot.py` ≤ 700 строк.
- `services/photo_pipeline.py` тестируется без Telegram (Vision и Tesseract — моки).
- Плохой OCR уходит в Vision fallback.
- `bot/handlers/photos.py` ≈ 300–400 строк (thin orchestrator).

Коммит:

```text
refactor: extract photo handler into services/photo_pipeline
```

---

## День 2 нового плана — Среда 20.05 — Memory Lane Find/Profile + ML DB Cleanup

Цель: дать команды `/ml_find` и `/ml_profile`, добить `sqlite3.connect` в ML-модулях, инструментировать новый source_matcher.

Задачи:

1. `/ml_find <query>`:
   - SQL LIKE по `caption`, `name`, `description`, `brand`, `style_tags`
   - Опциональные флаги `--topic`, `--brand`, `--color`
   - Исключать строки с `deleted_at IS NOT NULL`
   - Inline-кнопка «🔍 Искать» рядом с каждым результатом → запуск `/ml_search` для найденной записи
2. `/ml_profile [topic]`:
   - Агрегация: top-5 liked features, top-5 disliked, top brands, top colors/materials, top style_tags
   - Последние 5 примеров с фото
   - Без LLM, чистая SQL-агрегация
3. Расширить `/ml_stats` — добавить разбивку по `source_matcher` (tier × geo × CTR за 30 дней), чтобы видеть качество новой системы выбора источников.
4. Перевести оставшиеся ML-модули на `consumption.db.connect`:
   - `ml_source_matcher.py` (1 случай `sqlite3.connect`)
   - `ml_search_v2.py` (1 случай)
5. Тесты:
   - `/ml_find` empty DB, with filters, deleted excluded;
   - `/ml_profile` со всеми темами и без них;
   - Пустая база, профиль с одним примером.

Acceptance:

- Обе команды работают без LLM.
- `rg sqlite3.connect ml_*.py` пусто.
- 540+ tests зелёные.

Коммит:

```text
feat: add /ml_find and /ml_profile, finish ML db migration
```

---

## День 3 нового плана — Четверг 21.05 — Governance Seed + Final DB Cleanup

Цель: заложить proposal-first фундамент и закрыть остатки прямых `sqlite3.connect`.

Задачи:

1. Idempotent миграции:
   - `action_proposals(id, proposal_type, risk_level, status, evidence_json, created_at, ...)`;
   - `approvals(id, proposal_id, channel, confirmation_hash, approved_at, ...)`;
   - `audit_events(id, event_type, actor_type, input_hash, output_hash, ts, ...)`.
2. `governance/proposals.py`:
   - `create(proposal_type, evidence, risk_level) → proposal_id`;
   - `approve(proposal_id, channel='telegram')`;
   - `reject(proposal_id, reason)`;
   - `explain(proposal_id) → str` (human-readable);
   - Policy enum: `low | medium | high | critical`;
   - Без внешних side effects — только proposal-first contract.
3. `repositories/proposals.py` — query helpers поверх трёх таблиц.
4. Пример wiring: научить `run_price_drop_check` создавать `action_proposal` типа `notify_price_drop` с `risk_level=low` (пока с автоматическим approve, чтобы протестировать контракт).
5. Перевести на `consumption.db.connect`:
   - `daily_cheque_scan.py` (2);
   - `scripts/fines_bot.py` (2);
   - `sms_monitor.py` (1).
6. Тесты: create / approve / reject / explain, idempotent schema, risk_level enum constraint.

Acceptance:

- Governance schema создаётся idempotently.
- 4 функции `governance/proposals.py` зелёные в тестах.
- `rg "sqlite3.connect" --type py` показывает только tests и legacy backup.

Коммит:

```text
feat: governance seed (action_proposals, approvals, audit_events)
```

---

## Метрики успеха недели (обновлено)

| Метрика | На утро 18.05 | Сейчас (вечер 18.05) | Цель к 21.05 |
|---|---:|---:|---:|
| `telegram_bot.py` | ~3 485 строк | 1 274 | ≤ 700 |
| Прямые `sqlite3.connect` в production | много | 5 файлов | только tests + legacy |
| Repository modules | items/purchases/media | + alerts/credit | + proposals |
| `/ml_find`, `/ml_profile` | нет | нет | есть |
| Governance tables | нет | нет | есть |
| `services/photo_pipeline.py` | нет | нет | ≈ 400–500 строк, тестируемый |
| `bot/handlers/photos.py` | нет | 10 (placeholder) | 300–400 строк |
| Dependency/test baseline | есть | есть | есть |
| Тесты | 517 | 529 | ≥ 565 |

## Что не делать на этой неделе

- Не начинать Needs + Recommendation MVP до стабильного Governance.
- Не делать полный Legacy Retirement `consumption_agent_full_030526.py`.
- Не переезжать на PostgreSQL.
- Не делать web UI.
- Не добавлять embeddings для `/ml_find` — сначала SQL/LIKE должен доказать.
- Не делать большой Markdown V2/config rewrite одновременно с handler split.

## Главный порядок (актуальный)

1. ✅ DB Access (Days 1–2 закрыты).
2. ✅ Telegram split (Days 3–4 закрыты).
3. 🔄 Photo/OCR integration (Tue 19.05).
4. 🔄 Memory Lane UX + ML cleanup (Wed 20.05).
5. 🔄 Governance seed + final DB cleanup (Thu 21.05).

Каждый день заканчивается маленьким проверяемым slice, targeted tests, коммитом и sync WSL ↔ bare-repo ↔ GitHub.
