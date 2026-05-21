---
name: memory-lane
description: Учёт визуальных впечатлений и ассоциаций слово→тема для проекта consumption-agent. Используй когда нужно сохранить фото с впечатлением (нравится/не нравится/хэштеги) в Memory Lane, добавить/просмотреть/изменить ассоциацию слово-тема, работать с таблицами memory_lane_items и topic_rules в consumption.db, диагностировать проблемы с распознаванием темы по хэштегам.
---

# Memory Lane

> **Обновлено 2026-05-17 (5-дневный спринт 13–17 мая):**
> - Day 2: `ml_official_sites.py` — resolver брендовых entry points;
> - Day 3: 200+ слов перевода RU→EN + стемминг + геолокация источников;
> - Day 4: пагинация Telegram-результатов `/ml_search` (кнопка «Продолжить вывод»);
> - Day 5: price-drop watchlist (`ml_watchlist.py`) — команды `/ml_watch`, `/ml_unwatch`, cron 10:00.

## Что это

Memory Lane — модуль consumption-agent для сохранения и категоризации визуальных впечатлений. Пользователь отправляет фото в Telegram с подписью вида
«нравится #пиджак #тёмный», бот сохраняет фото, извлекает теги и тему, обогащает через Vision API (распознаёт название товара, описание, бренд), кладёт в БД.

Дополнительно ведётся таблица `topic_rules` — ассоциации слово→тема, которые можно задавать вручную через Telegram.

## Актуальный source of truth

Подробное, актуальное описание алгоритма кнопки `🔍 Искать`, Vision enrichment и связанного Telegram UX лежит в:

- `consumption_agent/docs/recognition_algorithms.md`

Важно: старые разделы ниже местами описывают ранний план, а не текущую реализацию. Для production-flow приоритет у кода и этого документа.

## Memory Lane Price-Drop Watchlist (Day 5)

После результатов `/ml_search` пользователь может нажать кнопку **«🔔 Следить за ценой»** — бот добавляет до 3 топ-товаров с ценой в `ml_watchlist`. Cron-задача ежедневно в 10:00 (`run_price_drop_check`) перепроверяет цены, и при падении ≥10% присылает Telegram-уведомление.

**Команды:**
- `/ml_watch` — список активных отслеживаний с историей цен (±%)
- `/ml_unwatch <id>` — убрать товар (также есть кнопка в нотификации)

**Таблицы:** `ml_watchlist` (item_id, product_url, initial_price, threshold_pct, status), `ml_price_history` (трек всех проверок). Статус lifecycle: `active → notified → active` (через add) или `dismissed`.

## Адаптивность и синхронизация тем (Спринт 21.05.2026)

- **LLM-независимость**: Вся работа с нейросетями (Vision, переводы запросов, категоризация, поиск специализированных магазинов) переведена на систему fallback-роутинга (`llm_router.py`). Агент автоматически переключается между OpenAI, Anthropic (Claude Opus), DeepSeek, Gemini и xAI при исчерпании квоты или ошибках, снижая зависимость от одного провайдера.
- **Синхронизация тем и категорий**: Темы в `topic_rules` синхронизированы с глобальным справочником категорий (`categories` таблица, колонка `topic_name`). Команда `/topic_list` показывает консолидированный список тем из Memory Lane и товарных категорий.
- **Интерактивная категоризация**: При сохранении фото в Memory Lane агент классифицирует вещь по темам (`topics`). Если нейросеть не смогла уверенно сопоставить товар с существующей темой, бот присылает сообщение `❓ Тема не распознана. Выберите из списка:` и выводит inline-клавиатуру со всеми темами. Выбор пользователя закрепляет тему за товаром.

### Инициализация схемы

```python
import memory_lane as ml
conn = get_db()  # подключение к consumption.db
ml.ensure_memory_lane_schema(conn)  # создаёт memory_lane_items, media_assets, topic_rules
ml.seed_default_topic_rules(conn)   # заполняет topic_rules дефолтными правилами из TOPIC_RULES
conn.close()
```

### Сохранение впечатления (самый частый сценарий)

