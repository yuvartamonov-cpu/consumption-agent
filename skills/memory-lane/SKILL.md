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
  3. У каждого фото — кнопка 🗑 Удалить (вызывает `ml_delete_callback`, удаляет запись и файл)

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

## Vision API enrichment

- `vision_item.enrich_memory_lane(image_path, caption)` — вызывает Vision API gpt-4o-mini для распознавания
- Результат: `{name, brand, description, style_tags, topic, color, estimated_price_rub, type}`
- Сохраняется в поля `name`, `description`, `brand` таблицы `memory_lane_items`

## Удаление записи

При нажатии 🗑 Удалить в `/ml_last`:
1. Запись удаляется из `memory_lane_items`
2. Связанный файл удаляется из `media_assets` (запись + файл на диске)
3. Подпись фото меняется на "🗑 Запись #ID удалена"

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
