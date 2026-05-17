# План кодирования consumption_agent на неделю 18-23 мая 2026

Дата подготовки: 17.05.2026

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

## День 1 - Понедельник 18.05 - DB Access Baseline

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

## День 2 - Вторник 19.05 - DB Access Completion

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

## День 3 - Среда 20.05 - Telegram Split Safe Start

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

## День 4 - Четверг 21.05 - Telegram Commands And Callbacks

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

## День 5 - Пятница 22.05 - Photo/OCR Pipeline Integration

Цель: связать уже созданный receipt pipeline с Telegram и убрать дубли логики из `photo_handler`.

Задачи:

1. Создать `services/photo_pipeline.py`:
   - classify;
   - QR;
   - OCR;
   - Vision receipt;
   - fallback parser;
   - persistence.
2. Подключить `photo_handler` к pipeline через `asyncio.to_thread`.
3. Вынести стратегии:
   - `receipt_ocr`;
   - `vision_receipt`;
   - `vision_item`;
   - tag parsing.
4. Добавить lightweight `ocr_attempts` или логируемый helper:
   - engine;
   - input path;
   - parse status;
   - elapsed ms;
   - failure reason.
5. Собрать sanitized fixtures:
   - Ozon;
   - Samokat OFD;
   - Yandex;
   - SMS-derived;
   - blurry photo.
6. Delivery/service fee держать first-class output: item vs delivery.

Acceptance:

- Pipeline тестируется без Telegram.
- Vision/Tesseract мокируются.
- Плохой OCR уходит в Vision fallback.
- Telegram photo path не блокирует event loop.

Коммит:

```text
refactor: route telegram photos through photo pipeline
```

---

## День 6 - Суббота 23.05 - Memory Lane Completion And Governance Seed

Цель: дать пользователю новые Memory Lane команды и заложить безопасный proposal-first фундамент.

Задачи Memory Lane:

1. Реализовать `/ml_find <query>`:
   - SQL LIKE;
   - topic/style/brand/color filters;
   - deleted rows excluded;
   - кнопка запуска `/ml_search` для найденной записи.
2. Реализовать `/ml_profile <topic>`:
   - liked features;
   - disliked features;
   - style tags;
   - brands;
   - colors/materials;
   - последние 5 примеров.
3. Не добавлять embeddings, пока простой SQL/text search не доказан.
4. Покрыть тестами поиск, профиль, пустую базу и deleted records.

Задачи Governance seed:

1. Добавить таблицы:
   - `action_proposals`;
   - `approvals`;
   - `audit_events`.
2. Создать `governance/proposals.py`:
   - create;
   - approve;
   - reject;
   - explain.
3. Добавить минимальный policy enum:
   - low;
   - medium;
   - high;
   - critical.
4. Пока без внешних side effects: только proposal-first contract.

Acceptance:

- `/ml_find` и `/ml_profile` работают без LLM.
- Governance schema создаётся idempotently.
- Tests на create/approve/reject зелёные.

Коммит:

```text
feat: add memory lane find/profile and governance seed
```

---

## Метрики успеха недели

| Метрика | Сейчас | Цель к 23.05 |
|---|---:|---:|
| `telegram_bot.py` | ~3 485 строк | <= 1 500 строк |
| Прямые `sqlite3.connect` в production | много | только documented exceptions |
| Repository modules | items/purchases/media | + alerts/credit |
| `/ml_find` | нет | есть |
| `/ml_profile` | нет | есть |
| Governance tables | нет | есть |
| Photo pipeline без Telegram | частично | тестируемый service |
| Dependency/test baseline | нет | есть |

## Что не делать на этой неделе

- Не начинать Needs + Recommendation MVP до Governance seed.
- Не делать полный Legacy Retirement `consumption_agent_full_030526.py`, только подготовить переносы и documented exceptions.
- Не переезжать на PostgreSQL.
- Не делать web UI.
- Не делать большой Markdown V2/config rewrite в тот же момент, что и handler split.

## Главный порядок

1. DB Access.
2. Telegram split.
3. Photo/OCR integration.
4. Memory Lane UX.
5. Governance seed.

Каждый день заканчивается маленьким проверяемым slice, targeted tests, коммитом и sync GitHub <-> bare repo.
