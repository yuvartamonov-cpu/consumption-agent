#!/usr/bin/env python3
"""
Сбор заказов Яндекса через перехват API (более надёжно)

Версия для Windows.
Установка: pip install selenium webdriver-manager

Запуск: python yandex_orders_api.py
Порядок:
1. Откроется Chrome
2. Ты логинишься в Яндексе вручную
3. После входа — скрипт ходит по API и скачивает все заказы
4. Результат: ~/yandex_orders/<service>.json
"""

import json, os, time, re
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException
import requests

ORDERS_DIR = os.path.expanduser("~/yandex_orders")
os.makedirs(ORDERS_DIR, exist_ok=True)

# Яндекс API эндпойнты для истории заказов
SERVICES = {
    "lavka": {
        "url": "https://lavka.yandex.ru/orders",
        "api": "https://api.lavka.yandex.ru/v1/orders",
    },
    "eda": {
        "url": "https://eda.yandex.ru/orders",
        "api": "https://api.eda.yandex.ru/v1/orders",
    },
    "taxi": {
        "url": "https://go.yandex.ru/orders",
        "api": "https://api.go.yandex.ru/v1/orders",
    },
    "drive": {
        "url": "https://drive.yandex.ru/orders",
        "api": "https://api.drive.yandex.ru/v1/orders",
    },
}

def load_existing(service_id):
    fp = os.path.join(ORDERS_DIR, f"{service_id}.json")
    if os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_orders(service_id, orders):
    fp = os.path.join(ORDERS_DIR, f"{service_id}.json")
    existing = load_existing(service_id)
    
    # Дедупликация
    existing_ids = {json.dumps(o.get("id", "")) for o in existing}
    new = [o for o in orders if json.dumps(o.get("id", "")) not in existing_ids]
    
    if new:
        all_orders = existing + new
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(all_orders, f, ensure_ascii=False, indent=2)
        print(f"  ✅ {service_id}: +{len(new)} новых, всего {len(all_orders)}")
    else:
        print(f"  ✅ {service_id}: новых нет, всего {len(existing)}")
    
    return len(new)

def extract_cookies(driver):
    """Извлекает куки из браузера для API-запросов"""
    selenium_cookies = driver.get_cookies()
    cookies = {}
    for c in selenium_cookies:
        cookies[c['name']] = c['value']
    return cookies

def extract_session_id(cookies):
    """Извлекает Session_id из кук Яндекса"""
    for name in ['Session_id', 'session_id', 'yandexuid', 'ys']:
        if name in cookies:
            return cookies[name]
    return None

def collect_via_api(driver, service_id, config, cookies):
    """Пытается собрать заказы через API Яндекса"""
    session_id = extract_session_id(cookies)
    if not session_id:
        return []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    
    if session_id:
        headers["X-Session-Id"] = session_id
    
    orders = []
    cursor = None
    
    for page in range(100):  # до 100 страниц (5000 заказов)
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        
        try:
            print(f"    Страница {page+1}...", end=" ", flush=True)
            r = requests.get(
                config["api"],
                headers=headers,
                cookies=cookies,
                params=params,
                timeout=15
            )
            
            if r.status_code != 200:
                print(f"  ⚠️ API {service_id} вернул {r.status_code}")
                break
            
            data = r.json()
            
            # Структура ответа может отличаться — ищем список
            items = []
            if "orders" in data:
                items = data["orders"]
            elif "items" in data:
                items = data["items"]
            elif isinstance(data, list):
                items = data
            
            if not items:
                break
            
            for item in items:
                if isinstance(item, dict):
                    orders.append(item)
            
            cursor = data.get("cursor") or data.get("nextCursor") or data.get("next")
            if not cursor:
                break
                
        except Exception as e:
            print(f"  ⚠️ Ошибка API {service_id}: {e}")
            break
    
    return orders

def main():
    print("=" * 60)
    print("СБОР ЗАКАЗОВ ЯНДЕКСА")
    print("=" * 60)
    print("Лавка, Еда, Такси, Драйв\n")
    print("1. Откроется Chrome")
    print("2. ВОЙДИ В ЯНДЕКС (логин/пароль/2FA)")
    print("3. После входа — скрипт соберёт заказы\n")
    
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    print("Запускаю Chrome...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    total_new = 0
    
    try:
        # Авторизация
        print("\n🔐 Открываю страницу авторизации Яндекса...")
        driver.get("https://passport.yandex.ru/auth?retpath=https://ya.ru")
        
        print("\n   ⚠️  ВОЙДИ В АККАУНТ ЯНДЕКСА в открывшемся окне браузера")
        print("   ⏳ Я жду до 3 минут...")
        
        try:
            WebDriverWait(driver, 180).until(
                lambda d: "ya.ru" in d.current_url or "yandex" in d.current_url.lower()
            )
            print("   ✅ Вход выполнен")
        except TimeoutException:
            print("   ⚠️ Не дождался редиректа, пробую продолжить...")
        
        time.sleep(5)
        
        # Пробуем API
        cookies = extract_cookies(driver)
        print(f"\n🍪 Куки: {len(cookies)} шт")
        
        for service_id, config in SERVICES.items():
            orders = collect_via_api(driver, service_id, config, cookies)
            if orders:
                cnt = save_orders(service_id, orders)
                total_new += cnt
            else:
                print(f"  ⚠️ {service_id}: API не сработал, пробую через страницу...")
                # Fallback: открываем страницу истории
                driver.get(config["url"])
                time.sleep(5)
                page_text = driver.find_element(By.TAG_NAME, "body").text
                # Сохраняем как есть для ручного разбора
                fp = os.path.join(ORDERS_DIR, f"{service_id}_raw.txt")
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(page_text)
                print(f"  ⚠️ Сохранил сырой текст страницы в {fp}")
    
    finally:
        driver.quit()
    
    print("\n" + "=" * 60)
    print(f"ГОТОВО! Добавлено {total_new} новых заказов")
    print("Файлы: ~/yandex_orders/*.json")
    print("=" * 60)
    
    # Сводка
    print("\n📊 Статистика:")
    for sid in SERVICES:
        orders = load_existing(sid)
        if orders:
            dates = [o.get("createdAt", "") or o.get("date", "")[:10] for o in orders if o.get("createdAt") or o.get("date")]
            min_date = min(dates) if dates else "?"
            max_date = max(dates) if dates else "?"
            sum_amounts = sum(
                float(o.get("totalAmount", 0) or o.get("price", 0) or 0)
                for o in orders
            )
            print(f"  {sid}: {len(orders)} заказов, {min_date} - {max_date}, ~{sum_amounts:.0f} ₽")

if __name__ == "__main__":
    main()
