#!/usr/bin/env python3
"""
Обработка всех скриншотов в папке incoming_screenshots
"""

import os
import subprocess

# Папка со скриншотами
SCREENSHOTS_DIR = "incoming_screenshots"

# Обработка каждого файла
for filename in os.listdir(SCREENSHOTS_DIR):
    if filename.lower().endswith((".jpg", ".png")):
        filepath = os.path.join(SCREENSHOTS_DIR, filename)
        print(f"Обработка: {filename}")
        try:
            subprocess.run(["python3", "ocr_recognize.py", filepath], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при обработке {filename}: {e}")

print("Все скриншоты обработаны!")