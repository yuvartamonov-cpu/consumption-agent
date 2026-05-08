#!/usr/bin/env python3
"""
Ozon LK connector via Playwright.
Сохраняет cookie-сессию, чтобы не логиниться каждый раз.
Входит по SMS-коду, если сессии нет.
Выгружает историю заказов.
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(SCRIPT_DIR, '.ozon_cookies.json')
STATE_FILE = os.path.join(SCRIPT_DIR, '.ozon_state.json')


async def login_and_save_session(page):
    """Логин в Ozon через SMS, сохраняет куки."""
    print("🔄 Открываю Ozon...")
    await page.goto('https://www.ozon.ru/', wait_until='domcontentloaded')
    await page.wait_for_timeout(3000)

    # Ищем кнопку "Войти" или "Мой профиль"
    login_btn = await page.query_selector('a[href*="login"], button:has-text("Войти"), a:has-text("Войти")')
    if login_btn:
        await login_btn.click()
        await page.wait_for_timeout(3000)

    # Поле ввода телефона/почты
    print("📞 Жду ввод номера телефона или почты...")
    print("   (скинь сюда, как введёшь)")
    await page.wait_for_url('**/login/**', timeout=60000)
    await page.wait_for_timeout(2000)

    # Пользователь вводит данные — ждём переход к SMS
    print("⌛ Жду, когда придёт SMS-код...")
    # После ввода номера Ozon просит код из SMS
    await page.wait_for_url(lambda url: 'sms' in url.lower() or 'confirm' in url.lower() or 'code' in url.lower(),
                            timeout=90000)

    print("📱 Пришёл SMS-код? Введи его на странице и нажми подтвердить.")
    print("⌛ Жду завершения входа...")

    # Ждём, пока URL сменится на не-логин (успешный вход)
    # или появится аватарка профиля
    try:
        await page.wait_for_url('https://www.ozon.ru/', timeout=60000)
    except:
        # Ищем, что мы больше не на /login/
        try:
            await page.wait_for_function(
                "!window.location.href.includes('login') && !window.location.href.includes('confirm')",
                timeout=60000
            )
        except:
            pass

    # Сохраняем куки
    cookies = await page.context.cookies()
    with open(COOKIE_FILE, 'w') as f:
        json.dump(cookies, f)
    print(f"✅ Сессия сохранена ({len(cookies)} cookies)")

    # Сохраняем стейт
    state = {
        'last_login': datetime.now().isoformat(),
        'cookies_count': len(cookies)
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

    return True


async def load_session(context):
    """Загружает сохранённые куки в контекст браузера."""
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE) as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        print(f"📂 Загружена сессия ({len(cookies)} cookies)")
        return True
    return False


async def fetch_orders(page, limit=20):
    """Парсит страницу 'Мои заказы'."""
    print("📦 Открываю 'Мои заказы'...")
    await page.goto('https://www.ozon.ru/my/orderlist', wait_until='domcontentloaded')
    await page.wait_for_timeout(5000)

    # Ждём появления списка заказов
    try:
        await page.wait_for_selector('[data-widget="orderItem"]', timeout=15000)
    except:
        # Пробуем другой селектор
        try:
            await page.wait_for_selector('.order-item, .order-card, [class*="order"]', timeout=10000)
        except:
            print("⚠️ Не удалось найти заказы на странице")

    # Прокручиваем вниз для загрузки
    for _ in range(3):
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        await page.wait_for_timeout(2000)

    html = await page.content()
    return html


def parse_orders_from_html(html):
    """Извлекает заказы из HTML страницы."""
    orders = []

    # Ищем блоки заказов
    order_blocks = re.findall(
        r'<div[^>]*data-widget="orderItem"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>',
        html, re.DOTALL
    )

    if not order_blocks:
        # Fallback: ищем карточки товаров
        order_blocks = re.findall(
            r'<div[^>]*class="[^"]*order-item[^"]*"[^>]*>(.*?)</div>\s*</div>',
            html, re.DOTALL
        )

    for block in order_blocks[:10]:
        # Извлекаем название товара
        name_match = re.search(r'<span[^>]*class="[^"]*name[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
        name = name_match.group(1).strip() if name_match else ''

        # Цена
        price_match = re.search(r'(\d[\d\s]*)\s*(?:₽|руб)', block)
        price = price_match.group(1).strip().replace(' ', '') if price_match else ''

        # Дата
        date_match = re.search(r'(\d{1,2}\s+(?:январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*\s+\d{4})', block, re.IGNORECASE)
        date = date_match.group(1) if date_match else ''

        # Статус
        status_match = re.search(r'(?:Доставлен|В пути|Ожидает|Отменён|Возврат|Получен)', block)
        status = status_match.group(0) if status_match else ''

        orders.append({
            'name': name,
            'price': price,
            'date': date,
            'status': status,
        })

    return orders


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )

        # Пробуем загрузить сессию
        session_loaded = await load_session(context)
        page = await context.new_page()

        if not session_loaded:
            await login_and_save_session(page)
        else:
            # Проверяем, жива ли сессия
            await page.goto('https://www.ozon.ru/', wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)

            # Проверяем, не выкинуло ли на логин
            if 'login' in page.url:
                print("🔄 Сессия истекла, логинюсь заново...")
                await login_and_save_session(page)

        # Парсим заказы
        html = await fetch_orders(page)
        orders = parse_orders_from_html(html)

        print(f"\n📊 Найдено заказов: {len(orders)}")
        for o in orders[:10]:
            print(f"   [{o['status']}] {o['date']} — {o['name'][:60]}")

        result = {
            'orders_count': len(orders),
            'orders': orders[:20],
            'fetched_at': datetime.now().isoformat(),
        }
        print(f"\nJSON:\n{json.dumps(result, ensure_ascii=False, indent=2)}")

        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
