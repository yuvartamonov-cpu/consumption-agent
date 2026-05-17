---
name: coding_plan_18-23may26
<<<<<<< HEAD
description: Use this skill when planning or executing the consumption_agent coding week of 18-23 May 2026, especially DB Access, Telegram decomposition, Photo/OCR pipeline integration, Memory Lane completion, Governance seed, and operational hardening.
---

# Coding Plan 18-23 May 2026

Use this skill as the current weekly execution plan for `consumption_agent`.

The detailed plan lives in:

```text
skills/coding_plan_18-23may26/references/week_plan.md
```

## Current Baseline

- Current synchronized commit when the plan was written: `bf8b445`.
- `master`, `origin/master`, and `paperclip/master` were in sync.
- `telegram_bot.py` is still large, around 3.5k lines.
- `consumption.db.connect` exists and should become the default production DB access path.
- `services/receipt_pipeline.py` exists and already uses the current matcher API.
- `bot/access.py`, `services/ocr.py`, `services/images.py`, and initial repositories exist.

## Execution Rules

1. Start with DB Access before major Telegram splitting.
2. Avoid big-bang refactors; keep each day as a testable slice.
3. After every extraction or DB migration slice, run targeted tests first.
4. Keep direct `sqlite3.connect` only in tests, `:memory:` cases, Phone Link temp DB reads, and explicitly documented legacy/import scripts.
5. Do not start Needs + Recommendation MVP until DB, Telegram split, Photo/OCR integration, and Governance seed are stable.

## When Asked For The Plan

Read `references/week_plan.md` and use it as the source of truth. If emailing the plan, send the reference file body, not this short wrapper.
=======
description: Подробный план кодирования по проекту Consumption Agent на неделю 18-23 мая 2026 — рефакторинг монолита telegram_bot.py, переход на Vision API для OCR-чеков, унификация конфигов, тесты на парсеры, доработка дедупликации и Markdown-fallback.
---

# План кодирования: 18–23 мая 2026

> **Контекст:** Consumption Agent работает стабильно, но накопился техдолг. Рефакторинг + улучшение качества + перенос OCR на Vision API.
> **Принцип:** каждый день — одна фокус-тема, утром план — вечером коммит/деплой/тесты. Не больше 1-2 крупных задач в день.

---

## 📅 День 1 — Понедельник 18.05 — Подготовка к рефакторингу

**Цель:** заложить инфраструктуру под расщепление `telegram_bot.py` без поломок.

### Задачи
1. **Создать ветку `refactor/handlers-split`** в репо `consumption-agent`.
2. **Снять метрики до рефакторинга:**
   - `wc -l` всех .py файлов в репо
   - количество CommandHandler / CallbackQueryHandler
   - покрытие тестами (`pytest --cov`)
3. **Завести пакет `bot/handlers/`** с подкаталогами:
   - `bot/handlers/expenses.py` (`cmd_dayexp`, `cmd_monthexp`, `cmd_check`)
   - `bot/handlers/items.py` (`cmd_list`, `cmd_items`, `cmd_items_full`, `cmd_add`, `cmd_add_item`)
   - `bot/handlers/memory_lane.py` (`cmd_ml_*`)
   - `bot/handlers/debts.py` (`cmd_debts`, `cmd_fines`)
   - `bot/handlers/carsharing.py` (`cmd_find_car`, `cmd_last_drives`)
   - `bot/handlers/warranties.py` (`cmd_warranties`, `cmd_set_warranty`)
   - `bot/handlers/topics.py` (`cmd_topic_set`, `cmd_topic_list`)
   - `bot/handlers/help.py` (`cmd_help`, `cmd_start`)
   - `bot/callbacks.py` — все `*_callback`
4. **Зафиксировать API подключения handlers** — функции вида `register_handlers(app)` в каждом модуле.
5. **Тестовый перенос:** только `cmd_help` и `cmd_start` (самые независимые) — проверить, что бот запускается.

### Дедлайн
К 22:00 — `bot/handlers/help.py` работает, бот не падает.

