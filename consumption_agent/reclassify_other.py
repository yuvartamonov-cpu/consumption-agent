import sqlite3
import os
import json

from services.llm_router import call_text_with_fallback

db_path = os.path.join(os.path.dirname(__file__), 'consumption.db')

def get_categories(conn):
    rows = conn.execute("SELECT id, slug, name FROM categories").fetchall()
    return [{"id": r[0], "slug": r[1], "name": r[2]} for r in rows]

def get_other_items(conn):
    # Find category id for 'other'
    row = conn.execute("SELECT id FROM categories WHERE slug='other'").fetchone()
    if not row:
        return [], None
    other_id = row[0]
    items = conn.execute("SELECT id, name FROM items WHERE category_id = ? AND deleted_at IS NULL", (other_id,)).fetchall()
    return [{"id": r[0], "name": r[1]} for r in items], other_id

def reclassify_batch(items, categories):
    prompt = f"""У нас есть следующие категории товаров:
{json.dumps(categories, ensure_ascii=False, indent=2)}

А также список товаров, которые сейчас попали в категорию "Прочее" (other):
{json.dumps(items, ensure_ascii=False, indent=2)}

Для каждого товара определи наиболее подходящую категорию из списка существующих (используй 'id' категории). Если товар совсем никуда не подходит, верни тот же id категории "other" (или null).
Верни ТОЛЬКО валидный JSON-массив объектов вида:
[
  {{"id": item_id, "category_id": new_category_id}}
]
Не пиши ничего кроме JSON."""

    response = call_text_with_fallback(
        system_prompt=None,
        user_prompt=prompt,
        openai_model="gpt-4o-mini",
        response_mime_type="application/json",
        max_tokens=1200,
        temperature=0.0,
    )

    content = response["text"].strip()
    if content.startswith("```json"):
        content = content[7:-3].strip()
    elif content.startswith("```"):
        content = content[3:-3].strip()
        
    try:
        return json.loads(content)
    except Exception as e:
        print(f"Failed to parse JSON: {e}")
        return []

def main():
    conn = sqlite3.connect(db_path)
    categories = get_categories(conn)
    items, other_id = get_other_items(conn)
    print(f"Found {len(items)} items in 'other' category (id: {other_id})")
    
    if not items:
        return

    batch_size = 50
    updated_count = 0
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        print(f"Processing batch {i} to {i+len(batch)}...")
        updates = reclassify_batch(batch, categories)
        
        for up in updates:
            item_id = up.get("id")
            new_cat_id = up.get("category_id")
            if item_id and new_cat_id and new_cat_id != other_id:
                conn.execute("UPDATE items SET category_id = ? WHERE id = ?", (new_cat_id, item_id))
                updated_count += 1
        
        conn.commit()
    
    print(f"Successfully reclassified {updated_count} items out of {len(items)}")
    conn.close()

if __name__ == "__main__":
    main()