```python
parsed = ml.parse_caption('нравится #диван #кожаный', conn=conn)
# → {'liked': ['нравится'], 'disliked': [], 'style_tags': ['диван', 'кожаный'], 'topic': 'мебель'}

asset_id = ml.save_media(conn, file_bytes, mime='image/jpeg')
# Опционально: результат Vision API enrich_memory_lane(image_path, caption)
vision_info = {'name': 'Кожаный диван', 'description': 'Диван с кожаной обивкой', 'brand': None}
item_id = ml.save_memory_lane(conn, 'нравится #диван #кожаный', asset_id, parsed, vision_info=vision_info)
```

## Модули

### memory_lane.py — ядро

**Функции:**
- `ensure_memory_lane_schema(conn)` — создаёт таблицы `memory_lane_items`, `media_assets`, `topic_rules`
- `is_memory_lane_caption(text)` — проверяет, содержит ли подпись триггер-слова или хэштеги
- `parse_caption(text, conn=None)` — извлекает liked/disliked/style_tags/topic. Если передан `conn`, сначала проверяет `topic_rules` в БД, потом статические `TOPIC_RULES`
- `save_media(conn, file_bytes, mime, base_dir)` — сохраняет файл в `data/media/<sha256>.<ext>`, возвращает id
- `save_memory_lane(conn, caption, media_asset_id, parsed, source='telegram', vision_info=None)` — запись в `memory_lane_items`. Принимает `vision_info` с полями `name`, `description`, `brand` (опционально).
- `list_recent(conn, n=10, topic=None)` — последние N записей, опционально с фильтром по теме. Возвращает также `name`, `description`, `brand`.
- `search_items(conn, query, *, topic=None, brand=None, color=None, limit=20)` — **(Day 2, 20.05)** текстовый поиск (SQL LIKE) по caption/name/description/brand/style_tags. Регистронезависимо для кириллицы через зарегистрированную функцию `pylower` (встроенный `LOWER()` в SQLite только ASCII). Используется `/ml_find`.
- `build_profile(conn, topic=None, *, examples=5, top_n=5)` — **(Day 2, 20.05)** агрегирует профиль вкуса без LLM: топ liked/disliked, бренды, цвета, материалы, style-теги + последние примеры. Цвета/материалы выводятся из `style_tags` по словарям `COLOR_WORDS` / `MATERIAL_WORDS`. Используется `/ml_profile`.
- `_ensure_vision_columns(conn)` — добавляет колонки `name`, `description`, `brand` в `memory_lane_items`, если их нет.

**Команды Telegram (Day 2, 20.05):**
- `/ml_find <запрос> [--topic T] [--brand B] [--color C]` — текстовый поиск; рядом с каждым результатом кнопка «🔍 #id» → `/ml_search`.
- `/ml_profile [тема]` — профиль вкуса по агрегации.
- `/ml_stats` дополнен блоком **source matcher (tier × geo × CTR за 30 дней)** через `ml_source_matcher.source_stats(conn, since_days=30)`.

**Примечание про deleted_at:** в `memory_lane_items` нет колонки `deleted_at` — удаление записи это hard DELETE (см. `ml_delete` callback), поэтому фильтрация удалённых в поиске не нужна.

**Для таблицы topic_rules:**
- `seed_default_topic_rules(conn)` — заполняет дефолтными правилами из `TOPIC_RULES` в коде
- `lookup_topic(conn, text)` — ищет тему в БД, обновляет счётчик usage_count
- `set_topic_rule(conn, keyword, topic)` — добавляет/обновляет правило. Возвращает True, если создано новое
- `list_topic_rules(conn, topic=None)` — список всех правил

### Схема memory_lane_items

```
id, profile_id, created_at, caption,
liked_features (JSON), disliked_features (JSON), style_tags (JSON),
topic, media_asset_id, source,
name, description, brand  ← добавочные, от Vision API
```

### TOPIC_RULES (статические, в memory_lane.py)

Темы: одежда, мебель, интерьер, еда, техника, аксессуары, косметика. При необходимости — расширять прямо в коде.

## Telegram-интеграция (в telegram_bot.py)

