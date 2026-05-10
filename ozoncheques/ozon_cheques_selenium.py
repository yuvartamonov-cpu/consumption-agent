#!/usr/bin/env python3
"""
Скачивает все чеки Ozon из писем на Gmail через Selenium.

Требования:
- ChromeDriver (https://chromedriver.chromium.org/)
- Установленный Chrome
- Логин/пароль от Ozon
- Логин/пароль от Gmail (для IMAP)

Запуск:
  python3 ozon_cheques_selenium.py
"""

import imaplib
import email
import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# Настройки
IMAP_SERVER = 'imap.gmail.com'
IMAP_USER = 'yu.v.artamonov@gmail.com'
IMAP_PASSWORD = 'xrsa izwn tvod ohqp'  # App Password
OZON_LOGIN = 'ВАШ_ЛОГИН_ОТ_OZON'  # email или телефон
OZON_PASSWORD = 'ВАШ_ПАРОЛЬ_ОТ_OZON'
CHROMEDRIVER_PATH = '/usr/local/bin/chromedriver'  # путь к ChromeDriver
DOWNLOAD_DIR = '/home/yuri_artamonov/.openclaw/workspace/ozoncheques'

# Настройка Selenium
chrome_options = Options()
chrome_options.add_argument('--headless')  # без графического интерфейса
chrome_options.add_argument('--disable-gpu')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_experimental_option('prefs', {
    'download.default_directory': DOWNLOAD_DIR,
    'download.prompt_for_download': False,
    'download.directory_upgrade': True,
    'safebrowsing.enabled': True
})

def get_ozon_links():
    """Извлекает ссылки на чеки из писем Ozon."""
    links = []
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(IMAP_USER, IMAP_PASSWORD)
    mail.select('inbox')

    # Ищем письма от Ozon с темой "Ваш чек"
    typ, data = mail.search(None, '(FROM "sender.ozon.ru" SUBJECT "Ваш чек")')
    for num in data[0].split():
        typ, data = mail.fetch(num, '(RFC822)')
        msg = email.message_from_bytes(data[0][1])
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                html = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                # Ищем ссылки на чеки
                import re
                found = re.findall(r'https://www.ozon.ru/my/e-check/download/[^\"\'\s>]+', html)
                for url in found:
                    if url not in links:
                        links.append(url)
    mail.close()
    mail.logout()
    return links

def login_ozon(driver):
    """Логинится в Ozon."""
    driver.get('https://www.ozon.ru/')
    time.sleep(2)

    # Нажимаем "Войти"
    driver.find_element(By.XPATH, '//button[contains(., "Войти")]').click()
    time.sleep(2)

    # Вводим логин
    login_field = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, '//input[@type="text"]'))
    )
    login_field.send_keys(OZON_LOGIN)
    driver.find_element(By.XPATH, '//button[contains(., "Продолжить")]').click()
    time.sleep(2)

    # Вводим пароль
    password_field = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, '//input[@type="password"]'))
    )
    password_field.send_keys(OZON_PASSWORD)
    driver.find_element(By.XPATH, '//button[contains(., "Войти")]').click()
    time.sleep(5)  # ждём авторизацию

def download_cheques(driver, links):
    """Скачивает чеки по ссылкам."""
    for i, url in enumerate(links, 1):
        print(f'Скачиваю чек {i}/{len(links)}: {url}')
        driver.get(url)
        time.sleep(3)  # ждём загрузку PDF
        # PDF должен сохраниться автоматически в DOWNLOAD_DIR

def main():
    # Получаем ссылки из писем
    links = get_ozon_links()
    print(f'Найдено {len(links)} ссылок на чеки')

    # Настраиваем Selenium
    service = Service(executable_path=CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        login_ozon(driver)
        download_cheques(driver, links)
        print(f'Все чеки скачаны в {DOWNLOAD_DIR}')
    except Exception as e:
        print(f'Ошибка: {e}')
    finally:
        driver.quit()

if __name__ == '__main__':
    main()
