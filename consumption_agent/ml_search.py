#!/usr/bin/env python3
"""Поиск товаров из Memory Lane на сайтах производителей и маркетплейсах.

Интеграция с telegram_bot.py:
- При выводе /ml_last добавляется InlineKeyboardButton "🔍 Искать"
- При нажатии запускается web_search по названию товара + бренд
- Результаты: ссылки на Ozon, Яндекс.Маркет, Wildberries, официальные сайты
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')

# Поисковые шаблоны для разных типов товаров
SEARCH_TEMPLATES = {
    'одежда': '{name} {brand} купить',
    'обувь': '{name} {brand} купить',
    'техника': '{name} {brand} купить официальный сайт',
    'мебель': '{name} {brand} купить',
    'косметика': '{name} {brand} купить',
    'аксессуары': '{name} {brand} купить',
    'default': '{name} {brand} купить',
}

# Маркетплейсы для поиска
MARKETPLACES = {
    'ozon': 'https://www.ozon.ru/search/?text={query}',
    'yandex_market': 'https://market.yandex.ru/search?text={query}',
    'wildberries': 'https://www.wildberries.ru/catalog/0/search.aspx?search={query}',
}


def get_ml_item(item_id: int) -> Optional[Dict]:
    """Получает товар из Memory Lane по ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('''
            SELECT id, name, brand, category, description, 
                   style_tags, liked, photo_path, created_at
            FROM memory_lane_items
            WHERE id = ? AND deleted_at IS NULL
        ''', (item_id,)).fetchone()
        
        if not row:
            return None
        
        return {
            'id': row['id'],
            'name': row['name'],
            'brand': row['brand'],
            'category': row['category'],
            'description': row['description'],
            'style_tags': json.loads(row['style_tags'] or '[]'),
            'liked': row['liked'],
            'photo_path': row['photo_path'],
            'created_at': row['created_at'],
        }
    finally:
        conn.close()


def build_search_query(item: Dict) -> str:
    """Формирует поисковый запрос для товара."""
    name = item.get('name', '')
    brand = item.get('brand', '')
    category = item.get('category', '')
    
    # Определяем шаблон по категории
    template = SEARCH_TEMPLATES.get(category, SEARCH_TEMPLATES['default'])
    
    query = template.format(
        name=name,
        brand=brand or ''
    ).strip()
    
    return query


def generate_marketplace_links(query: str) -> Dict[str, str]:
    """Генерирует ссылки на маркетплейсы."""
    import urllib.parse
    encoded = urllib.parse.quote(query)
    
    return {
        'ozon': MARKETPLACES['ozon'].format(query=encoded),
        'yandex_market': MARKETPLACES['yandex_market'].format(query=encoded),
        'wildberries': MARKETPLACES['wildberries'].format(query=encoded),
    }


def format_search_result(item: Dict, links: Dict[str, str]) -> str:
    """Форматирует результат поиска для Telegram."""
    name = item.get('name', 'Без названия')
    brand = item.get('brand', '')
    category = item.get('category', 'Без категории')
    
    lines = [
        f"🔍 *Поиск: {name}*",
        f"Бренд: {brand or 'не указан'}",
        f"Категория: {category}",
        "",
        "*Где купить:*",
        f"[🛒 Ozon]({links['ozon']})",
        f"[🛒 Яндекс.Маркет]({links['yandex_market']})",
        f"[🛒 Wildberries]({links['wildberries']})",
    ]
    
    return '\n'.join(lines)


def set_reminder(item_id: int, days: Optional[int] = None, months: Optional[int] = None) -> bool:
    """Устанавливает напоминание о товаре.
    
    Args:
        item_id: ID товара в Memory Lane
        days: напомнить через N дней
        months: напомнить через N месяцев
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        # Создаём таблицу напоминаний если нет
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ml_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL REFERENCES memory_lane_items(id),
                remind_at TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                deleted_at TEXT
            )
        ''')
        
        # Вычисляем дату напоминания
        now = datetime.now()
        if days:
            remind_at = now + timedelta(days=days)
        elif months:
            remind_at = now + timedelta(days=months * 30)
        else:
            return False
        
        conn.execute('''
            INSERT INTO ml_reminders (item_id, remind_at)
            VALUES (?, ?)
        ''', (item_id, remind_at.isoformat()))
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Ошибка установки напоминания: {e}")
        return False
    finally:
        conn.close()


def check_reminders() -> List[Dict]:
    """Проверяет просроченные напоминания."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute('''
            SELECT r.id, r.item_id, r.remind_at, m.name, m.brand, m.photo_path
            FROM ml_reminders r
            JOIN memory_lane_items m ON m.id = r.item_id
            WHERE r.status = 'pending'
              AND r.remind_at <= datetime('now')
              AND r.deleted_at IS NULL
            ORDER BY r.remind_at
        ''').fetchall()
        
        return [dict(row) for row in rows]
    finally:
        conn.close()


def search_item(item_id: int) -> Optional[str]:
    """Основная функция поиска товара.
    
    Returns:
        Markdown-строка с результатами поиска или None
    """
    item = get_ml_item(item_id)
    if not item:
        return None
    
    query = build_search_query(item)
    links = generate_marketplace_links(query)
    
    return format_search_result(item, links)


if __name__ == '__main__':
    # Тест
    if len(sys.argv) > 1:
        item_id = int(sys.argv[1])
        result = search_item(item_id)
        if result:
            print(result)
        else:
            print("Товар не найден")
    else:
        print("Usage: python3 ml_search.py <item_id>")
