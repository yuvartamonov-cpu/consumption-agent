import sqlite3
import json
import logging
from services.llm_router import call_text_with_fallback
from consumption.db import DB_PATH, connect as db_connect

log = logging.getLogger(__name__)

def init_retailers_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS category_retailers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            store_name TEXT NOT NULL,
            url_template TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(topic, store_name)
        )
    ''')
    conn.commit()
    conn.close()

def discover_retailers_for_topic(topic: str) -> list[dict]:
    """Использует LLM для поиска популярных интернет-магазинов для категории."""
    prompt = f"""Найди 3-5 самых популярных специализированных интернет-магазинов или прямых продавцов в России для категории товаров "{topic}".
    Не включай универсальные маркетплейсы (Ozon, Wildberries, Яндекс Маркет, AliExpress, Amazon).
    Верни строго валидный JSON-массив объектов:
    [
      {{"store_name": "Название магазина", "url_template": "https://example.com/search?q={{query}}" }}
    ]
    В url_template используй {{query}} вместо поискового запроса.
    Только JSON, ничего кроме JSON."""
    
    try:
        response = call_text_with_fallback(
            system_prompt=None,
            user_prompt=prompt,
            openai_model="gpt-4o-mini",
            response_mime_type="application/json",
            max_tokens=400,
            temperature=0.1
        )
        content = response["text"].strip()
        if content.startswith("```json"):
            content = content[7:-3].strip()
        elif content.startswith("```"):
            content = content[3:-3].strip()
        raw = json.loads(content)
        
        if not isinstance(raw, list):
            return []
            
        return [r for r in raw if isinstance(r, dict) and "store_name" in r and "url_template" in r]
    except Exception as e:
        log.warning(f"Failed to discover retailers for topic {topic}: {e}")
        return []

def get_or_discover_retailers(topic: str) -> list[dict]:
    init_retailers_db()
    conn = db_connect()
    rows = conn.execute("SELECT store_name, url_template FROM category_retailers WHERE topic = ?", (topic,)).fetchall()
    
    if not rows:
        log.info(f"No retailers found for topic '{topic}', discovering via LLM...")
        new_retailers = discover_retailers_for_topic(topic)
        for r in new_retailers:
            try:
                conn.execute(
                    "INSERT INTO category_retailers (topic, store_name, url_template) VALUES (?, ?, ?)",
                    (topic, r['store_name'], r['url_template'])
                )
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        rows = conn.execute("SELECT store_name, url_template FROM category_retailers WHERE topic = ?", (topic,)).fetchall()
        
    conn.close()
    return [{"store_name": r["store_name"], "url_template": r["url_template"]} for r in rows]
