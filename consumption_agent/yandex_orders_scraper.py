#!/usr/bin/env python3
"""
yandex_orders_scraper.py — надёжный сбор заказов Яндекса

Установка (один раз):
    pip install selenium webdriver-manager requests

Запуск:
    python yandex_orders_scraper.py

Что делает:
1. Открывает Chrome
2. Ты логинишься в Яндекс вручную
3. Скрипт собирает заказы из Еды, Лавки, Такси, Драйва
4. Сохраняет в JSON в папку yandex_orders/
"""

import json, os, time, re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
import requests

OUT_DIR = os.path.join(os.path.expanduser("~"), "yandex_orders")
os.makedirs(OUT_DIR, exist_ok=True)

SERVICES = [
    {"id": "eda",      "url": "https://eda.yandex.ru/orders",       "name": "Яндекс Еда"},
    {"id": "lavka",    "url": "https://lavka.yandex.ru/orders",     "name": "Яндекс Лавка"},
    {"id": "taxi",     "url": "https://go.yandex.ru/orders",        "name": "Яндекс Go (Такси)"},
    {"id": "drive",    "url": "https://drive.yandex.ru/orders",     "name": "Яндекс Драйв"},
]

def load_previous(service_id):
    fp = os.path.join(OUT_DIR, f"{service_id}.json")
    if os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_orders(service_id, orders):
    fp = os.path.join(OUT_DIR, f"{service_id}.json")
    existing = load_previous(service_id)
    existing_ids = {o.get("id") or json.dumps(o, sort_keys=True) for o in existing}
    new = [o for o in orders if (o.get("id") or json.dumps(o, sort_keys=True)) not in existing_ids]
    all_orders = existing + new
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(all_orders, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {service_id}: +{len(new)} новых, всего {len(all_orders)}")
    return len(new)

def wait_for_page_load(driver, timeout=30):
    """Ждёт полной загрузки страницы"""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(1)  # Дополнительная пауза
    except:
        print("  ⚠️ Таймаут загрузки страницы")

def scroll_to_bottom(driver, max_scrolls=100):
    """Скроллит страницу до конца"""
    last_height = driver.execute_script("return document.body.scrollHeight")
    scrolls = 0
    while scrolls < max_scrolls:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
        scrolls += 1
        if scrolls % 10 == 0:
            print(f"    Скролл {scrolls}, высота {last_height}")

def extract_cookies(driver):
    """Извлекает куки для API"""
    return {c['name']: c['value'] for c in driver.get_cookies()}

def try_api_orders(service_id, cookies):
    """Пробует получить заказы через API Яндекса"""
    api_urls = {
        "eda": "https://api.eda.yandex.ru/v1/orders",
        "lavka": "https://api.lavka.yandex.ru/v1/orders",
        "taxi": "https://api.go.yandex.ru/v1/orders",
        "drive": "https://api.drive.yandex.ru/v1/orders",
    }
    
    url = api_urls.get(service_id)
    if not url:
        return None
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    
    orders = []
    cursor = None
    
    for page in range(50):  # до 50 страниц (2500 заказов)
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        
        try:
            print(f"    API: страница {page+1}...")
            r = requests.get(url, headers=headers, cookies=cookies, params=params, timeout=15)
            if r.status_code != 200:
                print(f"    ❌ API вернул {r.status_code}")
                break
            
            data = r.json()
            items = data.get("orders") or data.get("items") or []
            if not items:
                print("    ❌ Пустой ответ от API")
                break
            
            orders.extend(items)
            cursor = data.get("cursor") or data.get("next") or data.get("nextCursor")
            if not cursor:
                print("    ✅ API: все заказы получены")
                break
                
        except Exception as e:
            print(f"    ❌ Ошибка API: {e}")
            break
    
    return orders if orders else None

def parse_page_orders(driver, service_id):
    """Парсит заказы прямо со страницы (если API не работает)"""
    print("  🔍 Парсинг страницы...")
    orders = []
    
    # Ищем все карточки заказов
    cards = driver.find_elements(By.CSS_SELECTOR, "[class*='order'], [class*='Order'], [class*='card'], [class*='history-item']")
    print(f"  Найдено карточек: {len(cards)}")
    
    for idx, card in enumerate(cards):
        try:
            text = card.text.strip()
            if not text:
                continue
            
            # Дата
            date_el = card.find_elements(By.CSS_SELECTOR, "[class*='date'], [class*='Date'], [class*='time']")
            date = date_el[0].text.strip() if date_el else ""
            
            # Сумма
            price_el = card.find_elements(By.CSS_SELECTOR, "[class*='price'], [class*='Price'], [class*='amount'], [class*='sum']")
            price = price_el[0].text.strip() if price_el else ""
            
            # Название/ресторан
            title_el = card.find_elements(By.CSS_SELECTOR, "h3, h4, [class*='title'], [class*='Title'], [class*='name']")
            title = title_el[0].text.strip() if title_el else ""
            
            # Состав заказа
            items = []
            item_els = card.find_elements(By.CSS_SELECTOR, "[class*='item'], [class*='Item'], [class*='product'], [class*='dish']")
            for el in item_els:
                items.append(el.text.strip())
            
            orders.append({
                "id": f"page_{idx}_{len(text)}",
                "service": service_id,
                "date": date,
                "price": price,
                "title": title,
                "items": items,
                "raw_text": text[:500],
            })
            
        except Exception as e:
            print(f"    ⚠️ Ошибка парсинга карточки: {e}")
    
    return orders

def scrape_service(driver, service):
    """Собирает заказы с одного сервиса"""
    sid = service["id"]
    url = service["url"]
    name = service["name"]
    
    print(f"\n{'='*50}")
    print(f"{name} ({url})")
    print(f"{'='*50}")
    
    driver.get(url)
    wait_for_page_load(driver)
    
    # Пробуем API
    cookies = extract_cookies(driver)
    orders = try_api_orders(sid, cookies)
    
    if not orders:
        print("  ⚠️ API не сработал, парсим страницу...")
        scroll_to_bottom(driver)
        orders = parse_page_orders(driver, sid)
    
    if orders:
        return orders
    else:
        print("  ❌ Заказов не найдено")
        return []

def main():
    print("=" * 60)
    print("СБОР ЗАКАЗОВ ЯНДЕКСА")
    print("=" * 60)
    print("Сервисы: Еда, Лавка, Такси, Драйв")
    print()
    print("🔹 1. Откроется Chrome")
    print("🔹 2. ВОЙДИ В ЯНДЕКС (логин, пароль, 2FA)")
    print("🔹 3. После входа НЕ ЗАКРЫВАЙ браузер — скрипт продолжит сам")
    print()
    
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    print("🚀 Запуск Chrome...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    total_new = 0
    
    try:
        # Авторизация
        print("\n🔐 Авторизация на Яндексе...")
        driver.get("https://passport.yandex.ru/auth?retpath=https://ya.ru")
        print("⏳ Ожидание входа (до 3 минут)...")
        
        try:
            WebDriverWait(driver, 180).until(
                lambda d: "auth" not in d.current_url.lower()
            )
            print("✅ Вход выполнен!")
        except TimeoutException:
            print("⚠️ Таймаут авторизации, продолжаю...")
        
        time.sleep(3)
        
        # Сбор заказов
        for svc in SERVICES:
            orders = scrape_service(driver, svc)
            if orders:
                cnt = save_orders(svc["id"], orders)
                total_new += cnt
    
    finally:
        driver.quit()
    
    print(f"\n{'='*60}")
    print(f"ГОТОВО! Добавлено {total_new} новых заказов")
    print(f"Файлы: {OUT_DIR}\*.json")
    print(f"{'='*60}")
    
    # Сводка
    print("\n📊 Сводка:")
    for svc in SERVICES:
        orders = load_previous(svc["id"])
        if orders:
            print(f"  {svc['name']}: {len(orders)} заказов")

if __name__ == "__main__":
    main()
