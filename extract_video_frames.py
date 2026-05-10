#!/usr/bin/env python3
import os
import cv2
from PIL import Image

# Конфиг
VIDEO_PATH = "/home/yuri_artamonov/.openclaw/media/inbound/file_3---50173732-258a-415e-857e-334335937581.mp4"
OUTPUT_DIR = "/home/yuri_artamonov/.openclaw/workspace/video_frames"
CONSUMPTION_DIR = "/home/yuri_artamonov/.openclaw/workspace/consumption_agent"

# Создаём папки
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CONSUMPTION_DIR, exist_ok=True)

# Извлекаем кадры
cap = cv2.VideoCapture(VIDEO_PATH)
fps = cap.get(cv2.CAP_PROP_FPS)
frame_interval = int(fps)  # 1 кадр в секунду

frame_count = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    if frame_count % frame_interval == 0:
        frame_path = os.path.join(OUTPUT_DIR, f"frame_{frame_count:04d}.jpg")
        cv2.imwrite(frame_path, frame)
        print(f"Сохранён кадр: {frame_path}")
    
    frame_count += 1

cap.release()
print(f"Готово! Извлечено кадров: {len(os.listdir(OUTPUT_DIR))}")