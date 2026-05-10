#!/usr/bin/env python3
import os
import json
from PIL import Image
from pathlib import Path
import time
import pytesseract

# Настройки
SCREENSHOTS_DIR = Path("/home/yuri_artamonov/.openclaw/workspace/screenshots/")
RESULTS_DIR = Path("/home/yuri_artamonov/.openclaw/workspace/results/")

# Создаём папки
SCREENSHOTS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# Логирование
def log(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
    with open(RESULTS_DIR / "process_log.txt", "a") as f:
        f.write(f"[{timestamp}] {message}\n")

# Извлечение текста (заглушка, если pytesseract не работает)
def extract_text_fallback(image_path):
    try:
        img = Image.open(image_path)
        # Пробуем pytesseract
        try:
            text = pytesseract.image_to_string(img, lang='rus+eng')
            if text.strip():
                return {
                    "filename": image_path.name,
                    "text": text.strip(),
                    "method": "pytesseract"
                }
        except:
            pass
        
        # Если pytesseract не сработал — возвращаем метаданные
        return {
            "filename": image_path.name,
            "text": "OCR не доступен. Установите tesseract-ocr.",
            "method": "fallback",
            "size": img.size,
            "mode": img.mode
        }
    except Exception as e:
        log(f"Ошибка при обработке {image_path.name}: {str(e)}")
        return {"error": str(e)}

# Основной цикл
if __name__ == "__main__":
    log("Скрипт запущен. Ожидание изображений...")
    
    while True:
        # Обрабатываем все изображения в папке
        for image_file in SCREENSHOTS_DIR.glob("*.*"):
            if image_file.suffix.lower() in (".png", ".jpg", ".jpeg"):
                log(f"Обработка: {image_file.name}")
                
                # Извлекаем текст
                result = extract_text_fallback(image_file)
                
                # Сохраняем результат
                result_path = RESULTS_DIR / f"{image_file.stem}.json"
                with open(result_path, "w") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                
                log(f"Сохранён результат: {result_path.name}")
                
                # Удаляем обработанный файл
                image_file.unlink()
        
        # Ждём новые файлы
        time.sleep(10)