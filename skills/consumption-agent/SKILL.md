---
name: consumption-agent
description: >
  Consumption Agent — Telegram-бот для учёта расходов, инвентаря товаров, каршеринга и кредитных уведомлений.
  Используй когда нужно: (1) работать с базой потребления (SQLite consumption.db),
  (2) добавлять/редактировать поездки каршеринга, (3) настраивать тарифы каршеринга,
  (4) анализировать чеки, импортировать данные с почты, (5) работать с кредитным мониторингом.
---

# Consumption Agent

## Overview

Telegram-бот @ConsumptionAgentBot для управления расходами. База — SQLite `consumption.db`. Работает как systemd-юнит (`consumption-bot.service`).

**Стек:** python-telegram-bot, SQLite, Tesseract OCR, fpdf2, rapidfuzz.

## Актуальные алгоритмы

Для фактических production-алгоритмов по состоянию кода см.:

- `consumption_agent/docs/recognition_algorithms.md`

Особенно важно:

- чеки: `QR/Tesseract -> EasyOCR -> Vision fallback -> parse -> match -> LLM category classify -> human review on low confidence`;
- фото предметов: `Vision classify -> Vision recognize -> category suggestion`;
- бирки: `OCR + crop OCR + barcode + size/article/color/price extraction`;
- Memory Lane search: `Vision attrs -> query expansion -> semantic translation -> federated retrieval -> canonicalize -> rerank`.

## Структура кода (обновлено 21.05.2026)

```
consumption_agent/
├── telegram_bot.py            # entrypoint / wiring / shared globals for handlers
├── bot/                       # Telegram-слой
│   ├── app.py                 # HandlerDeps + register_command_handlers / register_callback_handlers
│   ├── callbacks.py           # 865 строк — все callback handlers
│   ├── markdown.py            # esc_md, safe_send/edit_markdown_message
│   ├── ui.py                  # inline keyboards, callback data builders
│   ├── access.py              # access guard (whitelist chat ids)
│   └── handlers/
│       ├── help.py            # /start, /help (118 строк)
│       ├── finance.py         # /alerts, /check, /dayexp, /monthexp, /debts, /fines, /warranties (538)
│       ├── items.py           # /list, /items, /items_full, /add, /add_item (813)
│       ├── memory_lane.py     # /ml_last, /ml_search, /ml_stats, /ml_watch, /ml_unwatch (420)
│       ├── carsharing.py      # /find_car, /last_drives (220)
│       └── photos.py          # photo/tag/text handlers; heavy logic вынесена в services.photo_pipeline
├── services/
│   ├── receipt_pipeline.py    # 507 строк — receipt OCR+parse+match+persist
│   ├── photo_pipeline.py      # pure helpers for photo/tag routing and receipt/item heuristics
│   ├── ocr.py                 # 457 строк — Tesseract / Vision orchestration
│   └── images.py              # 86 строк — sha256-deduped media storage
├── repositories/
│   ├── items.py
│   ├── purchases.py
│   ├── media.py
│   ├── alerts.py
│   └── credit.py
├── ml_search_v2.py            # Memory Lane visual search orchestrator
├── ml_source_matcher.py       # 953 строки — 49 источников с tier/geo, learned ranking
├── ml_translate.py            # LLM-перевод RU→EN через GPT-4o-mini, fallback на словарь
├── ml_providers.py            # WB / Ozon / YM / retailer_links / composite
├── ml_canonical.py            # brand/category normalization
├── ml_taste.py                # liked/disliked aggregation для /ml_profile
├── ml_watchlist.py            # price-drop watchlist
├── memory_lane.py             # ядро Memory Lane (parse_caption, save_*)
├── gen_report.py              # reusable PDF report module (generate_report)
├── init_db.py                 # reusable DB init/migration module (initialize_database)
├── consumption_agent_full_030526.py  # legacy CLI shell; init/report уже делегированы в shared modules
└── consumption/db.py          # ЕДИНАЯ точка connect (WAL, foreign_keys, retry, row_factory)
```

