---
name: dayexp
description: "Команда /dayexp — расходы за сегодня с принудительным сканированием всех 4 почт (Gmail, Yandex, Mail.ru Zorea, Mail.ru Neutrinon) и SMS с двух телефонов (Phone Link Z Fold 3 + Z Fold 4). Используй когда пользователь хочет: (1) посмотреть расходы за сегодняшний день, (2) запустить принудительное сканирование чеков из почт и SMS, (3) получить сводку расходов с группировкой по магазинам."
---

# dayexp — Расходы за сегодня

## Использование

```bash
python3 <skill-dir>/scripts/dayexp_report.py        # только сегодня
python3 <skill-dir>/scripts/dayexp_report.py -n 7    # последние 7 дней
```

Скрипт:
1. Запускает `daily_cheque_scan.py` из `consumption_agent/` — сканирование всех почт + SMS
2. Читает из БД `consumption.db` все записи за N дней (включая сегодня)
3. Выводит отчёт в формате:
   - Заголовок: «📊 Расходы за сегодня» или «📊 Расходы за последние N дн.»
   - Количество покупок и общая сумма
   - Каждая покупка: иконка источника, магазин, сумма, описание
   - Блок «📌 По магазинам:» — итоги по каждому магазину

## Команда Telegram

Команда `/dayexp [N]` в `telegram_bot.py`:
- `/dayexp` — расходы за сегодня (по умолчанию N=1)
- `/dayexp 7` — расходы за последние 7 дней
- Отвечает «🔍 Сканирую почты и SMS за последние N дн....»
- Асинхронно запускает `daily_cheque_scan.py`
- После сканирования **редактирует** сообщение с результатом
- Использует `asyncio.create_subprocess_exec` (не блокирует бота)

## Структура скила

```
dayexp/
├── SKILL.md
├── scripts/
│   ├── dayexp_report.py       # Отчёт за сегодня
│   └── example.py             # (заглушка)
└── references/
    └── api_reference.md        # (заглушка)
```

## Зависимости

- `consumption_agent/consumption.db` — SQLite БД
- `consumption_agent/daily_cheque_scan.py` — скрипт сканирования почт и SMS
- Таблица `purchases` с полями: `purchase_date`, `total_amount`, `store_name`, `source`, `notes`, `deleted_at`
