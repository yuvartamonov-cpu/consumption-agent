---
name: monthexp
description: "Команда /monthexp — расходы с 1 числа текущего месяца по сегодня с принудительным сканированием всех 4 почт (Gmail, Yandex, Mail.ru Zorea, Mail.ru Neutrinon), релевантных IMAP-папок (`INBOX`, `Spam/Junk`, папки чеков/`Receipts`) и SMS с двух телефонов (Phone Link Z Fold 3 + Z Fold 4). Используй когда пользователь хочет: (1) посмотреть расходы за месяц, (2) получить сводку с группировкой по дням и магазинам, (3) увидеть общие траты с начала месяца."
---

# monthexp — Расходы с 1 числа месяца

## Использование

```bash
python3 <skill-dir>/scripts/monthexp_report.py
```

Скрипт:
1. Определяет `month_start = today.strftime('%Y-%m-01')` — всегда 1 число текущего месяца
2. Запускает `daily_cheque_scan.py` — сканирование всех почт + SMS за сегодня
3. Читает из БД `consumption.db` все записи с `month_start` по сегодня
4. Группирует по дням в обратном порядке (сначала сегодня)
5. Выводит отчёт в формате:
   - Заголовок: «📊 Расходы с 1 мая по 13 число»
   - Общее количество покупок и сумма
   - Каждый день: «📅 *ДД.ММ* — сумма (N покупок)»
   - Каждая покупка: иконка, магазин, сумма, описание
   - Блок «📌 Всего по магазинам:»

## Команда Telegram

Команда `/monthexp` в `telegram_bot.py`:
- Отвечает «🔍 Сканирую почты и SMS — собираю данные за месяц...»
- Асинхронно запускает `daily_cheque_scan.py`
- После сканирования **редактирует** сообщение с результатом
- Использует `asyncio.create_subprocess_exec` (не блокирует бота)

## Структура скила

```
monthexp/
├── SKILL.md
├── scripts/
│   ├── monthexp_report.py     # Отчёт за месяц с группировкой по дням
│   └── example.py             # (заглушка)
└── references/
    └── api_reference.md        # (заглушка)
```

## Зависимости

- `consumption_agent/consumption.db` — SQLite БД
- `consumption_agent/daily_cheque_scan.py` — скрипт сканирования почт и SMS
- `consumption_agent/imap_folders.py` — выбор релевантных IMAP-папок (`INBOX`, `Spam/Junk`, папки чеков)
- Таблица `purchases` с полями: `purchase_date`, `total_amount`, `store_name`, `source`, `notes`, `deleted_at`

## IMAP-охват

`/monthexp` использует тот же `daily_cheque_scan.py`, что и `/dayexp`, поэтому охватывает:
- `INBOX`
- `Spam` / `Junk` / `Спам`
- папки чеков вроде `Receipts`, `Checks`, `чеки`

Дубликаты между папками режутся по `Message-ID`.