- **photo_handler** — при получении фото с подписью:
  1. Проверяет `is_memory_lane_caption`
  2. Сохраняет фото в `media_assets`
  3. Парсит подпись через `parse_caption`
  4. Обогащает через `vision_item.enrich_memory_lane()` (Vision API: распознаёт название, описание, бренд)
  5. Сохраняет через `save_memory_lane(conn, caption, asset_id, parsed, vision_info=vision_info)`
  6. Отвечает пользователю с названием, брендом, описанием, если распознано

- **`/ml_last [N]`** — показать последние N записей:
  1. Текстовый список с названием товара (если распознано) и описанием
  2. Для каждой записи с фото — отправляет фото с подписью (название, описание, тема, дата)
  3. У каждого фото — кнопки:
     - 🔍 Искать — поиск товара на маркетплейсах
     - ⏰ Напомнить — напомнить через N дней/месяцев
     - 🗑 Удалить — удалить запись

- **`/topic_set <слово> <тема>`** — добавить/обновить ассоциацию (пишется как правило `topic_rules`)
- **`/topic_list [тема]`** — показать все правила

Пример ответа бота:
```
🧠 Memory Lane #3
📌 Кожаный диван
Реакция: нравится
Стиль: диван, кожаный
Тема: мебель
📝 Диван с кожаной обивкой. Современный стиль.
```

## Поиск товаров из Memory Lane

Краткая актуализация:

- поиск запускается через `ml_search_v2`, а не через старый плоский `search_query`;
- используется cached/fresh extraction атрибутов фото;
- foreign queries переводятся не буквально, а через semantic visual query;
- в Telegram у результатов есть отдельные top-link кнопки, пагинация и watchlist.

При нажатии 🔍 Искать в `/ml_last`:
1. **Распознавание фото** — OpenAI Vision API (gpt-4o-mini) генерирует описание:
   - `name` — название товара
   - `brand` — бренд
   - `category` — категория
   - `article` — артикул/модель
   - `search_query` — оптимальный запрос для поиска
2. **Поиск через API маркетплейсов** (параллельно):
   - Ozon API
   - Wildberries API
   - Яндекс.Маркет API
3. **Выбор лучшего** — по минимальной цене
4. **Вывод** — название, цена, магазин, ссылка

### Fallback
Если API недоступны:
- Поиск по фото через Яндекс.Картинки
- Прямые ссылки на поиск в маркетплейсах

### Напоминания
При нажатии ⏰ Напомнить:
- Варианты: 7 дней, 30 дней, 3 месяца, 6 месяцев, не напоминать
- Сохраняется в таблицу `ml_reminders`
- Проверка через cron/heartbeat

## Vision API enrichment

- `vision_item.enrich_memory_lane(image_path, caption)` — вызывает Vision API gpt-4o-mini для распознавания
- Результат: `{name, brand, description, style_tags, topic, color, estimated_price_rub, type}`
- Сохраняется в поля `name`, `description`, `brand` таблицы `memory_lane_items`

## Удаление записи

При нажатии 🗑 Удалить в `/ml_last`:
1. Запись удаляется из `memory_lane_items`
2. Связанный файл удаляется из `media_assets` (запись + файл на диске)
3. Подпись фото меняется на "🗑 Запись #ID удалена"

## Распознавание предметов по фото (Inventory / Vision)

Помимо Memory Lane, бот распознаёт предметы на фото и предлагает добавить их в инвентарь.

### Поток обработки фото (photo_handler)

1. **Классификация** — `vision_item.classify_photo_async()` определяет тип: receipt / tag / clothing / food / interior / tech / item / other
2. **Если предмет** (clothing/food/interior/tech/item/other/unknown) — вызывается `vision_item.recognize_item_async()`
3. **Результат** сохраняется во временную структуру `ctx.user_data['vision_pending']`
4. **Пользователю показываются кнопки:**
   - ✅ Подтвердить — сохраняет в БД, запрашивает доп. информацию через ForceReply
   - ❌ Отклонить — удаляет товар из БД и фото

### Подтверждение (vision_confirm_callback)

