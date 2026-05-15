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
            SELECT id, name, brand, topic, description, 
                   style_tags, media_asset_id, created_at
            FROM memory_lane_items
            WHERE id = ?
        ''', (item_id,)).fetchone()
        
        if not row:
            return None
        
        # Получаем путь к фото
        photo_path = None
        if row['media_asset_id']:
            ma_row = conn.execute(
                'SELECT file_path FROM media_assets WHERE id = ?',
                (row['media_asset_id'],)
            ).fetchone()
            if ma_row:
                photo_path = ma_row[0]
        
        return {
            'id': row['id'],
            'name': row['name'],
            'brand': row['brand'],
            'category': row['topic'],  # topic используем как category
            'description': row['description'],
            'style_tags': json.loads(row['style_tags'] or '[]'),
            'photo_path': photo_path,
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


async def enrich_item_from_photo(item: Dict) -> Dict:
    """Дополняет товар описанием по фото через Vision API.
    
    Если у товара есть фото и нет описания/названия — распознаёт.
    """
    photo_path = item.get('photo_path')
    if not photo_path or not os.path.exists(photo_path):
        return item
    
    # Если уже есть название и бренд — не перезаписываем
    if item.get('name') and item.get('brand'):
        return item
    
    try:
        # Используем Vision API для распознавания
        from vision_item import enrich_memory_lane
        vision_info = enrich_memory_lane(photo_path, item.get('caption', ''))
        
        if vision_info:
            if not item.get('name') and vision_info.get('name'):
                item['name'] = vision_info['name']
            if not item.get('brand') and vision_info.get('brand'):
                item['brand'] = vision_info['brand']
            if not item.get('description') and vision_info.get('description'):
                item['description'] = vision_info['description']
            if not item.get('category') and vision_info.get('category'):
                item['category'] = vision_info['category']
        
        return item
    except Exception as e:
        print(f"Ошибка распознавания фото: {e}")
        return item


async def search_web_best_match(query: str) -> Optional[Dict]:
    """Ищет лучшее соответствие через web_search.
    
    Returns:
        Dict с title, url, price, store или None
    """
    try:
        # Используем web_search для поиска
        from web_search import web_search
        results = web_search(query + ' купить цена', count=5)
        
        if not results:
            return None
        
        # Выбираем лучший результат
        best = None
        for r in results:
            title = r.get('title', '')
            url = r.get('url', '')
            # Ищем цену в title
            price_match = re.search(r'(\d[\d\s]*)\s*(?:₽|руб|RUB)', title)
            price = price_match.group(1).replace(' ', '') if price_match else None
            
            if 'ozon' in url or 'wildberries' in url or 'market.yandex' in url:
                if best is None or (price and (best.get('price') is None or int(price) < int(best['price']))):
                    best = {
                        'title': title,
                        'url': url,
                        'price': price,
                        'store': 'Ozon' if 'ozon' in url else 'Wildberries' if 'wildberries' in url else 'Яндекс.Маркет'
                    }
        
        return best
    except Exception as e:
        print(f"Ошибка web поиска: {e}")
        return None


def escape_html(text: str) -> str:
    """Экранирует HTML-символы."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_search_result(item: Dict, links: Dict[str, str], best_match: Optional[Dict] = None) -> str:
    """Форматирует результат поиска для Telegram (HTML)."""
    name = escape_html(item.get('name', 'Без названия'))
    brand = escape_html(item.get('brand', ''))
    category = escape_html(item.get('category', 'Без категории'))
    
    lines = [
        f"🔍 <b>Поиск: {name}</b>",
        f"Бренд: {brand or 'не указан'}",
        f"Категория: {category}",
    ]
    
    if best_match:
        price_str = f"{best_match['price']} ₽" if best_match.get('price') else 'цена не указана'
        title = escape_html(best_match['title'][:80])
        store = escape_html(best_match['store'])
        lines.extend([
            "",
            f"<b>Лучшее предложение:</b>",
            f"🛒 {store}",
            f"📦 {title}",
            f"💰 {price_str}",
            f"<a href='{best_match['url']}'>🔗 Перейти к товару</a>",
        ])
    else:
        lines.extend([
            "",
            "<b>Где купить:</b>",
            f"<a href='{links['ozon']}'>🛒 Ozon</a>",
            f"<a href='{links['yandex_market']}'>🛒 Яндекс.Маркет</a>",
            f"<a href='{links['wildberries']}'>🛒 Wildberries</a>",
        ])
    
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


async def search_item(item_id: int) -> Optional[str]:
    """Основная функция поиска товара.
    
    1. Получает товар из БД
    2. Если нет описания — распознаёт по фото через Vision API
    3. Ищет лучшее предложение через web_search
    
    Returns:
        HTML-строка с результатами поиска или None
    """
    item = get_ml_item(item_id)
    if not item:
        return None
    
    # Распознаём по фото если нет данных
    item = await enrich_item_from_photo(item)
    
    query = build_search_query(item)
    links = generate_marketplace_links(query)
    
    # Ищем лучшее соответствие через web
    best_match = await search_web_best_match(query)
    
    result = format_search_result(item, links, best_match)
    
    # Отправляем результат на почту для истории
    try:
        await send_search_result_email(item, result, best_match)
    except Exception as e:
        print(f"Ошибка отправки email: {e}")
    
    return result


async def send_search_result_email(item: Dict, result_html: str, best_match: Optional[Dict]):
    """Отправляет результат поиска на почту."""
    import aiosmtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    subject = f"🔍 Поиск: {item.get('name', 'Товар')}"
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = 'consumption-agent@local'
    msg['To'] = 'yu.v.artamonov@gmail.com'
    
    # Plain text версия
    text = f"""
Поиск товара из Memory Lane

Название: {item.get('name', 'Не указано')}
Бренд: {item.get('brand', 'Не указан')}
Категория: {item.get('category', 'Не указана')}

Результат поиска:
{best_match.get('title', 'Не найдено') if best_match else 'Не найдено'}
Цена: {best_match.get('price', 'Не указана') if best_match else 'Не указана'}
Магазин: {best_match.get('store', 'Не указан') if best_match else 'Не указан'}
Ссылка: {best_match.get('url', 'Нет') if best_match else 'Нет'}

---
Отправлено ботом Consumption Agent
"""
    
    msg.attach(MIMEText(text, 'plain'))
    msg.attach(MIMEText(result_html, 'html'))
    
    # Отправка (опционально, если настроен SMTP)
    # await aiosmtplib.send(msg, hostname='smtp.gmail.com', port=587, ...)


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
