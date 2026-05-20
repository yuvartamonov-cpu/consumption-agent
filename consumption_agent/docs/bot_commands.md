# @ConsumptionAgentBot — Подробные инструкции по командам

> Версия: 2026-05-17 · после 5-дневного спринта (13–17 мая 2026)
> Бот: [@ConsumptionAgentBot](https://t.me/ConsumptionAgentBot)
> Текущий коммит: `ae3c0c2`

---

## Содержание

1. [Базовые команды](#1-базовые-команды)
2. [Инвентарь и вещи](#2-инвентарь-и-вещи)
3. [Расходы и отчёты](#3-расходы-и-отчёты)
4. [Долги и штрафы](#4-долги-и-штрафы)
5. [Гарантии и сроки](#5-гарантии-и-сроки)
6. [Каршеринг](#6-каршеринг)
7. [Memory Lane (визуальные впечатления)](#7-memory-lane-визуальные-впечатления)
8. [Visual Product Search v2](#8-visual-product-search-v2)
9. [Watchlist цен](#9-watchlist-цен) ✨ Day 5
10. [Темы и правила](#10-темы-и-правила)
11. [Фото-сценарии](#11-фото-сценарии)
12. [Системные команды](#12-системные-команды)

---

## 1. Базовые команды

### `/start`

Приветственное сообщение. Безопасно вызвать в любой момент.

### `/help`

Полный список команд с краткими описаниями.

---

## 2. Инвентарь и вещи

### `/list`

Сводка инвентаря по категориям. Группирует все активные `items` по `category` и показывает количество и общую стоимость.

**Пример вывода:**
```
📦 Инвентарь:
👕 Одежда: 47 шт · 184 500 ₽
💻 Электроника: 12 шт · 320 000 ₽
🪑 Мебель: 8 шт · 92 000 ₽
```

### `/items [all|<категория>|<текст>]`

- `/items` — вещи со сроком замены ≤90 дней (🟡 средний приоритет)
- `/items all` — все вещи в инвентаре
- `/items электроника` — фильтр по категории
- `/items adidas` — полнотекстовый поиск (name, brand, description, style_tags, color, material)

Поиск идёт по объединённому полю `search_text` с использованием LEFT JOIN на `categories`.

### `/items_full [all|<категория>|<текст>]`

То же что `/items`, но с полной информацией:
- Фото товара (если есть `media_asset_id`)
- Бренд + модель + артикул
- Цена + дата покупки + чек
- Атрибуты (color, material, style)
- Срок замены/гарантии

По умолчанию показывает вещи со сроком замены ≤30 дней (🔴 высокий приоритет).

### `/add <name> [price] [category]`

Добавить товар вручную.

```
/add Толстовка Adidas Originals 4500 одежда
/add Молоко 90 еда
/add Книга по Python
```

Если price/category не указаны — сохраняется без них.

### `/add_tag`

Отправьте боту фото чека с подписью или без — OCR (Tesseract) распознает позиции и сумму. При неудаче OCR можно вписать сумму вручную в подпись.

### `/add_item`

Интерактивный мастер добавления товара (multi-step dialog).

---

## 3. Расходы и отчёты

### `/dayexp [N]` ✨ улучшено в Day 1

Расходы за N дней (по умолчанию N=1 — сегодня).

**Что делает:**
1. Запускает `daily_cheque_scan.py` — асинхронно сканирует все 4 IMAP-аккаунта (Gmail, Yandex, Mail.ru Zorea, Mail.ru Neutrinon) + Phone Link SMS.
2. По каждому аккаунту обходит **3 типа папок**: INBOX, Spam/Junk/Спам, Receipts/Чеки.
3. Дедуплицирует по Message-ID между папками, по дате+магазину+времени между email/SMS.
4. Парсит чеки ОФД (Платформа ОФД, Яндекс Чеки), SMS банков (Сбер 900, ВТБ, Альфа, Т-Банк, Совкомбанк).
5. Добавляет новые в `purchases`, отбрасывает дубли через `purchase_dedup.py`.
6. Логирует ScanMetrics: `[SCAN] scanner=daily_cheque_scan account=Gmail folders=3 msgs_seen=12 deduped=2 parsed=8 elapsed=1.4s`.

**Пример:**
- `/dayexp` — расходы за сегодня
- `/dayexp 7` — за неделю
- `/dayexp 30` — за месяц

### `/monthexp` ✨ улучшено в Day 1

Расходы с 1-го числа текущего месяца до сегодня. Группировка по дням + сводка по магазинам. Использует тот же сканер, что `/dayexp`.

**Пример вывода:**
```
📊 Май 2026 (1–17):
─ 12 мая · 2 покупки · 1 200 ₽
   📧 Самокат: 800 ₽
   📱 Пятёрочка: 400 ₽
─ 15 мая · 1 покупка · 5 500 ₽
   🏦 М.Видео: 5 500 ₽
...
📌 По магазинам:
Самокат · 12 покупок · 14 800 ₽
Пятёрочка · 8 покупок · 7 200 ₽
```

### `/check`

Базовая статистика по `purchases`: количество за последние 7/30 дней, средний чек, топ-5 магазинов.

### `/parse`

Запуск ручного парсинга последнего фото/файла из чата.

---

## 4. Долги и штрафы

### `/debts` ✨ улучшено в Day 1

Кредитные платежи к оплате в ближайшие 30 дней.

**Что сканирует:**
- IMAP всех 4 почт во всех релевантных папках (INBOX + Spam + Receipts).
- SMS банков через Phone Link.
- БД таблицы `credit_alerts` — сохранённые алерты.

**Пример вывода:**
```
💰 Кредитные платежи:
─ Альфа-Банк · 20 мая · 12 300 ₽ · ✅ Подтвердить
─ Т-Банк · 25 мая · 5 800 ₽ · ✅ Подтвердить
```

Кнопка «✅ Подтвердить» помечает алерт как оплаченный (callback `credit_paid:<id>`).

### `/fines` ✨ улучшено в Day 1

Неоплаченные штрафы (ГИБДД, парковки, МСД, платные дороги).

**Источники:** письма Госуслуг, Автодора, ЦАФАП. Скан 4 почт через `scripts/fines_bot.py` (с ScanMetrics).

Каждый штраф: дата, статья, сумма, источник, кнопка «✅ Оплачено» (callback `fine_paid:<id>`).

---

## 5. Гарантии и сроки

### `/warranties`

Все вещи с активной гарантией (`warranty_until > now`). Сортировка по дате истечения. Цветовая разметка:
- 🔴 До 7 дней
- 🟡 До 30 дней
- 🟢 Более 30 дней

### `/set_warranty <item_id> <YYYY-MM-DD>`

Установить дату окончания гарантии для существующего товара.

```
/set_warranty 142 2027-12-31
```

### `/alerts`

Сводка активных алертов: истекающие гарантии (≤30 дней), просроченные кредитные платежи, штрафы.

---

## 6. Каршеринг

### `/find_car <время> <км>`

Подбор самого выгодного тарифа каршеринга для заданной поездки.

```
/find_car 3ч 80км
/find_car 30мин 15км
```

Перебирает все тарифы в `carsharing_tariffs` (Яндекс Драйв, Ситидрайв, BelkaCar, Делимобиль) и считает стоимость по `calculate_drive_cost()`.

**Пример вывода:**
```
🚗 3 часа · 80 км
🥇 Яндекс Bay 24: 763₽ суточный + 1 080₽ км = 1 843₽
🥈 Делимобиль Старт: 1 260₽ + 960₽ = 2 220₽
🥉 BelkaCar Базовый: 1 440₽ + 1 200₽ = 2 640₽
```

### `/last_drives [N] [provider]`

Последние N поездок (по умолчанию 10). Можно фильтровать по провайдеру.

```
/last_drives
/last_drives 20
/last_drives 5 yandex_drive
/last_drives 10 citydrive
```

---

## 7. Memory Lane (визуальные впечатления)

### `/ml_last [N]`

Последние N записей из `memory_lane_items` (по умолчанию 10). Показывает caption, topic, фото.

```
/ml_last
/ml_last 5
```

### Фото с подписью «нравится» / «запомни» / «найди похожее»

Бот сохраняет фото в `data/media/` (sha256-дедуп), извлекает теги, тему, через Vision API распознаёт название/бренд/описание, кладёт в `memory_lane_items` и `media_assets`.

Прямо в результате обработки появляются кнопки:
- 🔍 **Искать** → запускает `/ml_search`
- 🗑 **Удалить** → удаляет запись
- 🔔 **Напомнить** → ставит reminder

---

## 8. Visual Product Search v2 ✨ Day 2, 3, 4

### `/ml_search <id>`

Запуск визуального поиска для item из Memory Lane. 10-этапный pipeline (attribute extraction → query expansion → federated search → canonical groups → anomaly detection → inventory collision → taste re-ranking).

**Что нового после спринта:**

- **Day 2:** Бренд распознан? → ссылки на официальный сайт + дистрибьюторы + авторизованные ритейлеры (`ml_official_sites.py`, 25+ брендов).
- **Day 3:** Иностранные источники только в РФ/KZ/BY (геолокация). Запросы к AliExpress/Alibaba автоматически переводятся (200+ слов + стемминг).
- **Day 4:** Если результат не помещается в 4096 символов или больше 5 товаров — внизу кнопка «📄 Продолжить вывод (N ещё)». При нажатии — следующая страница со сквозной нумерацией.
- **Day 5:** Если у топ-3 товаров есть цена — внизу кнопка «🔔 Следить за ценой (топ-3)».

**Пример вывода:**
```
🔍 кроссовки · Nike
белый · кожа

Найдено: 12 товаров в 18 листингах

1. Nike Air Force 1 Low '07 White
   🛒 Wildberries · 7 990 ₽
   🔗 открыть
2. Nike Air Force 1 — официальный сайт
   🛒 Официальный сайт
   🔗 открыть
3. Nike → Sneakerhead
   🛒 Sneakerhead
   🔗 открыть
…
[📄 Продолжить вывод (2 ещё)] [🔔 Следить за ценой (топ-3)]
```

### `/ml_stats`

CTR по источникам + bandit snapshot + последние 8 событий (impressions/clicks).

Используется для:
- Понимать, какие маркетплейсы дают клики
- Видеть, как bandit обновляется (Beta α/β по категориям)
- Дебажить, если кажется, что результаты не релевантны

---

## 9. Watchlist цен ✨ Day 5

### `/ml_watch`

Список активных price-drop watches.

**Пример вывода:**
```
🔔 Watchlist: 3 активных
#12 · Wildberries · 7 990 ₽  (-5.1%)
   Nike Air Force 1 Low '07 White Triple
   проверено: 2026-05-17 10:00
#13 · Wildberries · 12 500 ₽
   Apple AirPods Pro 2
   проверено: 2026-05-17 10:00

Убрать: /ml_unwatch <id>
```

`±%` показывает изменение от `initial_price` к `last_price`.

### `/ml_unwatch <watch_id>`

Убрать товар из watchlist.

```
/ml_unwatch 12
```

Также есть кнопка «❌ Больше не следить» прямо в уведомлении о падении цены.

### Как добавить в watchlist

В результатах `/ml_search` нажмите **«🔔 Следить за ценой (топ-3)»** — бот добавляет в `ml_watchlist` первые 3 товара, у которых есть цена. Это в основном Wildberries (живой API), реже — Lamoda/DNS, если их парсер вернул price.

### Что происходит дальше

Каждый день в **10:00 по серверному времени** запускается `run_price_drop_check`:

1. Достаёт все `active` watches из `ml_watchlist`.
2. Для каждого URL вызывает `_default_price_fetcher`:
   - Если это `wildberries.ru/catalog/<wb_id>/detail.aspx` — идёт в `card.wb.ru/cards/v2/detail` и достаёт текущую цену.
   - Остальные URL — пока пропускаются (link-only).
3. Считает `dropped_pct = (initial - current) / initial * 100`.
4. Если `dropped_pct >= threshold_pct` (по умолчанию 10%) → шлёт Telegram-уведомление:

```
💸 Цена упала на 15.2%!

Nike Air Force 1 Low '07 White
🛒 Wildberries
Было: 7 990 ₽  →  Стало: 6 775 ₽
Экономия: 1 215 ₽

🔗 Открыть товар
[❌ Больше не следить]
```

Watch переходит в статус `notified` чтобы избежать повторных уведомлений. Можно реактивировать тот же товар, нажав снова «Следить за ценой» в `/ml_search` — bot вернёт его в `active`.

### Параметры watchlist

| Параметр | Значение | Где менять |
|---|---|---|
| `threshold_pct` (порог падения) | 10% | поле в `ml_watchlist` |
| Время проверки | 10:00 ежедневно | `telegram_bot.py: run_daily(...)` |
| Источники с live-ценой | Только Wildberries | `_default_price_fetcher` |
| История цен | Все проверки в `ml_price_history` | можно сделать график позже |

---

## 10. Темы и правила

### `/topic_set <слово> <тема>`

Добавить ассоциацию «слово → тема» в `topic_rules`.

```
/topic_set диван мебель
/topic_set худи одежда
```

После этого, если в подписи к фото будет «нравится #диван», Memory Lane автоматически проставит `topic=мебель`.

### `/topic_list [тема]`

Все правила или фильтр по конкретной теме.

```
/topic_list
/topic_list одежда
```

---

## 11. Фото-сценарии

Когда вы отправляете боту **просто фото** (без команды):

| Подпись (caption) | Действие |
|---|---|
| Фото чека | OCR + парсинг позиций → `purchases` |
| Фото товара + «нравится #X» | Memory Lane запись |
| Фото товара + «запомни», «найди похожее» | Memory Lane + автозапуск vision-pipeline |
| Фото без подписи | Бот спрашивает — это чек или впечатление? |

---

## 12. Системные команды

Эти команды не для пользователя, а для CLI/cron:

### CLI: `consumption status|doctor|check-db|backup-now|restart-bot`

```bash
cd /home/yuri_artamonov/.openclaw/workspace/consumption_agent
python3 consumption.py status      # systemd-юнит running?
python3 consumption.py doctor      # diagnostic check (БД, IMAP, токены)
python3 consumption.py check-db    # SQLite WAL integrity
python3 consumption.py backup-now  # encrypted backup
python3 consumption.py restart-bot # systemctl --user restart
```

### Cron-задачи

| Время | Задача | Команда |
|---|---|---|
| 09:00 ежедневно | Алерты гарантий | `run_daily_alert_job` |
| 10:00 ежедневно | **Day 5:** Проверка цен Memory Lane | `run_price_drop_check` |
| 10:00, 18:00 | Кредитный мониторинг | `credit_monitor.py` |
| 23:30 ежедневно | Сканирование чеков | `daily_cheque_scan.py` |
| Ежечасно 10–23 | Heartbeat: кредиты + штрафы | `check_debts_fines_retry.sh` |

### Логи

Все сканеры пишут в `consumption_agent/logs/`:
- `daily_cheque_scan.log` — IMAP+SMS scan с ScanMetrics
- `credit_monitor.log` — кредитные алерты
- `consumption-bot.log` — основной лог бота (systemd journal)

Формат лога `[SCAN]`:
```
[SCAN] scanner=daily_cheque_scan | account=Gmail | folders=3 | msgs_seen=12 | deduped=2 | parsed=8 | elapsed=1.4s
  └ INBOX: seen=8 dedup=1 parsed=5
  └ [Gmail]/Spam: seen=0 dedup=0 parsed=0
  └ Receipts: seen=4 dedup=1 parsed=3
```

---

## Сводка callback-кнопок

| Кнопка | callback_data | Handler | Где появляется |
|---|---|---|---|
| 🔍 Искать | `ml_search:<id>` | `ml_search_callback` | После обработки фото Memory Lane |
| 🗑 Удалить | `ml_delete:<id>` | `ml_delete_callback` | Memory Lane результат |
| 🔔 Напомнить | `ml_remind:<id>` | `ml_remind_callback` | Memory Lane результат |
| 📄 Продолжить вывод | `ml_page:<id>:<n>` | `ml_page_callback` | /ml_search overflow |
| 🔔 Следить за ценой | `ml_watch:<id>` | `ml_watch_callback` | /ml_search (топ-3 с ценой) |
| ❌ Больше не следить | `ml_unwatch:<id>` | `ml_unwatch_callback` | Price-drop notification |
| ✅ Подтвердить (кредит) | `credit_paid:<id>` | `credit_paid_callback` | /debts |
| ✅ Оплачено (штраф) | `fine_paid:<id>` | `fine_paid_callback` | /fines |
| ✅ Заменено | `item_replaced:<id>` | `item_replaced_callback` | Алерт замены |
| 🗑 Удалить (item) | `item_delete:<id>` | `item_delete_callback` | /items |
| 📸 Фото (item) | `item_photo:<id>` | `item_photo_callback` | /items |
| ✅ Подтвердить (Vision) | `vision_confirm` | `vision_confirm_callback` | Vision OCR |
| ❌ Отклонить (Vision) | `vision_reject` | `vision_reject_callback` | Vision OCR |
| Дедуп: удалить | `dedup_delete:<id>` | `dedup_delete_callback` | Duplicate purchase prompt |
| Дедуп: оставить | `dedup_keep:<id>` | `dedup_keep_callback` | Duplicate purchase prompt |

---

## Безопасность

- Бот реагирует только на сообщения из `ALLOWED_CHAT_IDS` (`.env`).
- Все credentials в `.env` (Gmail/Yandex/Mail.ru passwords, `CONSUMPTION_BOT_TOKEN`).
- `consumption.db` в WAL-режиме, бекапы шифруются.
- Memory Lane фото хранятся локально в `data/media/` (sha256-дедуп).

---

*Сгенерировано автоматически 17 мая 2026 по итогам 5-дневного спринта.*
*Коммиты спринта: `11ccc27`, `f86175c`, `edf5119`, `a2bfa84`, `ae3c0c2`.*
