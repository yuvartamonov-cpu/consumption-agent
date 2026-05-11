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

## Команды бота

| Команда | Описание |
|---------|----------|
| `/list` | Инвентарь по категориям |
| `/alerts` | Алерты (гарантии, сроки) |
| `/find_car 3ч 80км` | Подбор тарифа каршеринга |
| `/last_drives [N] [provider]` | Последние поездки (фильтр: yandex_drive, citydrive, belka, delimobil) |
| `/warranties` | Отчёт по гарантиям |
| `/add <name> [price] [category]` | Добавить товар |
| `/add_photo` | Фото чека (OCR) |
| `/check` | Статистика |
| `/help` | Список команд |

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
| `consumption.db` | SQLite-база данных |
| `.env` | Переменные окружения (пароли, токены) |
| `credit_monitor.py` | IMAP-мониторинг кредитных уведомлений |
| `credit_alerts.py` | Отправка алертов в Telegram |
| `email_importer.py` | Импорт чеков с почты |

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
