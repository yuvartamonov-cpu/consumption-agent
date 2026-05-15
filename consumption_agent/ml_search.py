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
    """Формирует поисковый запрос для товара.
    
    Использует сгенерированный нейросетью запрос если есть,
    иначе формирует из названия + бренда.
    """
    # Если есть сгенерированный запрос от Vision API
    if item.get('search_query'):
        return item['search_query']
    
    # Если есть артикул — ищем по нему (самый точный способ)
    if item.get('article'):
        return f"{item['article']} {item.get('brand', '')}".strip()
    
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
    """Дополняет товар описанием по фото через Vision API (OpenAI).
    
    Использует GPT-4o-mini для распознавания товара по фото.
    Возвращает структурированное описание: название, бренд, категория, артикул.
    """
    photo_path = item.get('photo_path')
    if not photo_path or not os.path.exists(photo_path):
        return item
    
    # Если уже есть название и бренд — не перезаписываем
    if item.get('name') and item.get('brand'):
        return item
    
    try:
        import base64
        import openai
        
        # Читаем фото
        with open(photo_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Запрос к OpenAI Vision
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Ты эксперт по распознаванию товаров. Опиши товар на фото для поиска в интернет-магазинах."
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Опиши товар на фото. Нужно:\n1. Точное название (на русском)\n2. Бренд (если виден)\n3. Категория (одежда, обувь, техника и т.д.)\n4. Артикул/модель (если виден)\n5. Ключевые признаки для поиска\n\nОтветь в формате JSON:\n{\n  \"name\": \"название\",\n  \"brand\": \"бренд\",\n  \"category\": \"категория\",\n  \"article\": \"артикул\",\n  \"search_query\": \"запрос для поиска\"\n}"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=500,
            temperature=0.3
        )
        
        # Парсим ответ
        content = response.choices[0].message.content
        # Извлекаем JSON из ответа
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            vision_info = json.loads(json_match.group())
            
            if not item.get('name') and vision_info.get('name'):
                item['name'] = vision_info['name']
            if not item.get('brand') and vision_info.get('brand'):
                item['brand'] = vision_info['brand']
            if not item.get('category') and vision_info.get('category'):
                item['category'] = vision_info['category']
            if vision_info.get('search_query'):
                item['search_query'] = vision_info['search_query']
            if vision_info.get('article'):
                item['article'] = vision_info['article']
        
        return item
    except Exception as e:
        print(f"Ошибка распознавания фото: {e}")
        return item


async def search_by_image_yandex(photo_path: str) -> Optional[Dict]:
    """Поиск по изображению через Яндекс.Картинки.
    
    Returns:
        Dict с title, url, price, store или None
    """
    try:
        import requests
        
        # Загружаем фото на Яндекс.Картинки
        url = "https://yandex.ru/images/search"
        
        with open(photo_path, 'rb') as f:
            files = {'upfile': ('image.jpg', f, 'image/jpeg')}
            response = requests.post(
                url,
                files=files,
                params={'rpt': 'imageview', 'format': 'json'},
                timeout=30
            )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        
        # Ищем товары в результатах
        if 'blocks' in data:
            for block in data['blocks']:
                if block.get('type') == 'products':
                    products = block.get('products', [])
                    if products:
                        best = products[0]
                        return {
                            'title': best.get('title', 'Найдено по фото'),
                            'url': best.get('url', ''),
                            'price': str(best.get('price', '')),
                            'store': best.get('shop', 'Яндекс.Маркет'),
                        }
        
        return None
    except Exception as e:
        print(f"Ошибка поиска по фото: {e}")
        return None


async def search_web_best_match(query: str, photo_path: Optional[str] = None) -> Optional[Dict]:
    """Ищет лучшее соответствие через web_search или по фото.
    
    Returns:
        Dict с title, url, price, store или None
    """
    # Сначала пробуем поиск по фото (если есть)
    if photo_path and os.path.exists(photo_path):
        result = await search_by_image_yandex(photo_path)
        if result:
            return result
    
    # Fallback на текстовый поиск
    try:
        from web_search import web_search
        
        # Ищем с приоритетом на маркетплейсы
        search_queries = [
            query + ' site:ozon.ru',
            query + ' site:wildberries.ru',
            query + ' site:market.yandex.ru',
            query + ' купить',
        ]
        
        for sq in search_queries:
            results = web_search(sq, count=3)
            if results:
                for r in results:
                    title = r.get('title', '')
                    url = r.get('url', '')
                    # Ищем цену
                    price_match = re.search(r'(\d[\d\s]*)\s*(?:₽|руб|RUB)', title)
                    price = price_match.group(1).replace(' ', '') if price_match else None
                    
                    if any(s in url for s in ['ozon', 'wildberries', 'market.yandex']):
                        return {
                            'title': title,
                            'url': url,
                            'price': price,
                            'store': 'Ozon' if 'ozon' in url else 'Wildberries' if 'wildberries' in url else 'Яндекс.Маркет'
                        }
        
        return None
    except Exception as e:
        print(f"Ошибка web поиска: {e}")
        return None


def escape_html(text: Optional[str]) -> str:
    """Экранирует HTML-символы."""
    if not text:
        return ''
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
        price_str = f"{best_match.get('price', '')} ₽" if best_match.get('price') else 'цена не указана'
        title = escape_html((best_match.get('title') or 'Найдено по фото')[:80])
        store = escape_html(best_match.get('store', 'Маркетплейс'))
        url = best_match.get('url', '')
        lines.extend([
            "",
            f"<b>Лучшее предложение:</b>",
            f"🛒 {store}",
            f"📦 {title}",
            f"💰 {price_str}",
        ])
        if url:
            lines.append(f"<a href='{url}'>🔗 Перейти к товару</a>")
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
    
    # Ищем лучшее соответствие через web или по фото
    photo_path = item.get('photo_path')
    best_match = await search_web_best_match(query, photo_path)
    
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
