---
name: openai-vision
description: >
  Распознавание изображений через OpenAI Vision API (GPT-4o-mini/GPT-4o).
  Используй когда нужно распознать чек по фото (товары, сумму, магазин),
  определить предмет на фото (тип, название, бренд, цвет, категорию),
  классифицировать фото (receipt/tag/clothing/food/interior/tech/item/other),
  обогатить запись Memory Lane данными с фото,
  помочь /add_item в consumption-agent с распознаванием по фото.
---

# OpenAI Vision API

Два модуля распознавания изображений через OpenAI Vision API.

## Требования

- `OPENAI_API_KEY` в окружении (или `.env`)
- `openai` (`pip install openai`)

## Модули

### `vision_receipt.py` — распознавание чеков

```python
from vision_receipt import recognize_receipt, format_receipt_response

result = recognize_receipt("path/to/receipt.jpg")
# → {"store": "Ozon", "date": "2025-11-25", "items": [...], "total": 7076.00}

text = format_receipt_response(result)
# → форматированный текст для Telegram
```

Поля результата:
- `store` — название магазина
- `date` — дата в формате YYYY-MM-DD
- `items` — список товаров: `[{name, qty, price}]`
- `delivery` — информация о доставке
- `total` — итоговая сумма

### `vision_item.py` — распознавание предметов

```python
from vision_item import classify_photo, recognize_item, enrich_memory_lane

# Быстрая классификация (дешёво, ~2K tokens)
photo_type = classify_photo("photo.jpg")
# → "receipt" | "clothing" | "food" | "item" | ...

# Полное распознавание предмета
info = recognize_item("photo.jpg")
# → {"type": "clothing", "name": "Синий пиджак", "brand": "Lardini", ...}

# Обогащение для Memory Lane
data = enrich_memory_lane("photo.jpg", caption="Пиджак Lardini")
# → {"topic": "одежда", "style_tags": ["formal", "blue"], ...}
```

Поля результата `recognize_item`:
- `type` — тип: item/food/interior/clothing/receipt/tag/other
- `name` — название предмета
- `brand` — бренд (если виден)
- `category` — категория
- `color`, `material` — цвет, материал
- `style_tags` — теги стиля [2-5]
- `description` — краткое описание
- `estimated_price_rub` — примерная цена (если оценена)

## Модель

По умолчанию `gpt-4o-mini` (дёшево, ~$0.004-0.006 за фото).  
Переопределяется через `VISION_MODEL` в окружении или аргумент `model=`.

## Промпты

- **ITEM_PROMPT** — детальный промпт для предметов (JSON-схема + правила)
- **CLASSIFY_PROMPT** — однократная классификация
- **RECEIPT_PROMPT** — промпт для чеков с правилами парсинга

## Использование из consumption-agent

Файлы лежат в `consumption_agent/`. В `telegram_bot.py` используются:
- `photo_handler` — классифицирует фото через `classify_photo`, затем чеки → `recognize_receipt`, предметы → `recognize_item`
- `/add_item` с фото — вызывает `recognize_item` для автозаполнения
- Memory Lane — `enrich_memory_lane` для обогащения записи

## CLI

```bash
# Распознать предмет
python3 vision_item.py photo.jpg

# Только классификация
python3 vision_item.py photo.jpg classify

# Распознать чек
python3 vision_receipt.py receipt.jpg
```
