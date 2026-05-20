import json
import os
import logging

from services.llm_router import call_text_with_fallback

log = logging.getLogger(__name__)

def get_categories(conn):
    rows = conn.execute("SELECT id, slug, name FROM categories").fetchall()
    return [{"id": r[0], "slug": r[1], "name": r[2]} for r in rows]


def suggest_category_options(conn, item_name, rejected_category_ids=None, limit=3):
    if rejected_category_ids is None:
        rejected_category_ids = []

    categories = get_categories(conn)
    valid_cats = [c for c in categories if c["id"] not in rejected_category_ids]
    if not valid_cats:
        return []

    prompt = f"""Мы классифицируем товар после распознавания чека.
Название товара: "{item_name}"

Список доступных существующих категорий (ID и Название):
{json.dumps(valid_cats, ensure_ascii=False, indent=2)}

Задача:
1. Выбери до {limit} наиболее подходящих существующих категорий.
2. Не предлагай новые категории.
3. Для каждого варианта укажи уверенность от 0 до 100.
4. Верни строго валидный JSON-массив объектов:
[
  {{"category_id": "id", "confidence": 0-100}}
]

Только JSON, ничего кроме JSON."""

    try:
        response = call_text_with_fallback(
            system_prompt=None,
            user_prompt=prompt,
            openai_model="gpt-4o-mini",
            response_mime_type="application/json",
            max_tokens=400,
            temperature=0.0
        )
        content = response["text"].strip()
        if content.startswith("```json"):
            content = content[7:-3].strip()
        elif content.startswith("```"):
            content = content[3:-3].strip()
        raw = json.loads(content)
        if not isinstance(raw, list):
            return []

        cat_map = {c["id"]: c for c in valid_cats}
        out = []
        seen = set()
        for row in raw:
            if not isinstance(row, dict):
                continue
            category_id = row.get("category_id")
            if not category_id or category_id in seen or category_id not in cat_map:
                continue
            seen.add(category_id)
            confidence = row.get("confidence", 0)
            try:
                confidence = int(confidence)
            except Exception:
                confidence = 0
            out.append({
                "category_id": category_id,
                "category_name": cat_map[category_id]["name"],
                "confidence": max(0, min(100, confidence)),
            })
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        log.warning(f"AI category options failed: {e}")
        return []

def suggest_category(conn, item_name, rejected_category_ids=None):
    if rejected_category_ids is None:
        rejected_category_ids = []
        
    categories = get_categories(conn)
    valid_cats = [c for c in categories if c["id"] not in rejected_category_ids]
    
    prompt = f"""Мы классифицируем товар для базы инвентаря.
Название товара: "{item_name}"

Список доступных существующих категорий (ID и Название):
{json.dumps(valid_cats, ensure_ascii=False, indent=2)}

Задача:
1. Выбери наиболее подходящую категорию из существующих.
2. Если ни одна из существующих совсем не подходит, предложи создать новую (придумай короткий slug на английском и name на русском).
3. Оцени свою уверенность от 0 до 100.
4. Верни строго валидный JSON:
{{
  "action": "existing" или "new",
  "category_id": "id существующей категории" (если action=existing),
  "new_category_slug": "slug" (если action=new),
  "new_category_name": "Название" (если action=new),
  "confidence": число от 0 до 100
}}"""

    try:
        response = call_text_with_fallback(
            system_prompt=None,
            user_prompt=prompt,
            openai_model="gpt-4o-mini",
            response_mime_type="application/json",
            max_tokens=250,
            temperature=0.0
        )
        content = response["text"].strip()
        if content.startswith("```json"):
            content = content[7:-3].strip()
        elif content.startswith("```"):
            content = content[3:-3].strip()
        return json.loads(content)
    except Exception as e:
        log.warning(f"AI category suggestion failed: {e}")
        return {"action": "existing", "category_id": "cat_other", "confidence": 0}
