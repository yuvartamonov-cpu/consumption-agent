import json
import os
from openai import OpenAI
import logging

log = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def get_categories(conn):
    rows = conn.execute("SELECT id, slug, name FROM categories").fetchall()
    return [{"id": r[0], "slug": r[1], "name": r[2]} for r in rows]

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
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content[7:-3].strip()
        elif content.startswith("```"):
            content = content[3:-3].strip()
        return json.loads(content)
    except Exception as e:
        log.warning(f"AI category suggestion failed: {e}")
        return {"action": "existing", "category_id": "cat_other", "confidence": 0}
