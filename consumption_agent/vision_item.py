"""
vision_item.py — Распознавание предметов/товаров на фото через Vision API.
Работает для: Memory Lane, /add_item, классификация фото.

Стоимость: ~$0.004-0.006 за фото (gpt-4o-mini).
"""

import json
import logging
import os
import re

from services.vision_router import call_vision_with_fallback

log = logging.getLogger(__name__)

VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o-mini")

ITEM_PROMPT = """Посмотри на фото и определи, что на нём изображено.
Верни ТОЛЬКО валидный JSON (без markdown, без ```):
{
  "type": "item|food|interior|clothing|receipt|tag|other",
  "name": "краткое название предмета на русском",
  "brand": "бренд если видно, иначе null",
  "category": "категория: одежда/обувь/техника/мебель/еда/интерьер/косметика/аксессуары/бытовая техника/другое",
  "color": "основной цвет если определяется, иначе null",
  "material": "материал если виден, иначе null",
  "style_tags": ["тег1", "тег2"],
  "description": "краткое описание (1-2 предложения)",
  "estimated_price_rub": null
}

Правила:
- type: "receipt" если это кассовый чек, "tag" если бирка одежды, "clothing" если одежда, "food" если еда, "interior" если интерьер/мебель, "item" для остального
- name: конкретное название (не "предмет", а "синий пиджак" или "кофемолка Bosch")
- style_tags: 2-5 тегов для описания стиля/характера (для одежды: casual/formal/sport, для еды: домашняя/ресторан)
- estimated_price_rub: примерная цена в рублях если можно оценить, иначе null
- Только JSON"""

CLASSIFY_PROMPT = """Что на фото? Ответь ОДНИМ словом:
- receipt (кассовый чек, квитанция)
- tag (бирка одежды, ценник, этикетка)
- clothing (одежда, обувь)
- food (еда, напитки)
- interior (интерьер, мебель, декор)
- tech (техника, гаджеты)
- item (любой другой предмет/товар)
- other (не предмет)

Ответь ОДНИМ словом."""


def _call_vision(image_path: str, prompt: str, model: str = None, max_tokens: int = 1000) -> str:
    """Вызывает Vision API и возвращает текст ответа."""
    response = call_vision_with_fallback(
        image_path,
        prompt,
        openai_model=model or VISION_MODEL,
        max_tokens=max_tokens,
    )
    return response["text"].strip()


def _call_vision_with_timeout(image_path: str, prompt: str, model: str = None, max_tokens: int = 1000, timeout: float = 30.0):
    """Запускает _call_vision в отдельном процессе с жёстким таймаутом.
    
    Если процесс не завершился за timeout секунд — убивается.
    Возвращает (result, timed_out).
    """
    import multiprocessing
    import time
    
    manager = multiprocessing.Manager()
    result_dict = manager.dict()
    
    def worker(path, pr, m, mt, rd):
        try:
            result = _call_vision(path, pr, m, mt)
            rd['status'] = 'ok'
            rd['result'] = result
        except Exception as e:
            rd['status'] = 'error'
            rd['error'] = str(e)
    
    process = multiprocessing.Process(
        target=worker,
        args=(image_path, prompt, model, max_tokens, result_dict)
    )
    process.start()
    
    # Ждём с проверкой каждые 0.5 сек
    elapsed = 0.0
    while process.is_alive() and elapsed < timeout:
        time.sleep(0.5)
        elapsed += 0.5
    
    if process.is_alive():
        log.warning(f"Vision process timeout after {timeout}s for {image_path}")
        process.terminate()
        process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join()
        return None, True
    
    process.join(timeout=1.0)
    
    if result_dict.get('status') == 'ok':
        return result_dict['result'], False
    elif result_dict.get('status') == 'error':
        raise RuntimeError(result_dict.get('error', 'Unknown error'))
    else:
        log.warning(f"Vision process no result for {image_path}")
        return None, True


def classify_photo(image_path: str) -> str:
    """
    Быстрая классификация фото: receipt/tag/clothing/food/interior/tech/item/other.
    Дёшево (~2K tokens).
    """
    try:
        result, timed_out = _call_vision_with_timeout(image_path, CLASSIFY_PROMPT, max_tokens=10, timeout=30.0)
        if timed_out:
            return 'unknown'
        result = result.lower().strip().rstrip('.')
        valid = {'receipt', 'tag', 'clothing', 'food', 'interior', 'tech', 'item', 'other'}
        return result if result in valid else 'other'
    except Exception as e:
        log.error(f"classify_photo failed: {e}")
        return 'unknown'


async def classify_photo_async(image_path: str) -> str:
    """Асинхронная версия classify_photo с жёстким таймаутом 30 сек."""
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, classify_photo, image_path)


def recognize_item(image_path: str, model: str = None) -> dict:
    """
    Полное распознавание предмета на фото.
    Возвращает dict с полями: type, name, brand, category, color, material, style_tags, description.
    """
    try:
        text, timed_out = _call_vision_with_timeout(image_path, ITEM_PROMPT, model=model, timeout=30.0)
        if timed_out:
            return {"error": "timeout", "type": "unknown", "name": "Объект не распознан", "description": "Распознавание не завершилось за 30 секунд"}
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text)
        return result
    except json.JSONDecodeError as e:
        log.error(f"Vision item: invalid JSON: {e}")
        return {"error": f"Invalid JSON: {e}", "type": "unknown"}
    except Exception as e:
        log.error(f"Vision item failed: {e}")
        return {"error": str(e), "type": "unknown"}


async def recognize_item_async(image_path: str, model: str = None) -> dict:
    """Асинхронная версия recognize_item с жёстким таймаутом 30 сек."""
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, recognize_item, image_path, model)


def enrich_memory_lane(image_path: str, caption: str = "") -> dict:
    """
    Обогащает Memory Lane запись данными с фото.
    Возвращает: topic, style_tags, description, name.
    """
    result = recognize_item(image_path)
    if "error" in result:
        return result
    
    return {
        "topic": result.get("category"),
        "style_tags": result.get("style_tags", []),
        "description": result.get("description"),
        "name": result.get("name"),
        "brand": result.get("brand"),
        "color": result.get("color"),
        "type": result.get("type"),
        "estimated_price_rub": result.get("estimated_price_rub"),
    }


# CLI
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        action = sys.argv[2] if len(sys.argv) > 2 else "full"
        
        if action == "classify":
            print(classify_photo(path))
        else:
            result = recognize_item(path)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Usage: python3 vision_item.py <image_path> [classify|full]")