- Убирает кнопки, обновляет сообщение
- Отправляет ForceReply: "📝 Введите дополнительную информацию о товаре (бренд, размер, материал):"
- Сохраняет `item_id` в `ctx.user_data['vision_awaiting_notes']`

### Доп. информация (text_handler)

- Если текст не пустой (до 50 символов) — добавляет в `items.notes`
- Если пустой — ничего не добавляет

### Отклонение (vision_reject_callback)

- Soft delete товара (`deleted_at`, `status='disposed'`)
- Удаление фото из `media_assets` и с диска
- Удаление связи `item_photos`

### vision_item.py — ядро распознавания

- `_call_vision()` — синхронный вызов OpenAI Vision API (gpt-4o-mini)
- `_call_vision_with_timeout()` — запуск в отдельном `multiprocessing.Process` с жёстким таймаутом 30 сек
- `classify_photo()` / `classify_photo_async()` — быстрая классификация
- `recognize_item()` / `recognize_item_async()` — полное распознавание с полями name, brand, category, color, material, style_tags, description, estimated_price_rub
- При таймауте возвращает: `{"error": "timeout", "name": "Объект не распознан"}`

### Жёсткий таймаут 30 секунд

Проблема: OpenAI API может зависнуть и заблокировать event loop.
Решение: `multiprocessing.Process` + `process.terminate()` / `process.kill()` если не завершился за 30 сек.

```python
def _call_vision_with_timeout(image_path, prompt, timeout=30.0):
    manager = multiprocessing.Manager()
    result_dict = manager.dict()
    
    process = multiprocessing.Process(target=worker, args=(...))
    process.start()
    
    elapsed = 0.0
    while process.is_alive() and elapsed < timeout:
        time.sleep(0.5)
        elapsed += 0.5
    
    if process.is_alive():
        process.terminate()
        process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
    
    return result_dict.get('result'), result_dict.get('timed_out', False)
```

## Рабочая заметка: отправка писем из этого окружения

Если нужно быстро отправить себе список задач или статус по почте из `consumption_agent`, рабочий способ такой:

1. Перейти в `consumption_agent`
2. Экспортировать переменные из `.env` через `set -a && source .env && set +a`
3. Использовать `smtplib.SMTP_SSL('smtp.gmail.com', 465)`
4. Брать пароль из `GMAIL_APP_PASSWORD`, очищая `replace('"', '').replace(' ', '')`
5. Логиниться как `yu.v.artamonov@gmail.com`

Минимальный шаблон:

```python
import os, smtplib
from email.mime.text import MIMEText

user = 'yu.v.artamonov@gmail.com'
pwd = os.getenv('GMAIL_APP_PASSWORD', '').replace('"', '').replace(' ', '')
msg = MIMEText('test', 'plain', 'utf-8')
msg['Subject'] = 'test'
msg['From'] = user
msg['To'] = user

with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=20) as server:
    server.login(user, pwd)
    server.sendmail(user, [user], msg.as_string())
```

Важно: просто `source .env` может не экспортировать переменные в Python-процесс. Нужен именно `set -a`.

## План развития поиска

### Этап 1: Маркетплейсы (реализовано)
- Ozon, Wildberries, Яндекс.Маркет
- Быстрый поиск, сравнение цен
- Проблема: API часто блокируют, результаты не всегда точные

### Этап 2: Сайты производителей (TODO)
- Поиск по артикулу/модели на официальных сайтах
- Примеры:
  - Nike → nike.com/ru
  - Adidas → adidas.ru
  - Zara → zara.com/ru
  - H&M → hm.com/ru
- Метод: `site:nike.com {артикул}` через web_search

### Этап 3: Дистрибьюторы (TODO)
- Специализированные магазины по категориям:
  - Одежда: lamoda.ru, brandshop.ru, svyatnyh.ru
  - Техника: citilink.ru, dns-shop.ru, mvideo.ru
  - Мебель: ikea.com/ru, Hoff, Mr.Doors
  - Косметика: iledebeaute.ru, goldapple.ru
- Поиск через `site:lamoda.ru {название}`

### Этап 4: Геолокация (TODO)
- Определение ближайших магазинов
- Проверка наличия в конкретном городе
- Интеграция с картами (2GIS, Яндекс.Карты)

