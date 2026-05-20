"""
vision_receipt.py — Распознавание чеков через Vision API (GPT-4o-mini).
Стоимость: ~$0.006 за чек (37K tokens input).

Использование:
    from vision_receipt import recognize_receipt
    result = recognize_receipt("path/to/receipt.jpg")
    # result = {"store": "Ozon", "date": "2025-11-25", "items": [...], "delivery": {...}, "total": 7076.00}
"""

import json
import logging
import os
import re

from services.vision_router import call_vision_with_fallback

log = logging.getLogger(__name__)

# Модель для распознавания (gpt-4o-mini — дёшево и достаточно)
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o-mini")

RECEIPT_PROMPT = """Распознай кассовый чек на фото. Верни ТОЛЬКО валидный JSON (без markdown, без ```):
{
  "store": "название магазина/сервиса",
  "date": "YYYY-MM-DD",
  "items": [
    {"name": "точное название товара с чека", "qty": 1, "price": 123.45}
  ],
  "delivery": {"name": "Доставка", "price": 0.00},
  "total": 1234.56
}

Правила:
- Каждый товар — отдельный элемент в items
- Доставку выделяй отдельно в delivery И в items
- price — цена за единицу, qty — количество
- Если qty > 1, price = цена за 1 шт (не за все)
- total — итоговая сумма чека (ИТОГ)
- Дату бери из чека, формат YYYY-MM-DD
- Если магазин не определён, пиши "Неизвестный"
- Если доставки нет, delivery.price = 0
- Только JSON, ничего больше"""


def recognize_receipt(image_path: str, model: str = None) -> dict:
    """
    Распознаёт чек с фото через Vision router:
    OpenAI -> Gemini -> xAI.
    
    Args:
        image_path: путь к изображению чека
        model: модель (по умолчанию gpt-4o-mini)
    
    Returns:
        dict с полями: store, date, items, delivery, total
        При ошибке возвращает {"error": "описание"}
    """
    if not os.path.exists(image_path):
        return {"error": f"Файл не найден: {image_path}"}
    
    model = model or VISION_MODEL
    
    try:
        response = call_vision_with_fallback(
            image_path,
            RECEIPT_PROMPT,
            openai_model=model,
            max_tokens=2000,
        )
        text = response["text"].strip()
        
        # Убираем markdown-обёртку если есть
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        
        result = json.loads(text)
        if response.get("usage"):
            result['_tokens'] = response["usage"]
        result['_model'] = response.get("model", model)
        result['_provider'] = response.get("provider", "openai")
        return result
        
    except json.JSONDecodeError as e:
        log.error(f"Vision API вернул невалидный JSON: {text[:200]}")
        return {"error": f"Невалидный JSON от API: {str(e)}", "raw": text[:500]}
    except Exception as e:
        log.error(f"Vision receipt error: {e}")
        return {"error": str(e)}


def format_receipt_response(result: dict) -> str:
    """Форматирует результат распознавания для Telegram."""
    if "error" in result:
        return f"❌ Ошибка распознавания: {result['error']}"
    
    parts = ["🧾 Чек распознан"]
    
    if result.get("store"):
        parts.append(f"🏪 Магазин: {result['store']}")
    if result.get("date"):
        parts.append(f"📅 Дата: {result['date']}")
    
    items = [i for i in result.get("items", []) if not _is_delivery(i)]
    delivery_items = [i for i in result.get("items", []) if _is_delivery(i)]
    
    if items:
        parts.append(f"📦 Товары ({len(items)}):")
        for item in items:
            price = item.get("price", 0)
            qty = item.get("qty", 1)
            qty_str = f" × {qty}" if qty > 1 else ""
            parts.append(f"  • {item['name']} — {price:.2f} ₽{qty_str}")
    
    # Доставка
    delivery = result.get("delivery", {})
    dl_price = delivery.get("price", 0)
    if not dl_price and delivery_items:
        dl_price = sum(d.get("price", 0) for d in delivery_items)
    if dl_price and dl_price > 0:
        parts.append(f"\n🚚 Доставка: {dl_price:.2f} ₽")
    
    if result.get("total"):
        parts.append(f"\n💰 Итого: {result['total']:.2f} ₽")
    
    if not items and not dl_price:
        parts.append("Товары: не найдены")
    
    return "\n".join(parts)


def _is_delivery(item: dict) -> bool:
    """Проверяет, является ли позиция доставкой."""
    name = (item.get("name") or "").lower()
    keywords = ["доставк", "курьер", "shipping", "delivery", "почт", "postage", "транспорт"]
    return any(kw in name for kw in keywords)


# CLI
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = recognize_receipt(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print()
        print(format_receipt_response(result))
    else:
        print("Usage: python3 vision_receipt.py <image_path>")
