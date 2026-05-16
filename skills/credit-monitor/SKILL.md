---
name: credit-monitor
description: >
  Credit Monitor — мониторинг кредитных задолженностей и платежей.
  Используй когда нужно: (1) работать с таблицей credit_alerts в consumption.db,
  (2) проверять/настраивать IMAP-мониторинг почт на письма от банков и МФО во всех релевантных папках (`INBOX`, `Spam/Junk`, папки чеков/Receipts),
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
| `dengi_srazu` | Деньги Сразу | деньги сразу, dengisrazu, dengi-srazu |
| `flashzaim` | ФлэшЗайм | флэшзайм, flashzaim, flash-zaim |
| `fast_finance` | Фаст Финанс | фаст финанс, fast finance, fastfinance |
| `glatsint` | Глацинт | глацинт, glatsint |

## Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `credit_monitor.py` | IMAP-мониторинг 4 почтовых ящиков + извлечение данных платежей |
| `credit_alerts.py` | Отправка алертов в Telegram через бота |
| `sms_monitor.py` | Мониторинг SMS через Windows Phone Link |
| `check_credit_alerts.sh` | Shell-скрипт для запуска по cron |
| `telegram_bot.py` | Обработчик кнопки `credit_paid` для подтверждения оплаты |

## Telegram-интеграция

### Команды бота
| Команда | Описание |
|---------|----------|
| `/debts` | Кредиты к оплате в ближайшие 30 дней (цветовая маркировка) |
| `/fines` | Неоплаченные штрафы |

`/debts` выводит таблицу кредитов с группировкой:
- 🔴 ПРОСРОЧЕН
- 🟡 СРОЧНО (≤3 дня)
- 🟢 На этой неделе (≤7 дней)
- ⚪ Остальные

`/fines` выводит активные штрафы (type=new), без подтверждённых оплаченных.

### Кнопки
Бот отправляет алерт с кнопкой:
- `✅ Подтвердить оплату` — кредиты (callback: `credit_paid:{id}`)
- `✅ Оплачено` — штрафы (callback: `fine_paid:{id}`)

## Cron-расписание

```cron
# Каждый час с 10 до 23, retry до успеха (флаг /tmp/debts_fines_done_YYYY-MM-DD)
0 10-23 * * * cd /home/yuri_artamonov/.openclaw/workspace/consumption_agent && bash check_debts_fines_retry.sh
```

Скрипт `check_debts_fines_retry.sh`:
1. Проверяет флаг — был ли сегодня успешный прогон
2. Если да — выход (не тратим время)
3. Если нет — запускает `credit_alerts.py` (кредиты) + `scripts/fines_bot.py` (штрафы)
4. Если хотя бы один источник ответил — ставит флаг

## Heartbeat (OpenClaw)

Каждый heartbeat запускается `check_debts_fines.sh` (аналог retry, но без флага).
Сообщать о новых кредитах/штрафах сразу.

## IMAP-конфигурация

```python
IMAP_CONFIGS = [
    {'host': 'imap.gmail.com',  'user': 'yu.v.artamonov@gmail.com',       'password': os.getenv('GMAIL_APP_PASSWORD')},
    {'host': 'imap.yandex.ru',  'user': 'HKID2021@yandex.ru',             'password': os.getenv('YANDEX_APP_PASSWORD')},
    {'host': 'imap.mail.ru',    'user': 'zorea2001@mail.ru',               'password': os.getenv('MAILRU_ZOREA_PASSWORD')},
    {'host': 'imap.mail.ru',    'user': 'neutrinon@mail.ru',               'password': os.getenv('MAILRU_NEUTRINON_PASSWORD')},
]
```

### Охват папок

`credit_monitor.py` и `scripts/fines_bot.py` больше не ограничены `INBOX`. Через `consumption_agent/imap_folders.py` они обходят:
- `INBOX`
- `Spam` / `Junk` / `Спам`
- папки чеков/квитанций вроде `Receipts`, `Checks`, `чеки`

Это важно для банковских уведомлений и штрафов, которые почтовые провайдеры иногда складывают в spam или auto-sorted папки.

Дедупликация между папками идёт по `Message-ID`, поэтому одно письмо не должно порождать несколько записей.

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

## Фильтрация кредитных SMS

Скрипт `scripts/cleanup_alerts.py` содержит полный пайплайн классификации SMS.

### Архитектура классификации

```
SMS → detect_sender_name() → classify_alert() → Category
                              ↓
                    ┌─── is_sms_category() (для известных банков)
                    │    └── CREDIT_REMINDER_PATTERNS → credit
                    │    └── CODE_PATTERNS → ad
                    │    └── BANK_NOTIFICATION_PATTERNS → ad
                    │    └── CREDIT_APPLICATION_PATTERNS → ad
                    │    └── SUBSCRIPTION_PATTERNS → ad
                    │    └── fallback → ad (всё остальное от банка — не кредит)
                    │
                    ├── SPAM_MFO_SENDERS → ad
                    ├── SPAM_MFO_PATTERNS → ad
                    ├── телефонные номера (+7XXX, 0XXX) → ad
                    └── всё остальное → unknown
```

