#!/usr/bin/env python3
import os
from PIL import Image
import cv2

# Конфиг
VIDEO_PATH = "/home/yuri_artamonov/.openclaw/media/inbound/file_3---50173732-258a-415e-857e-334335937581.mp4"
OUTPUT_DIR = "/home/yuri_artamonov/.openclaw/workspace/video_frames"
CONSUMPTION_DIR = "/home/yuri_artamonov/.openclaw/workspace/consumption_agent"

# Создаём папки
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CONSUMPTION_DIR, exist_ok=True)

# Извлекаем первый кадр
cap = cv2.VideoCapture(VIDEO_PATH)
ret, frame = cap.read()
cap.release()

if ret:
    frame_path = os.path.join(OUTPUT_DIR, "frame_0001.jpg")
    cv2.imwrite(frame_path, frame)
    print(f"Сохранён первый кадр: {frame_path}")
    
    # Анализируем товары на кадре
    try:
        image = Image.open(frame_path)
        # Здесь можно добавить распознавание товаров (например, через Google Vision API)
        # Пока сохраняем кадр для ручного анализа
        consumption_file = os.path.join(CONSUMPTION_DIR, "detected_items.txt")
        with open(consumption_file, "w") as f:
            f.write("Товары на видео (требуется ручной анализ):\n")
            f.write(f"Кадр: {frame_path}\n")
        print(f"Файл для анализа сохранён: {consumption_file}")
    except Exception as e:
        print(f"Ошибка при анализе кадра: {e}")
else:
    print("Не удалось извлечь кадры из видео.")