Остаточные прямые `sqlite3.connect` в production/legacy коде, которые ещё стоит дожать: `ml_source_matcher.py`, `ml_search_v2.py`, `daily_cheque_scan.py`, `scripts/fines_bot.py`, `sms_monitor.py`, а также legacy-ветки в `consumption_agent_full_030526.py`, кроме уже делегированных `init/report`.

## Команды бота

| Команда | Описание |
|---------|----------|
| `/list` | Инвентарь по категориям |
| `/alerts` | Алерты (гарантии, сроки) |
| `/find_car 3ч 80км` | Подбор тарифа каршеринга |
| `/last_drives [N] [provider]` | Последние поездки (фильтр: yandex_drive, citydrive, belka, delimobil) |
| `/debts` | Кредиты к оплате в ближайшие 30 дней |
| `/fines` | Неоплаченные штрафы |
| `/warranties` | Отчёт по гарантиям |
| `/add <name> [price] [category]` | Добавить товар |
| `/add_photo` | Фото чека (OCR) |
| `/check` | Статистика |
| `/ml_last [N]` | Последние N записей Memory Lane |
| `/ml_search <id>` | Visual product search v2 (с пагинацией и watchlist) |
| `/ml_stats` | CTR по источникам + bandit snapshot |
| `/ml_watch` | **Day 5:** активные price-drop watches |
| `/ml_unwatch <id>` | **Day 5:** убрать товар из watchlist |
| `/topic_set <слово> <тема>` | Задать тему для слова |
| `/topic_list [тема]` | Все правила тем |
| `/help` | Список команд (или `/help <команда>` — подробно) |
| `/items [all|<категория>]` | Список вещей (по умолчанию — с истекающими сроками) |
| `/items_full [all|<категория>]` | Полный вывод вещей с фото и доп. данными |
| `/dayexp [N]` | Расходы за N дней (по умолчанию 1). Источники: почта, SMS, выписки |
| `/monthexp` | Расходы с начала месяца с расшифровкой по дням |

См. подробные инструкции по каждой команде в **`docs/bot_commands.md`** (генерируется автоматически).

## База данных

### `carsharing_tariffs` — тарифы каршеринга

```sql
CREATE TABLE carsharing_tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,           -- yandex, citydrive, belka, delimobil
    tariff_name TEXT,
    hourly_rate REAL,
    km_rate REAL,
    min_time_minutes INTEGER DEFAULT 30,
    daily_limit_km INTEGER,
    insurance_included BOOLEAN DEFAULT 1,
    zone TEXT DEFAULT 'msk',
    notes TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, tariff_name, zone)
);
```

### `carsharing_trips` — поездки

```sql
CREATE TABLE carsharing_trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_start TEXT,
    date_end TEXT,
    car_model TEXT,
    car_plate TEXT,
    distance_km REAL,
    tariff TEXT,
    base_cost REAL,
    insurance REAL,
    over_minutes_cost REAL DEFAULT 0,
    discounts REAL DEFAULT 0,
    total REAL,
    source TEXT DEFAULT 'yandex_drive',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Расчёт стоимости поездки

```python
def calculate_drive_cost(tariff, hours, km):
    provider = tariff['provider']
    h_rate = tariff['hourly_rate'] or 0
    km_rate = tariff['km_rate'] or 0
    if provider == 'yandex':
        # Bay 24: flat 763₽/сутки + 13.5₽/км
        base = h_rate + km * km_rate
    else:
        # per-minute providers
        base = h_rate * hours + km * km_rate
    return max(round(base, -1), 500)