### Этап 5: История цен (TODO)
- Отслеживание динамики цен
- Уведомление о снижении цены
- Интеграция с сервисами типа Price.ru

## План рефакторинга поиска (18.05.2026)

### Проблемы
1. **Некорректный перевод на английский** — словарный QUERY_TRANSLATIONS теряет контекст и смысл
2. **Поиск велосипеда на luxury-сайтах** — нет классификации источников по типу товара
3. **Нет обучения** — система не запоминает, какие источники релевантны для данного типа товара

### План изменений

#### 1. ml_translate.py (новый)
- Перевод запросов через LLM (GPT-4o-mini) с полным контекстом Memory Lane:
  - name, description, style_tags, caption, brand, subcategory, category, material, color
- Fallback на словарный перевод если LLM недоступен
- Поддержка языков: en, de, kk/kz

#### 2. search_sources — таблица в consumption.db
- Структура:
```sql
CREATE TABLE search_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,            -- 'lamoda', 'brandshop', 'oskelly', 'kaspi', 'amazon'
  name TEXT NOT NULL,                  -- 'Lamoda', 'Oskelly', 'Amazon'
  url_template TEXT,                   -- 'https://...{query}'
  site_domain TEXT,                    -- 'lamoda.ru' (для site: поиска)
  category_tags TEXT,                  -- JSON: ['одежда', 'обувь', 'аксессуары']
  item_types TEXT,                     -- JSON: ['luxury_clothing', 'streetwear', 'footwear', 'cycling', 'electronics', ...]
  geo TEXT DEFAULT 'RU',               -- 'RU', 'KZ', 'BY', 'EU', 'US', 'ALL'
  tier TEXT DEFAULT 'aggregator',      -- 'manufacturer' | 'distributor' | 'aggregator' | 'marketplace'
  score REAL DEFAULT 1.0,              -- обучаемый вес источника
  is_active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
```

#### 3. ml_source_matcher.py (новый)
- Заменяет жёсткие CATEGORY_SOURCES в ml_search_v2.py
- `get_sources(item_type, geo, tier_filter=None, top_n=12)` — динамический подбор
- `record_click(source_key, item_type)` — повышает score для пары (source, item_type)
- `record_skip(source_key, item_type)` — понижает score
- `get_item_type(attrs)` — определяет тип товара по Vision атрибутам
  - (бренд люксовый → luxury, спортивный → sport, еда → grocery, техника → electronics)

#### 4. ml_bandit.py — дообучение
- Уже есть `ml_bandit.sample_sources()`, но он работает по категории, не по типу
- Расширить: добавить `train_source_pair(source_key, item_type, reward)`
- Reward = +1 при клике, -0.5 при игнорировании (пользователь выбрал другой источник)

#### 5. ml_providers.py — новый build_source_query_bundle
- Вместо слияния первых 12 токенов — собирать осмысленный запрос:
  1. brand + name + model + article + primary_color + material + fit + gender
  2. Если есть description — добавить ключевые слова из него
  3. Перевести через ml_translate.translate_query() с контекстом
- Убрать очистку кириллицы после брендов (Soulux не должен теряться)

#### 6. route_sources() — динамический подбор
- Жёсткий CATEGORY_SOURCES заменить на вызов ml_source_matcher.get_sources()
- Фильтрация по гео (оставить)
- Bandit-сортировка (оставить)
- Подмешивать brand-site pinned

### 🎯 Новая философия: агент вместо маркетплейсов (19.05.2026)

**Ключевая идея:** consumption agent не должен быть клиентом маркетплейсов.
Он должен их **заменять**. Маркетплейсы — только fallback для сверки цен.

#### Что агент делает вместо маркетплейсов:
1. **Собирает визуальные предпочтения** (Memory Lane) — фото, реакции, оценки
2. **Распознаёт товары** — Vision API определяет бренд, модель, категорию
3. **Ведёт инвентарь** — что куплено, когда, какие гарантии, когда менять
4. **Ищет у прямых продавцов** — официальные сайты брендов, дистрибьюторов
5. **Рекомендует на основе вкуса** — не «что дешевле», а «что подходит тебе»
6. **Напоминает о замене** — по сроку службы, а не по скидке

