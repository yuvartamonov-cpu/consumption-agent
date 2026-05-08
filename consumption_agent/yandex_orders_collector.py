#!/usr/bin/env python3
"""
Сбор заказов из Яндекс.Еда, Лавка, Такси, Драйв через браузер

Что делает:
1. Открывает Chrome вручную (ты логинишься сам — безопасно)
2. Собирает историю заказов с Яндекс Go/Еда/Лавка
3. Сохраняет JSON в папку ~/yandex_orders/
4. Можно запускать раз в неделю для инкрементального сбора

Установка зависимостей (1 раз):
pip install selenium webdriver-manager

Запуск:
python yandex_orders_collector.py
"""

import json, os, time, re
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException

ORDERS_DIR = os.path.expanduser("~/yandex_orders")
os.makedirs(ORDERS_DIR, exist_ok=True)

SERVICES = {
    "lavka": "https://lavka.yandex.ru/orders",
    "eda": "https://eda.yandex.ru/orders",
    "taxi": "https://go.yandex.ru/orders",
    "drive": "https://drive.yandex.ru/orders",
}

def load_existing(service_id):
    """Загружает уже собранные заказы"""
    fp = os.path.join(ORDERS_DIR, f"{service_id}.json")
    if os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_orders(service_id, orders):
    """Сохраняет заказы, удаляя дубликаты по ID"""
    fp = os.path.join(ORDERS_DIR, f"{service_id}.json")
    existing = load_existing(service_id)
    existing_ids = {o.get("id") or o.get("order_id") for o in existing}
    new = [o for o in orders if (o.get("id") or o.get("order_id")) not in existing_ids]
    if new:
        all_orders = existing + new
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(all_orders, f, ensure_ascii=False, indent=2)
        print(f"  Добавлено {len(new)} новых, всего {len(all_orders)}")
    else:
        print(f"  Новых нет, всего {len(existing)}")

def collect_orders(driver, service_id, url, max_pages=50):
    """Собирает заказы со страницы истории"""
    print(f"\n=== {service_id}: {url} ===")
    driver.get(url)
    time.sleep(5)
    
    orders = []
    six_months_ago = datetime.now() - timedelta(days=180)
    
    for page in range(max_pages):
        # Скроллим вниз, чтобы подгрузить заказы
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        
        # Ищем карточки заказов
        cards = driver.find_elements(By.CSS_SELECTOR, "[class*='order'], [class*='Order'], [class*='card'], [class*='Card'], [data-testid*='order'], [class*='history-item']")
        
        if not cards:
            # Пробуем найти любой контейнер с заказами
            cards = driver.find_elements(By.CSS_SELECTOR, "div[class*='item'], div[class*='Item']")
        
        prev_count = len(orders)
        
        for card in cards:
            try:
                text = card.text.strip()
                if not text or len(text) < 20:
                    continue
                
                # Дата заказа
                date_str = ""
                date_el = card.find_elements(By.CSS_SELECTOR, "[class*='date'], [class*='Date'], [class*='time'], [class*='Time']")
                if date_el:
                    date_str = date_el[0].text.strip()
                
                # Сумма
                price_el = card.find_elements(By.CSS_SELECTOR, "[class*='price'], [class*='Price'], [class*='amount'], [class*='sum'], [class*='total'], [class*='Total']")
                price = ""
                if price_el:
                    price = price_el[0].text.strip()
                
                # Название/ресторан
                title_el = card.find_elements(By.CSS_SELECTOR, "h3, h4, [class*='title'], [class*='Title'], [class*='name'], [class*='Name'], [class*='restaurant']")
                title = title_el[0].text.strip() if title_el else ""
                
                orders.append({
                    "service": service_id,
                    "raw_text": text[:500],
                    "date": date_str,
                    "price": price,
                    "title": title,
                    "scraped_at": datetime.now().isoformat(),
                })
            except:
                pass
        
        print(f"  Страница {page+1}: собрано {len(orders)} заказов")
        
        # Если новых заказов не появилось — достигли конца
        if len(orders) == prev_count:
            print("  Больше заказов нет")
            break
        
        # Проверяем, не ушли ли мы за 6 месяцев по датам
        if orders and date_str:
            # Парсим дату — если она старше 6 мес, останавливаемся
            from datetime import datetime, timedelta
            try:
                # Формат: '31 марта 2025' или '01.01.2025' или ISO
                for fmt in ('%d %B %Y', '%d.%m.%Y', '%Y-%m-%d'):
                    try:
                        order_date = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    # Русские месяца — грубая замена
                    ru_months = {'января': '01', 'февраля': '02', 'марта': '03',
                                 'апреля': '04', 'мая': '05', 'июня': '06',
                                 'июля': '07', 'августа': '08', 'сентября': '09',
                                 'октября': '10', 'ноября': '11', 'декабря': '12'}
                    for ru, num in ru_months.items():
                        if ru in date_str.lower():
                            normalized = date_str.lower().replace(ru, num)
                            order_date = datetime.strptime(normalized, '%d %m %Y')
                            break
                    else:
                        order_date = datetime.now()
                
                six_months_ago = datetime.now() - timedelta(days=180)
                if order_date < six_months_ago:
                    print(f"  Достигнут предел 6 месяцев ({date_str}), останавливаемся")
                    break
            except Exception as e:
                print(f"  Не удалось распарсить дату '{date_str}': {e}")
    
    return orders

def main():
    print("=== Сбор заказов Яндекса ===")
    print("1. Откроется Chrome")
    print("2. Войди в Яндекс (логин/пароль + 2FA если надо)")
    print("3. Скрипт сам соберёт заказы из Лавки, Еды, Такси, Драйва")
    print("4. Результаты в ~/yandex_orders/\n")
    
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    print("Запускаю Chrome...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    try:
        # Сначала логинимся на Яндексе
        print("\n1. Авторизация на Яндексе...")
        driver.get("https://passport.yandex.ru/auth")
        print("   Войди в аккаунт в открывшемся окне браузера.")
        print("   Я жду, пока ты закончишь... (максимум 3 минуты)")
        
        # Ждём, пока пользователь войдёт (редирект на главную или в Яндкс ID)
        try:
            WebDriverWait(driver, 180).until(
                lambda d: "auth" not in d.current_url.lower() or "profile" in d.current_url.lower()
            )
        except TimeoutException:
            print("   Таймаут авторизации. Продолжаю в любом случае...")
        
        time.sleep(3)
        print("   Авторизация пройдена (надеюсь)")
        
        # Собираем заказы из каждого сервиса
        for service_id, url in SERVICES.items():
            orders = collect_orders(driver, service_id, url)
            save_orders(service_id, orders)
        
        # Сводка
        print("\n=== ИТОГО ===")
        for service_id in SERVICES:
            orders = load_existing(service_id)
            print(f"  {service_id}: {len(orders)} заказов сохранено в ~/yandex_orders/{service_id}.json")
    
    finally:
        driver.quit()
    
    print("\nГотово! Теперь можно запустить Consumption Agent для импорта:")
    print("  python consumption_agent_full_030526.py import --from-yandex-json")

if __name__ == "__main__":
    main()