```

## Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `telegram_bot.py` | Основной бот (все команды, логика) |
| `bot/handlers/photos.py` | Telegram photo/tag/text handlers |
| `services/photo_pipeline.py` | Telegram-независимая логика photo/tag pipeline |
| `consumption.db` | SQLite-база данных |
| `.env` | Переменные окружения (пароли, токены) |
| `credit_monitor.py` | IMAP-мониторинг кредитных уведомлений |
| `credit_alerts.py` | Отправка кредитных алертов в Telegram |
| `email_importer.py` | Импорт чеков с почты |
| `scripts/fines_bot.py` | Мониторинг штрафов ГИБДД/парковок из писем Госуслуг |
| `purchase_dedup.py` | Дедупликация расходов между email и SMS, канонизация магазинов, учёт доставки |
| `memory_lane.py` | Memory Lane (топики, ассоциации слово→тема) |
| `gen_report.py` | Общий генератор PDF-отчёта |
| `init_db.py` | Общая инициализация и миграция БД |
| `consumption_agent_full_030526.py` | Legacy CLI entrypoint; постепенно чистится от дублей |
| `check_debts_fines.sh` | Heartbeat-скрипт (кредиты + штрафы) |
| `check_debts_fines_retry.sh` | Ежечасный cron 10-23, retry до успеха |
| `imap_folders.py` | **Day 1:** ScanMetrics + discover_target_mailboxes (INBOX + Spam + Receipts) |
| `ml_search_v2.py` | Visual product search v2 + Telegram-пагинация |
| `ml_providers.py` | WB API + 13 retailers + AliExpress/Alibaba (RU→EN перевод, гео-фильтр) |
| `ml_official_sites.py` | **Day 2:** resolver official sites / distributors / authorized retailers |
| `ml_watchlist.py` | **Day 5:** price-drop watchlist + cron-проверка цен |

## Поиск вещей: /items и /items_full

**Синтаксис:**
- `/items` — вещи, у которых скоро истекает срок замены (≤90 дней)
- `/items all` — все вещи
- `/items <текст>` — поиск по названию, бренду, категории, описанию, тегам, цвету, материалу
- `/items_full` — вещи со сроком замены ≤30 дней (с 🔴)
- `/items_full all` — все вещи с полной информацией и фото
- `/items_full <текст>` — полный поиск (как `/items`)

**SQL-запрос:** Оба используют LEFT JOIN с таблицей `categories` для получения русского названия категории (`category_name` на индексе 11). Поиск идёт по `search_text`, содержащему: name, brand, category_name, notes, attributes (description, style_tags, color, material).

**Важно:** `attributes` — это JSON-поле, хранится в колонке `attributes` таблицы `items`. В `cmd_items` раньше не было `attributes` в SELECT (ошибка выхода за границы индекса), исправлено в коммите `c12582a`.

## Расходы: /dayexp и /monthexp

**Источники данных:**
| Иконка | Источник | Описание |
|--------|----------|----------|
| 📧 | Почта | Чеки из Gmail, Yandex, Mail.ru (Ozon, Самокат, Яндекс Еда и др.) |
| 📱 | SMS | Расходы из SMS банков через Phone Link (2 телефона) |
| 🏦 | Выписка | PDF-выписки Сбербанка |
| 📝 | Ручной ввод | Добавленные вручную через бота |

**Поддерживаемые банки для SMS:**
- Сбербанк (900) — основной источник
- ВТБ — паттерны готовы
- Альфа-Банк — паттерны готовы
- Т-Банк (Тинькофф) — паттерны готовы
- Совкомбанк (Халва) — паттерны готовы

**Синтаксис:**
- `/dayexp` — расходы за сегодня (сканирование почт + SMS)
- `/dayexp 7` — расходы за последние 7 дней
- `/dayexp 30` — расходы за последние 30 дней
- `/monthexp` — расходы с 1-го числа текущего месяца по сегодня

**Фоновое сканирование:**
При вызове `/dayexp` и `/monthexp` запускается `daily_cheque_scan.py`, который:
1. Сканирует все 4 почтовых ящика на чеки
2. Сканирует SMS через Phone Link (2 телефона)
3. Запускает `sms_expense_monitor.py` для всех банков
4. Дедуплицирует записи через `purchase_dedup.py`
5. Добавляет новые в `purchases`

**Текущая логика дедупликации расходов:**
- email между папками режется по `Message-ID`
- email и SMS сравниваются по дате, каноническому названию магазина и времени операции
- алиасы продавца схлопываются, например `Умный ритейл` → `Самокат`
- если в email-чеке есть отдельная `доставка`, то повтором считается и SMS-списание на сумму `итог - доставка`
- мягкое удаление дублей делается через `deleted_at`, а отчёты `/dayexp` и `/monthexp` показывают только активные записи

**Таблица `purchases`:**
```sql
CREATE TABLE purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_date TEXT NOT NULL,
    total_amount REAL,
    store_name TEXT,
    source TEXT,        -- 'gmail', 'sms_sber', 'sber_statement', 'local'
    data_origin TEXT,   -- то же, что source
    notes TEXT,
    deleted_at TEXT
);
```

**Таблица `transfers` (переводы между счетами):**
```sql
CREATE TABLE transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_date TEXT NOT NULL,
    amount REAL NOT NULL,
    description TEXT,
    source TEXT DEFAULT 'sber_statement'
);
```

## Работа с ботом

**Перезапуск:**
```bash
systemctl --user restart consumption-bot.service
```

**Проверка статуса:**
```bash
systemctl --user status consumption-bot.service
```

**База:**
```bash
sqlite3 /home/yuri_artamonov/.openclaw/workspace/consumption_agent/consumption.db "SELECT ..."
```

## Актуальное состояние (11.05.2026)

| Провайдер | Поездок | Период |
|-----------|---------|--------|
| Яндекс Драйв | 26 | дек 2025 — май 2026 |
| Ситидрайв | 4 | апр 2026 |
| BelkaCar | 7 | фев-мар 2026 |
| Делимобиль | 2 | янв 2026 |
| **Всего** | **39** | |

**Тарифы в БД:** Яндекс Bay 24 (763₽/сут +13.5₽/км), Ситидрайв Стандарт (420₽/ч +14₽/км), BelkaCar Базовый (480₽/ч +15₽/км), Делимобиль Старт (420₽/ч +12₽/км).

## Пароли

Все credentials в `.env` (.gitignore, не попадает в git). Читаются через `os.getenv()`.
Переменные: `GMAIL_APP_PASSWORD`, `YANDEX_APP_PASSWORD`, `MAILRU_ZOREA_PASSWORD`, `MAILRU_NEUTRINON_PASSWORD`, `CONSUMPTION_BOT_TOKEN`.

## 🧾 Парсинг чеков Самокат (ОФД Платформа ОФД)

**Текущая проблема:** Самокат отправляет чеки через Платформу ОФД (`noreply@chek.pofd.ru`). Письма содержат только HTML с рекламой, а состав заказа подгружается асинхронно через JS.

**Известные форматы:**
- Отправитель: `noreply@chek.pofd.ru` (Платформа ОФД)
- Тема: `Чек и подарок. ООО УМНЫЙ РИТЕЙЛ, X XXX ₽`
- Сумма указана в теме письма — можно вытащить
- В HTML есть ссылка: `https://lk.platformaofd.ru/web/noauth/cheque?fn={FN}&fp={FP}&i={I}`
- Состав заказа на странице ОФД подгружается через API с CSRF-токеном

**Статус:** ❌ Автоматический парсинг пока не работает. Данные загружаются через WebSocket/JS.
**План:** Нужно решить — или использовать Browser Automation (Playwright) для рендеринга страницы ОФД, или найти партнёрский API.


## 🧾 Парсинг чеков Самокат (ОФД Платформа ОФД)

**Отправитель:** `noreply@chek.pofd.ru` (Платформа ОФД)
**Тема:** `Чек и подарок. ООО УМНЫЙ РИТЕЙЛ, X XXX ₽`

**Метод парсинга:**
1. Извлечь HTML из письма
2. Найти таблицу через BeautifulSoup, содержащую и `«КАССОВЫЙ ЧЕК»` и `«ИТОГ»`
3. Из её plain-text разобрать построчно:
   - `N: Название товара, вес` — название (всё после `N: ` до конца строки)
   - `количество` — цифра перед `шт.`
   - `x`
   - `цена` — число после `x`
   - `Общая стоимость позиции с учетом скидок и наценок` + `сумма`
4. ИТОГ — контрольная сумма

**Функция:** `_parse_samokat_items(html)` в `consumption_agent_full_030526.py`

**Источник данных:** фискальный чек (ФФД 1.2), все цены в рублях, формат цен: `XXX.XX`