#### Почему не маркетплейсы:
- API маркетплейсов ненадёжны (Ozon — куки протухают, WB — ограничения)
- Маркетплейсы показывают всё подряд, не зная вкуса пользователя
- Агент знает, что у тебя уже есть (нет дублей)
- Маркетплейсы зарабатывают на рекламе, агент — на точности рекомендации

#### Новая иерархия источников:
1. **manufacturer** — официальный сайт бренда (nike.com, schneider.com) — самый важный
2. **distributor** — официальный ритейлер/дистрибьютор (brandshop, lamoda для одежды)
3. **aggregator** — агрегатор цен (Price.ru, E-Katalog, Яндекс.Маркет) — для справки
4. **marketplace** — Ozon, Wildberries, Amazon — только когда ничего не нашлось выше

### Примеры item_types
- `luxury_clothing`, `streetwear`, `footwear`, `sportswear`, `formal_wear`
- `electronics`, `computers`, `audio`, `kitchen_appliances`
- `furniture`, `lighting`, `home_decor`
- `cosmetics`, `skincare`, `perfume`
- `cycling`, `sports_equipment`, `fitness`
- `books`, `toys`, `pet_supplies`
- `auto_parts`, `hardware`

### Обучение системы
1. При каждом клике пользователя на результат → `record_click(source_key, item_type)`
2. При выборе другого результата → `record_skip(source_key, item_type)`
3. Bandit периодически пересчитывает источники по типам товаров
4. Со временем для `циклинг` исчезнут `leform` и `brandshop`, а появятся `velosipedov.ru`, `chainreactioncycles.com`

## Ozon: отказ от прямого API (19.05.2026)

### Проблема
Ozon сильно закрутил защиту — куки живут недолго, API эндпоинты меняются, `.ozon_cookies.txt` пустой. Прямой API-доступ к Ozon фактически не работает.

### Решение
Переходим на web-parsing для Ozon и подключаем агрегаторы цен:

#### 1. Ozon — web scraping через site-search
- Прямые API-запросы к api.ozon.ru заменяем на Google site-search:
  `site:ozon.ru {query}`
- Используем существующий механизм `_build_site_search_url()` в `ml_providers.py`
- Ozon остаётся в результатах поиска, но как link-only источник без цен
- Цены можно будет добрать при открытии ссылки через парсинг страницы товара (TODO)

#### 2. Добавление Megamarket (site-search)
- Нет писем на почте от Megamarket → используем site-search через Google
- `site:megamarket.ru {query}`
- Добавить в seed_sources: key='megamarket', tier='marketplace', geo='RU'

#### 3. Подключение агрегаторов цен
- **Price.ru** — `site:price.ru {query}`, агрегатор, показывает цены с Ozon, WB, Яндекс.Маркета
- **Goods.ru** — `site:goods.ru {query}`
- **E-katalog** (ekatalog.ru) — техника, сравнение цен
- **Priceva.ru** — мониторинг цен
- **market.yandex.ru** — уже есть
- **Price24.ru** / **Priceonline.ru** — дополнительные агрегаторы

#### 4. Приоритет
- Для техники: e-katalog > price.ru > Яндекс.Маркет > Wildberries > Ozon (site-search)
- Для одежды: Яндекс.Маркет > Lamoda > Brandshop > Ozon (site-search) > Wildberries
- Агрегаторы показывают реальные цены без авторизации — их ставим выше site-search Ozon

#### 5. Прайсинг через парсинг страницы (TODO)
- При клике на link-only результат — парсить страницу товара для извлечения цены
- Использовать curl + регулярки или BeautifulSoup
- Сохранять в `item_price_links` или `ml_watchlist`
- Реализовать после voice input


## Telegram Voice Input (Спринт 19.05.2026 — Новая задача)

### Проблема
Пользователь может отправлять голосовые сообщения в Telegram, но consumption bot их не обрабатывает — голосовые падают в OpenClaw main session, где расшифровываются через Whisper API, но не попадают в бота.

