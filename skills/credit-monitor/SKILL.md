---
name: credit-monitor
description: >
  Credit Monitor — мониторинг кредитных задолженностей и платежей.
  Используй когда нужно: (1) работать с таблицей credit_alerts в consumption.db,
  (2) проверять/настраивать IMAP-мониторинг почт на письма от банков и МФО,
  (3) управлять подтверждением оплаты через Telegram-кнопки,
  (4) анализировать историю кредитных уведомлений, (5) настраивать cron-расписание.
---

# Credit Monitor

## Overview

Мониторинг кредитных платежей через проверку почты (Gmail, Яндекс, Mail.ru ×2) и SMS через Windows Phone Link. Отправляет предупреждения в Telegram за 3+ дня до платежа.

**Расписание:** 10:00 и 18:00 через cron.
**Триггеры:** минимум за 3 дня до платежа.

## База данных

### `credit_alerts`

```sql
CREATE TABLE credit_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,         -- 'email' или 'sms'
    sender TEXT,                  -- email отправителя
    sender_name TEXT,             -- sberbank, tinkoff, turbozaim, vtb и т.д.
    subject TEXT,                 -- тема письма
    body TEXT,                    -- тело письма/SMS
    payment_date TEXT,            -- дата платежа
    payment_amount REAL,          -- сумма
    currency TEXT DEFAULT 'RUB',
    detected_at TEXT DEFAULT (datetime('now')),
    notified_at TEXT,             -- когда уведомили
    days_until_payment INTEGER,   -- дней до платежа
    raw_message_id TEXT UNIQUE,
    is_active INTEGER DEFAULT 1,
    paid_confirmed_at TEXT,       -- дата подтверждения оплаты
    paid_confirmed_via TEXT,      -- 'telegram_button' и т.д.
    paid_note TEXT
);
```

## Банки и МФО

### Определяемые банки
| ID | Название | Триггеры |
|----|----------|----------|
| `sberbank` | Сбербанк | сбербанк, sberbank, сбер |
| `sovcombank` | Совкомбанк | совкомбанк, совком |
| `vtb` | ВТБ | втб, vtb |
| `tinkoff` | Тинькофф | тинькофф, tinkoff, т-банк, t-bank |
| `alfa` | Альфа-Банк | альфа-банк, альфабанк, alfabank |

### Определяемые МФО
| ID | Название | Триггеры |
|----|----------|----------|
| `joy_finance` | Joy Finance | joy finance, джой финанс |
| `turbozaim` | Turbozaim | turbozaim, турбозайм |
| `nebus` | Nebus Finance | nebus finance, небус |
| `boostra` | Boostra | boostra, бустра |
| `ekvazaim` | Эквазайм | эквазайм, ekvazaim |
| `webzaim` | Webzaim | webzaim, вебзайм |

## Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `credit_monitor.py` | IMAP-мониторинг 4 почтовых ящиков + извлечение данных платежей |
| `credit_alerts.py` | Отправка алертов в Telegram через бота |
| `sms_monitor.py` | Мониторинг SMS через Windows Phone Link |
| `check_credit_alerts.sh` | Shell-скрипт для запуска по cron |
| `telegram_bot.py` | Обработчик кнопки `credit_paid` для подтверждения оплаты |

## Telegram-интеграция

Бот отправляет алерт с кнопкой `✅ Подтвердить оплату`. Кнопка вызывает `credit_paid_callback`:

```python
# Callback handler в telegram_bot.py
async def credit_paid_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Проверка OWNER_CHAT_ID
    # Обновление paid_confirmed_at в БД
    # Редактирование сообщения c отметкой ✅ Отмечено как оплачено

app.add_handler(CallbackQueryHandler(credit_paid_callback, pattern=r'^credit_paid:\d+$'))
```

## Cron-расписание

```cron
0 10 * * * cd /home/yuri_artamonov/.openclaw/workspace/consumption_agent && ./check_credit_alerts.sh >> /tmp/credit_alerts.log 2>&1
0 18 * * * cd /home/yuri_artamonov/.openclaw/workspace/consumption_agent && ./check_credit_alerts.sh >> /tmp/credit_alerts.log 2>&1
```

## IMAP-конфигурация

```python
IMAP_CONFIGS = [
    {'host': 'imap.gmail.com',  'user': 'yu.v.artamonov@gmail.com',       'password': os.getenv('GMAIL_APP_PASSWORD')},
    {'host': 'imap.yandex.ru',  'user': 'HKID2021@yandex.ru',             'password': os.getenv('YANDEX_APP_PASSWORD')},
    {'host': 'imap.mail.ru',    'user': 'zorea2001@mail.ru',               'password': os.getenv('MAILRU_ZOREA_PASSWORD')},
    {'host': 'imap.mail.ru',    'user': 'neutrinon@mail.ru',               'password': os.getenv('MAILRU_NEUTRINON_PASSWORD')},
]
```

## Извлечение данных из писем

Паттерны для поиска даты платежа:
- `"ближайший платёж 15.05.2026"` / `"до 15.05.2026"`
- `"оплатите до 15 мая"` / `"до 15 мая 2026 г."`
- `"15.05.2026 спишется"`
- `"дата платежа: 15.05.2026"`
- `"следующий платёж 15 мая"`

Паттерны для суммы:
- `"Сумма платежа: 1 500,00 руб"`
- `"к оплате 23687.00"`
- Ключевые слова: списание, погашение, задолженность, долг, кредит, займ

## Актуальное состояние (11.05.2026)

- **Всего алертов в БД:** 29
- **Активных:** 28
- **Подтверждено оплаченных:** 1
- **Определённые банки:** turbozaim, sberbank, vtb (остальные — 'unknown' — требуют донастройки паттернов)
- **Проблема:** большинство писем не определяют sender_name (попадает 'unknown')

## Порядок работы

1. **Добавить новый источник:** дополнить `IMAP_CONFIGS` или `BANK_PATTERNS`/`MFO_PATTERNS`
2. **Запустить проверку:** `./check_credit_alerts.sh`
3. **Подтвердить оплату:** нажать кнопку в Telegram
4. **Проверить статус:** `/alerts` в боте (покажет активные алерты)

## Переменные окружения (.env)

```
GMAIL_APP_PASSWORD=...
YANDEX_APP_PASSWORD=...
MAILRU_ZOREA_PASSWORD=...
MAILRU_NEUTRINON_PASSWORD=...
CONSUMPTION_BOT_TOKEN=...
```
