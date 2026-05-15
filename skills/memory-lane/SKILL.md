---
name: memory-lane
description: Учёт визуальных впечатлений и ассоциаций слово→тема для проекта consumption-agent. Используй когда нужно сохранить фото с впечатлением (нравится/не нравится/хэштеги) в Memory Lane, добавить/просмотреть/изменить ассоциацию слово-тема, работать с таблицами memory_lane_items и topic_rules в consumption.db, диагностировать проблемы с распознаванием темы по хэштегам.
---

# Memory Lane

## Что это

Memory Lane — модуль consumption-agent для сохранения и категоризации визуальных впечатлений. Пользователь отправляет фото в Telegram с подписью вида
«нравится #пиджак #тёмный», бот сохраняет фото, извлекает теги и тему, обогащает через Vision API (распознаёт название товара, описание, бренд), кладёт в БД.

Дополнительно ведётся таблица `topic_rules` — ассоциации слово→тема, которые можно задавать вручную через Telegram.

## Быстрый старт

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
- `_ensure_vision_columns(conn)` — добавляет колонки `name`, `description`, `brand` в `memory_lane_items`, если их нет.

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

## Ключевые файлы

| Файл | Назначение |
|------|-----------|
| `memory_lane.py` | Ядро: парсинг, сохранение, topic_rules |
| `vision_item.py` | Распознавание предметов через OpenAI Vision API |
| `ml_search.py` | Поиск товаров из Memory Lane на маркетплейсах |
| `telegram_bot.py` | Интеграция: photo_handler, /ml_last, /topic_set, /topic_list, кнопки |
| `consumption.db` | SQLite: таблицы `memory_lane_items`, `media_assets`, `topic_rules`, `ml_reminders` |

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