### Приоритет проверок (classify_alert)

1. **A. body с признаками спам-МФО** → `_detect_sender()` проверяет `NOT_BANK_FROM` (IsWis.Ru, Kapytal.Ru...) по содержимому SMS
2. **B. Общие рекламные паттерны** → `AD_SUBJECT_PATTERNS` (акции, кешбэк, рассрочки, поздравления, чеки, штрафы)
3. **C. Сумма** → `< 100₽ = чек, > 5 млн = реклама`
4. **D. SMS-специфичная логика** → `is_sms_category()` (требует sender в CREDIT_SENDERS):
   - коды 2FA → ad
   - напоминание о платеже → credit
   - обычные банковские оповещения → ad
   - заявки на кредит (не напоминания) → ad
   - подписки → ad
   - дата + сумма > 1000 → credit
   - всё остальное → ad
5. **E. Неизвестный отправитель** → если в `SPAM_MFO_SENDERS` или `SPAM_MFO_PATTERNS` → ad
6. **F. Телефонные номера** → `+7XXX` / `0XXX` → ad
7. **G. Известные банки/МФО** с напоминанием → credit, иначе unknown
8. **H. Спам-паттерны повторно** → ad

### Определение отправителя (detect_sender_name)

В `scripts/scan_sms_3mo.py`:
1. `SHORT_CODE_BANKS`: `900` → sberbank
2. `BANK_SMS_SENDERS`: `vtb`, `alfa-bank`, `t-bank` → по from_address
3. `SPOOF_SENDERS`: если отправитель среди спам-МФО → `spam_mfo`
4. `BODY_SENDER_PATTERNS`: поиск в тексте SMS (сбер, альфа, втб, совком, boostra...)

### Белые списки отправителей

**CREDIT_SENDERS** — от кого ждём настоящие кредитные уведомления:
```python
CREDIT_SENDERS = {
    'sberbank', 'vtb', 'tinkoff', 'alfa',
    'sovcombank', 'raiffeisen', 'gazprombank', 'otkritie',
    'rosbank', 'uralsib', 'homecredit', 'rencredit',
    'pochtabank', 'akbars', 'absolut', 'mdm',
    'turbozaim', 'joy_finance', 'nebus',
    'ekvazaim', 'webzaim',
}
```

**SPAM_MFO_SENDERS** — от кого всегда реклама (проверяется до CREDIT_SENDERS):
```python
SPAM_MFO_SENDERS = {
    'iswis', 'kapytal', 'c-m0ney', 'speedcrru', 'l0anpayru',
    'hotloan', 'bistroz', 'iamzaem', 'zaymer', 'vivus',
    'my-cred', 'fingis', 'banki.ru', 'atb', 'bankzenit',
    'gazprombank', 'uralsib', 'boostra', '0919',
    'beeline', 't-mob', 'rsb.ru', 'unknown',
    # ... и ещё 30+ отправителей
}
```

### Паттерны реальных кредитных напоминаний

Располагаются по приоритету в `CREDIT_REMINDER_PATTERNS`:

| # | Паттерн | Пример | Банк |
|---|---------|--------|------|
| 1 | `не\s+забудьте\s+внести` | «Не забудьте внести 613.19 RUR по кредитке» | Alfa |
| 2 | `внесите(?:\s+очередн[уы]ю)?\s+оплат` | «Внесите очередную оплату по займу» | Turbozaim |
| 3 | `дата\s+платежа[!.]` | «Сегодня дата платежа! К оплате: 0.00 руб» | JoyMoney |
| 4 | `к\s+оплате[!.:]` | «К оплате: 0.00 руб» | JoyMoney |
| 5 | `внесите\s+плат[её]ж` | «Внесите платеж по кредитке 11 400 руб» | VTB |
| 6 | `спишем\s+\d+.*не\s+забудьте` | «27.04.2026 спишем 32500. Не забудьте» | Alfa |
| 7 | `плат[её]ж\s+по\s+займ` | «15.04.2026 — платеж по займу» | Alfa Finance |
| 8 | `плат[её]ж\s+по\s+кредитке` | «платеж по кредитке» | VTB |
| 9 | `очередн[оа]го\s+платеж[аа]` | «внесения очередного платежа» | sber |
| 10 | `не\s+допустить\s+просрочку` | «не допустить просрочку» | sber |

### Паттерны обычных банковских уведомлений (НЕ кредит)

`BANK_NOTIFICATION_PATTERNS` — покупки, переводы, баланс:
```
счёт карты, счёт\d{4}, покупк, перевод, по СБП,
списание, зачисление, оплата, баланс, недостаточно средств,
комиссия, отклонён, заблокировали перевод, приостановил,
защита клиентов, мошенничество, не дозвонились,
пополнен на, счет *X пополнен, получите до X без %,
пришлем X посоветуйте, Доступно 39000₽, на карту без проверок
```

