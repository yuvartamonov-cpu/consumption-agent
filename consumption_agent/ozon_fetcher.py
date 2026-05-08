#!/usr/bin/env python3
"""Скачивает страницу заказов Ozon через переданные куки и парсит её."""
import json
import re
import sys
import os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(SCRIPT_DIR, '.ozon_cookies.txt')


def parse_cookie_string(cookie_str):
    """Парсит cookie string в словарь."""
    cookies = {}
    for part in cookie_str.split(';'):
        part = part.strip()
        if '=' in part:
            key, val = part.split('=', 1)
            cookies[key.strip()] = val.strip()
    return cookies


async def fetch_orders_with_curl():
    """Скачивает страницу заказов через curl с куками."""
    import subprocess

    with open(COOKIE_FILE) as f:
        cookie_str = f.read().strip()

    # Формируем cookie header
    cookies = parse_cookie_string(cookie_str)
    cookie_header = '; '.join(f'{k}={v}' for k, v in cookies.items())

    # Headers как у настоящего браузера
    headers = [
        'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        f'Cookie: {cookie_header}',
        'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language: ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    ]

    # Пробуем разные варианты URL
    urls = [
        'https://www.ozon.ru/my/orderlist',
        'https://ozon.ru/my/orderlist',
    ]

    for url in urls:
        cmd = ['curl', '-s', '-L'] + [h for header in headers for h in ('-H', header)] + [url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0 and len(result.stdout) > 1000:
            save_debug_html(result.stdout, 'orders_page')
            return result.stdout

    return None


def save_debug_html(html, prefix='ozon'):
    """Сохраняет HTML для отладки."""
    debug_dir = os.path.join(SCRIPT_DIR, 'debug')
    os.makedirs(debug_dir, exist_ok=True)
    path = os.path.join(debug_dir, f'{prefix}_{datetime.now().strftime("%H%M%S")}.html')
    with open(path, 'w') as f:
        f.write(html)
    print(f"  💾 HTML сохранён: {path}")
    return path


def parse_orders(html):
    """Извлекает заказы из HTML."""
    orders = []

    # Способ 1: ищем JSON-данные в script тегах (Ozon часто так грузит)
    json_blocks = re.findall(
        r'<script[^>]*type="application/json"[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )

    if json_blocks:
        try:
            data = json.loads(json_blocks[0])
            # Пытаемся найти заказы в props
            props = data.get('props', {}).get('pageProps', {})
            # Ozon может прятать заказы глубоко в стейте
            print(f"  📦 Найден JSON-блок __NEXT_DATA__")
            # Сохраним для анализа
            with open(os.path.join(SCRIPT_DIR, 'debug', 'next_data.json'), 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except:
            pass

    # Способ 2: ищем блоки товаров в HTML
    # Ищем структуру с названием товара
    items = re.findall(
        r'<a[^>]*href="[^"]*"[^>]*class="[^"]*name[^"]*"[^>]*>([^<]+)</a>',
        html
    )
    for item in items:
        item = item.strip()
        if item and len(item) > 3:
            orders.append({'name': item})

    # Способ 3: ищем названия в любых a-тегах внутри виджетов заказов
    if not orders:
        # Ищем все тексты между тегами <a> внутри div с order
        order_blocks = re.findall(
            r'<div[^>]*(?:order|basket|purchase)[^>]*>.*?<a[^>]*>([^<]{5,100})</a>',
            html, re.DOTALL | re.IGNORECASE
        )
        for name in order_blocks:
            name = name.strip()
            if name and len(name) > 5 and 'ozon' not in name.lower():
                orders.append({'name': name})

    return orders


async def main():
    print("📡 Скачиваю страницу заказов Ozon...")

    if not os.path.exists(COOKIE_FILE):
        print("❌ Нет файла с куками. Скинь куки из браузера.")
        return

    html = await fetch_orders_with_curl()

    if not html:
        print("❌ Не удалось загрузить страницу")
        return

    print(f"  ✅ Загружено: {len(html)} символов")

    # Проверяем, не выкинуло ли на логин
    if 'login' in html.lower()[:2000]:
        print("⚠️ Куки не работают — страница логина. Нужны свежие куки.")
        save_debug_html(html, 'login_page')
        return

    # Проверяем наличие заказов
    if 'orderlist' in html.lower()[:500]:
        print("  ✅ Это страница заказов")

    orders = parse_orders(html)

    print(f"\n📊 Найдено позиций: {len(orders)}")
    for o in orders[:15]:
        print(f"  • {o['name'][:80]}")

    # Результат
    result = {
        'count': len(orders),
        'orders': orders[:30],
        'fetched_at': datetime.now().isoformat(),
    }

    # Сохраняем результат
    out_path = os.path.join(SCRIPT_DIR, 'ozon_orders_result.json')
    with open(out_path, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Результат сохранён: {out_path}")


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