### Цель
Добавить поддержку аудиосообщений (voice/audio) в consumption bot для команд `/add`, `/ml_search` и `Memory Lane` через голосовой ввод.

### Архитектура

**Вариант A: Whisper API в боте (рекомендуемый)**
- В consumption bot добавить обработчик `voice_handler` и `audio_handler`
- При получении голосового/аудио → скачать файл через `bot.get_file(file_id)`
- Отправить в OpenAI Whisper API (`/v1/audio/transcriptions`)
- Полученный текст прогнать через существующие хендлеры:
  - Если содержит хэштеги/«нравится» → Memory Lane (`photo_handler`-подобная логика, но без фото)
  - Если содержит «найди», «ищи», «поиск» → `/ml_search`
  - Если содержит «добавь» → `/add_item`
- Результат распознавания и ответ бота — в том же сообщении

**Вариант B: OpenClaw как прокси (альтернативный)**
- OpenClaw main session получает голосовое от Telegram
- Расшифровывает через Whisper API
- Отправляет текст в consumption bot через `sessions_send`
- Бот обрабатывает как обычное текстовое сообщение

**Выбор: Вариант A**, т.к.:
- Меньше зависимостей от OpenClaw runtime
- Прямая интеграция, быстрее
- Whisper API уже есть (openai-whisper-api skill)

### План реализации

1. **Создать `bot/handlers/voice.py`**:
   - `voice_handler(update, context)` — принимает `Message.voice`
   - `audio_handler(update, context)` — принимает `Message.audio` (если пользователь шлёт файл)
   - `_transcribe_voice(file_path)` — вызывает Whisper API через curl/openai
     - Получает `file_id` → `bot.get_file(file_id).download()` → сохраняет временно
     - Вызывает `curl https://api.openai.com/v1/audio/transcriptions ...` или использует `openai` Python SDK
     - Возвращает текст распознавания
   - `_route_voice_to_handler(text, update, context)` — определяет интент:
     - Memory Lane (хэштеги/нравится/не нравится) → переиспользует `photo_handler` (без фото)
     - `/ml_search` (найди/ищи/поиск) → вызывает `cmd_ml_search` или `_ml_search_force`
     - `/add_item` (добавь/запиши/сохрани) → вызывает `cmd_add`
     - Fallback: просто вернуть текст распознавания + вопрос «Что с этим делать?»

2. **Зарегистрировать хендлеры в `bot/main.py`**:
   ```python
   from bot.handlers.voice import voice_handler, audio_handler
   dp.add_handler(MessageHandler(filters.VOICE, voice_handler))
   dp.add_handler(MessageHandler(filters.AUDIO, audio_handler))
   ```

3. **Создать `bot/transcriber.py`**:
   - Изолированный модуль для вызова Whisper API
   - `transcribe(file_path: str, language: str = 'ru') -> str`
   - Использует `openai.OpenAI().audio.transcriptions.create()`
   - Поддержка форматов: ogg, m4a, mp3, wav
   - Логирование длительности и размера файла

4. **Интеграция с Memory Lane**:
   - Если голосовое содержит хэштеги → сохранить в `memory_lane_items` как текстовую запись (без фото)
   - Поле `media_asset_id = NULL` — допустимо для voice-only entry
   - Ответ бота: «🧠 Сохранено в Memory Lane: {текст}»

5. **Интеграция с `/ml_search`**:
   - Если голосовое содержит «найди/ищи/поиск» + описание товара → запустить поиск
   - Разобрать описание: «найди кроссовки Nike» → brand=Nike, name=кроссовки
   - Вызвать `cmd_ml_search` с распознанным текстом как caption
   - Если фото нет — поиск по текстовому описанию без Vision attributes

6. **Обработка ошибок**:
   - Если Whisper API недоступен — ответ: «Не удалось распознать голос. Попробуй написать текстом.»
   - Если текст пустой — ответ: «Голосовое распознано, но текст пустой. Попробуй ещё раз.»
   - Таймаут 30 секунд на распознавание

### Зависимости
- `openai` Python SDK (уже есть в consumption_agent)
- Права на чтение файлов Telegram (file download)
- `OPENAI_API_KEY` в `.env` (для Whisper API)


## Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `memory_lane.py` | Ядро: парсинг, сохранение, topic_rules |
| `vision_item.py` | Распознавание предметов через OpenAI Vision API |
| `ml_search.py` | Поиск товаров из Memory Lane на маркетплейсах |
| `telegram_bot.py` | Интеграция: photo_handler, /ml_last, /topic_set, /topic_list, кнопки |
| `consumption.db` | SQLite: таблицы `memory_lane_items`, `media_assets`, `topic_rules`, `ml_reminders` |
| `ml_translate.py` | **(новый)** LLM-перевод запросов с контекстом Memory Lane |
| `ml_source_matcher.py` | **(новый)** Динамический подбор источников по типу товара + обучение |
| `ml_providers.py` | API-провайдеры и link-only источники (обновляется) |
| `ml_search_v2.py` | Оркестратор поиска (обновляется: route_sources → ml_source_matcher) |
| `ml_bandit.py` | Thompson-sampling сортировка источников (обновляется: обучение пар source↔item_type) |

## Диагностика

**Тема не определена (topic = None / —):**
1. Проверить, есть ли ключевое слово в `TOPIC_RULES` или `topic_rules`
2. Если нет — добавить через `/topic_set`
3. Если есть, но вызвано через `parse_caption` без `conn` — передать `conn`

**Ошибка «no such table: topic_rules»:**
- Не вызван `ensure_memory_lane_schema` → вызвать перед любой работой с topic_rules

**Название/описание не сохраняются:**
- Проверить, что `vision_info` передан в `save_memory_lane`
- Проверить, что `_ensure_vision_columns` добавил колонки (если не сработало — выполнить вручную)

**Поиск не работает:**
- Проверить `ml_search.py` — импортируется ли корректно
- Проверить API-ключи для OpenAI Vision
- Проверить наличие `photo_path` в `memory_lane_items` (через `media_assets`)

---

## 💰 Монетизация (19.05.2026)

**Философия:** Агент не зарабатывает на комиссии маркетплейсов.
Он зарабатывает на прямых продавцах, данных и подписке.

Подробнее: `docs/monetization.md`

### 1. Инвентарная реклама (CPA)
- Бренды платят за рекомендацию, когда у пользователя созрела замена
- CPA вместо CPM — бренд платит за переход/покупку
- Агент знает профиль, бренды не платят за показы нецелевой аудитории

### 2. Data-intelligence для брендов
- Агрегированные инсайты по категориям без PII
- «Пользователи меняют кроссовки раз в 8-14 мес, 62% тёмные цвета»
- Недоступно маркетплейсам — они видят транзакции, не lifecycle

### 3. Premium B2C
- Бесплатно: 50 товаров, базовые напоминания, site-search
- Премиум: неограниченный инвентарь, импорт с почты, price-drop alerts

### 4. Affiliate без маркетплейсов
- Tier 1 (manufacturer) → 10-15%, Tier 2 (distributor) → 5-10%
- Tier 3 (aggregator) → 2-5%, Tier 4 (marketplace) → 1-3% (только fallback)

### 5. Данные → скидки от брендов (6-12 мес)
- Пользователь разрешает передачу профиля предпочтений брендам
- Уровни: анонимная статистика → профиль → уведомления от бренда
- Скидка пользователю (5-15%) + комиссия агенту (5-10%) от бренда
- Замена модели маркетплейса: вместо 15-30% комиссии — прямая связь
- Технически: таблица `user_data_consent`, анонимайзер, бренд-партнёрский API

### 6. P2P-обмен между агентами (18+ мес)
- Пользователи выставляют вещи из инвентаря на продажу/отдачу
- Агенты сводят: «у A есть drill, B ищет drill» — без Avito, без комиссии
- Агент знает реальный инвентарь, историю, гарантию
- Мотивация: можно купить дешевле и доверять (агент проверяет)
- **Комиссия: 0%** — matching бесплатно, ценность в точности
- Платное: приоритет listings, расширенный радиус, автоуведомления

Подробнее: `docs/monetization.md`