### Паттерны спам-МФО

`SPAM_MFO_PATTERNS` — рекламные займы, не напоминания:
```
готовы перевести, одобрен на карту, заберите,
получите до Х, выдача/займ подтвержден, беспроцентн,
на любые цели, деньги на карту, мгновенно на карту,
источник денег, получите деньги, успейте взять,
ваш займ готов, оформление получить, займы на разные цели,
попробуйте кредитную карту, cc./clk./bee./beel.ink
```

### Паттерны 2FA/кодов

`CODE_PATTERNS` — всегда приоритетнее любых кредитных проверок:
```
код: \d{4,6}, код для входа, никому не сообщай,
введите код, проверочный код, для подтверждения,
code: \d{4,8}, @id.sber
```

### Сканирование SMS из Phone Link

Скрипт `scripts/scan_sms_3mo.py`:

```bash
# Запуск по умолчанию (старый профиль, 90 дней)
python3 scripts/scan_sms_3mo.py

# Смена профиля — изменить WINDOWS_PHONE_LINK_DB в начале файла
WINDOWS_PHONE_LINK_DB = '/path/to/phone.db'

# Копирование БД из NTFS (Windows Phone Link блокирует прямой SQLite-доступ)
mkdir -p /tmp/phone_backup
cp /mnt/c/.../phone.db /tmp/phone_backup/
cp /mnt/c/.../phone.db-wal /tmp/phone_backup/
python3 scripts/scan_sms_3mo.py # или с изменённым WINDOWS_PHONE_LINK_DB
```

### Добавление нового отправителя

1. **Реальный банк/МФО с напоминаниями** → добавить в `CREDIT_SENDERS`
2. **Спам-МФО** → добавить в `SPAM_MFO_SENDERS` (проверяется раньше)
3. **Спам-МФО, маскирующийся под банк** → добавить в `NOT_BANK_FROM` и `SPOOF_SENDERS`
4. **Новый паттерн напоминания** → добавить в `CREDIT_REMINDER_PATTERNS`
5. **Новый паттерн обычного уведомления** → добавить в `BANK_NOTIFICATION_PATTERNS`

### Валидация: проверка спама на пропущенные кредиты

После каждого сканирования необходимо убедиться, что в спам/рекламу не попали
настоящие кредитные напоминания.

**Команда для проверки 200 последних SMS из каждого профиля:**
```bash
cd ~/.openclaw/workspace/consumption_agent/scripts
python3 << 'PYEOF'
import sys, sqlite3
sys.path.insert(0, '.')
from cleanup_alerts import classify_alert, Category
from scan_sms_3mo import detect_sender_name

db = sqlite3.connect('/tmp/phone_link_backup/phone_new.db')
db.row_factory = sqlite3.Row

for label, where in [("НОВЫЙ", "message_id >= 12040"), ("СТАРЫЙ", "message_id < 12040")]:
    msgs = db.execute(
        f"SELECT message_id, from_address, body FROM message WHERE {where}"
        " ORDER BY message_id DESC LIMIT 200"
    ).fetchall()
    
    credits = []
    for msg in msgs:
        sn = detect_sender_name(msg['from_address'], msg['body'])
        cat = classify_alert(msg['message_id'], 'sms', sn, '', msg['body'], None)
        if cat == Category.REAL_CREDIT:
            credits.append((msg['message_id'], msg['from_address'], sn, msg['body'][:70]))
    
    print(f"[{label}] {len(msgs)} SMS: {len(credits)} кредитов, {len(msgs)-len(credits)} спам")
    for c in credits:
        print(f"  🟢 #{c[0]} | {c[1]:15s} → {c[2]:10s} | {c[3]}")
    print()

db.close()
PYEOF
```

**Проверять:**
- После добавления нового паттерна в `CREDIT_REMINDER_PATTERNS`
- После добавления нового отправителя в `CREDIT_SENDERS`
- Периодически (раз в месяц) — чтобы не накопились пропущенные
- При жалобе пользователя на пропущенный алерт

**Ожидаемый результат:** 0 кредитов в AD-выборке.

### Запись в БД

После сканирования, реальные кредитные напоминания записываются в `credit_alerts`:
```python
c.execute('''INSERT INTO credit_alerts 
    (id, source, sender_name, subject, body, payment_amount, 
     payment_date, is_active, detected_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1, datetime("now"))''',
    (new_id, 'sms', bank_name, '', body, amount, date_str))
```

Проверка на дубликаты — по `sender_name + body`.

### Актуальное состояние (11.05.2026)

- **Всего алертов в БД:** 97
- **Активных:** 9 (2 email + 7 SMS)
- **SMS-источников:** 2 телефона (старый: 319 SMS, новый: 521 SMS)
- **Точность классификации:** 840 SMS → 8 credit (0 false positive), 412 ad, 0 unknown
- **Определённые банки:** turbozaim, sberbank, vtb, alfa (остальные — 'unknown' — требуют донастройки паттернов)

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