### Коммит
`refactor: extract help+start handlers to bot/handlers/help.py`

---

## 📅 День 2 — Вторник 19.05 — Расщепление команд

**Цель:** перенести все основные команды в `bot/handlers/`.

### Задачи
1. Перенести `cmd_list`, `cmd_items`, `cmd_items_full`, `cmd_add`, `cmd_add_item`, `add_photo`, `photo_handler` → `bot/handlers/items.py`.
2. Перенести `cmd_dayexp`, `cmd_monthexp`, `cmd_check`, `cmd_parse` → `bot/handlers/expenses.py`.
3. Перенести `cmd_debts`, `cmd_fines` → `bot/handlers/debts.py`.
4. Перенести `cmd_ml_*` → `bot/handlers/memory_lane.py`.
5. Перенести `cmd_topic_set`, `cmd_topic_list` → `bot/handlers/topics.py`.
6. Перенести `cmd_warranties`, `cmd_set_warranty`, `cmd_alerts` → `bot/handlers/warranties.py`.
7. Перенести `cmd_find_car`, `cmd_last_drives` → `bot/handlers/carsharing.py`.
8. **`telegram_bot.py` сократить** до ~500 строк: только `main()`, `register_*()`, общие helpers (`esc_md`, `get_db`, `add_authorized_handler`).
9. **Запустить тесты + smoke-test:** `/help`, `/list`, `/dayexp`, `/ml_search` — всё должно работать.

### Дедлайн
К 22:00 — бот работает, все команды отвечают.

### Коммит
`refactor: split telegram_bot.py into bot/handlers/* modules`

---

## 📅 День 3 — Среда 20.05 — Callbacks + общие модули

**Цель:** вынести callback-обработчики + общие модули утилит.

### Задачи
1. **Создать `bot/callbacks.py`** — все `*_callback` функции (15+).
2. **Создать `bot/markdown.py`** — `esc_md()`, безопасный sender с fallback на plain-text.
3. **Создать `bot/db.py`** — `get_db()`, контекстный менеджер для соединений.
4. **Создать `bot/icons.py`** — `_source_icon()`, mapping иконок (вынести из `purchase_duplicate_detector.py` + `telegram_bot.py`).
5. **Создать `bot/auth.py`** — `ALLOWED_CHAT_IDS`, `add_authorized_handler`, whitelist-логика.
6. Прогон pytest, smoke-test всех команд.

### Дедлайн
К 22:00 — `telegram_bot.py` ≤ 400 строк, всё разнесено по модулям.

### Коммит
`refactor: extract callbacks, db, markdown, icons, auth into bot/* modules`

---

## 📅 День 4 — Четверг 21.05 — Vision API для OCR чеков

**Цель:** заменить Tesseract на GPT-4o-mini Vision для чеков-картинок.

### Задачи
1. **Создать `cheque_parser_vision.py`** на базе `openai-vision` скилла.
   - Промпт: «Распознай чек. Верни JSON: store, date, time, total, items[name, price, qty]».
   - Fallback на Tesseract, если Vision API недоступен.
2. **Интегрировать в `add_photo`** — приоритет Vision над Tesseract.
3. **Интегрировать в `import_yandex_market_screens.py`** — догнать старые скриншоты Яндекс.Маркета.
4. **Метрики:** добавить в БД таблицу `ocr_attempts` (engine, input_path, output_json, parse_ok, elapsed_ms).
5. **Тест:** прогнать 10 чеков через Vision и Tesseract, сравнить точность.

### Дедлайн
К 22:00 — Vision-парсер работает на ≥ 8/10 чеков.

### Коммит
`feat: добавлен Vision API парсер чеков, fallback на Tesseract`

---

## 📅 День 5 — Пятница 22.05 — Унификация конфигов + Markdown V2

**Цель:** убрать хардкод, перейти на единый Markdown V2.

