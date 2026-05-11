---
name: memory-lane
description: Учёт визуальных впечатлений и ассоциаций слово→тема для проекта consumption-agent. Используй когда нужно сохранить фото с впечатлением (нравится/не нравится/хэштеги) в Memory Lane, добавить/просмотреть/изменить ассоциацию слово-тема, работать с таблицами memory_lane_items и topic_rules в consumption.db, диагностировать проблемы с распознаванием темы по хэштегам.
---

# Memory Lane

## Что это

Memory Lane — модуль consumption-agent для сохранения и категоризации визуальных впечатлений. Пользователь отправляет фото в Telegram с подписью вида «нравится #пиджак #тёмный», бот сохраняет фото, извлекает теги и тему, кладёт в БД.

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
item_id = ml.save_memory_lane(conn, 'нравится #диван #кожаный', asset_id, parsed)
```

## Модули

### memory_lane.py — ядро

**Функции:**
- `ensure_memory_lane_schema(conn)` — создаёт таблицы `memory_lane_items`, `media_assets`, `topic_rules`
- `is_memory_lane_caption(text)` — проверяет, содержит ли подпись триггер-слова или хэштеги
- `parse_caption(text, conn=None)` — извлекает liked/disliked/style_tags/topic. Если передан `conn`, сначала проверяет `topic_rules` в БД, потом статические `TOPIC_RULES`
- `save_media(conn, file_bytes, mime, base_dir)` — сохраняет файл в `data/media/<sha256>.<ext>`, возвращает id
- `save_memory_lane(conn, caption, media_asset_id, parsed)` — запись в `memory_lane_items`
- `list_recent(conn, n=10, topic=None)` — последние N записей, опционально с фильтром по теме

**Для таблицы topic_rules:**
- `seed_default_topic_rules(conn)` — заполняет дефолтными правилами из `TOPIC_RULES` в коде
- `lookup_topic(conn, text)` — ищет тему в БД, обновляет счётчик usage_count
- `set_topic_rule(conn, keyword, topic)` — добавляет/обновляет правило. Возвращает True, если создано новое
- `list_topic_rules(conn, topic=None)` — список всех правил

### TOPIC_RULES (статические, в memory_lane.py)

Темы: одежда, мебель, интерьер, еда, техника, аксессуары, косметика. При необходимости — расширять прямо в коде.

## Telegram-интеграция (в telegram_bot.py)

- **photo_handler** — при получении фото с подписью проверяет `is_memory_lane_caption`, вызывает `save_media` + `save_memory_lane`, отвечает пользователю
- **`/ml_last [N]`** — показать последние N записей
- **`/topic_set <слово> <тема>`** — добавить/обновить ассоциацию (пишется как правило `topic_rules`)
- **`/topic_list [тема]`** — показать все правила

Ответ бота:
```
🧠 Memory Lane #2
Реакция: нравится
Стиль: пиджак, тёмный, клетчатый
Тема: одежда
```

## Диагностика

**Тема не определена (topic = None / —):**
1. Проверить, есть ли ключевое слово в `TOPIC_RULES` или `topic_rules`
2. Если нет — добавить через `/topic_set`
3. Если есть, но вызвано через `parse_caption` без `conn` — передать `conn`

**Ошибка «no such table: topic_rules»:**
- Не вызван `ensure_memory_lane_schema` → вызвать перед любой работой с topic_rules

**usage_count не увеличивается:**
- `lookup_topic` увеличивает счётчик, но прямые запросы через `list_topic_rules` — нет. Это нормально.
