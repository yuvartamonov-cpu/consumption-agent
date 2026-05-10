#!/usr/bin/env python3
import os
import subprocess
from datetime import datetime

# Конфиг
OUTPUT_DIR = os.path.expanduser("~/.openclaw/workspace/ozoncheques")
PACKAGE_NAME = "ru.ozon.app.android"
RECEIPTS_DIR = "/sdcard/Android/data/ru.ozon.app.android/files/receipts/"

# Создаём папку для чеков
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Проверяем подключение ADB
try:
    subprocess.run(["adb", "devices"], check=True, capture_output=True)
except:
    print("Ошибка: ADB не найден или телефон не подключён. Установи adb и подключи телефон.")
    exit(1)

# Извлекаем чеки
try:
    result = subprocess.run(["adb", "shell", "ls", RECEIPTS_DIR], capture_output=True, text=True)
    if "No such file or directory" in result.stderr:
        print("Ошибка: Папка с чеками не найдена. Убедись, что приложение Ozon установлено и чеки доступны.")
        exit(1)

    files = [f for f in result.stdout.split() if f.endswith(".pdf")]
    if not files:
        print("Чеков не найдено. Скачай хотя бы один чек в приложении Ozon.")
        exit(1)

    for file in files:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_path = os.path.join(OUTPUT_DIR, f"ozon_receipt_{timestamp}_{file}")
        subprocess.run(["adb", "pull", f"{RECEIPTS_DIR}{file}", output_path], check=True)
        print(f"Скачан чек: {output_path}")
except Exception as e:
    print(f"Ошибка: {e}")