### Задачи
1. **Создать `consumption_agent/config.yaml`:**
   - `marketplace_senders`: список email-доменов магазинов
   - `store_fuzzy_amount`: список магазинов для нечёткого матчинга сумм
   - `store_aliases`: `umnyy_ritejl → Самокат`, и т.д.
   - `imap_accounts`: список аккаунтов (через переменные окружения для паролей)
   - `dedup_thresholds`: 500₽ для близких сумм, 90 мин для close-in-time
2. **Загрузчик `config.py`** через `pyyaml`, валидация через `pydantic`.
3. **Заменить хардкод в коде** на `config.get('...')`.
4. **Перейти на ParseMode.MARKDOWN_V2 + escape_markdown(text, version=2):**
   - Глобальный `safe_send_message()` в `bot/markdown.py`.
   - Убрать fallback в plain-text (он будет не нужен).
5. **Тест:** прогнать `/dayexp`, `/monthexp` с символами `_*[]()<>~\`#+-=|{}.!`.

### Дедлайн
К 22:00 — все хардкоды вынесены в `config.yaml`, Markdown V2 работает.

### Коммит
`refactor: unified config.yaml + Markdown V2 with escape_markdown`

---

## 📅 День 6 — Суббота 23.05 — Тесты + документация

**Цель:** покрыть критичные парсеры тестами и обновить документацию.

### Задачи
1. **Тесты на парсеры:**
   - `tests/parsers/test_yandex_market_html.py`
   - `tests/parsers/test_ozon_email.py`
   - `tests/parsers/test_sber_sms.py`
   - `tests/parsers/test_vtb_sms.py`
   - Фикстуры: реальные письма из `.eml`-сэмплов (анонимизировать суммы).
2. **Тесты на ml_search pipeline:**
   - `tests/ml/test_query_expansion.py`
   - `tests/ml/test_canonical_groups.py`
   - `tests/ml/test_brand_gating.py`
3. **Обновить `docs/bot_commands.md`** — добавить `/help <команда>`.
4. **Обновить SKILL.md** для всех связанных скиллов (consumption-agent, dayexp, monthexp, memory-lane).
5. **Закрыть ветку `refactor/handlers-split` в master** — слить через PR с самим собой.

### Дедлайн
К 22:00 — `pytest` зелёный, тесты ≥ 80% покрытия на парсерах.

### Коммит
`test: add parser tests + ml_search pipeline tests + docs sync`

---

## 🎯 Метрики успеха недели

| Метрика | До (17.05) | После (23.05) |
|---|---|---|
| `wc -l telegram_bot.py` | 4 065 | ≤ 400 |
| Модулей в `bot/` | 1 (`access.py`) | 10+ |
| Тестов на парсеры | 2 | 8+ |
| OCR engine | только Tesseract | Vision + Tesseract fallback |
| Хардкод (магазины, sender'ы) | в коде | `config.yaml` |
| Markdown-fallback | есть (костыль) | не нужен |

---

## ⚠️ Риски и страховки

1. **Регрессии после расщепления** — для каждого дня smoke-test всех команд через Telegram.
2. **Vision API стоит денег** — лимит $5/неделя, мониторить через `/status`.
3. **Markdown V2** может сломать старые сообщения с reply-markup — проверить кнопки `dedup_delete`, `credit_paid`.
4. **Конфликт с `daily_cheque_scan` по cron** — не катить рефакторинг в 23:30 (когда крон запускается).

---

## 📝 Что не делаем на этой неделе

- Не трогаем `consumption.db` schema (миграции — отдельный спринт).
- Не делаем web-интерфейс.
- Не оптимизируем ml_search pipeline (он работает, не до того).
- Не переезжаем на PostgreSQL.

---

## Контакты

- **Юрий:** yu.v.artamonov@gmail.com
- **Бот:** @ConsumptionAgentBot
- **Репо:** github.com/yuvartamonov-cpu/consumption-agent
- **Ветка:** `refactor/handlers-split` (создать 18.05)

*Скилл создан: 17.05.2026, 20:15 MSK*
>>>>>>> 5b997ab (feat: расшифровка 'Прочее' в email-отчёте dayexp)
