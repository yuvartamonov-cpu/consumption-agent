#!/usr/bin/env python3
import easyocr
import re
import os

# Путь к фото
image_path = 'receipts/tesseract_test.jpg'

# Проверяем, что файл существует
if not os.path.exists(image_path):
    print(f'Файл не найден: {image_path}')
    exit(1)

# EasyOCR
reader = easyocr.Reader(['ru', 'en'])
results = reader.readtext(image_path, detail=0)
text = '\n'.join(results)
print('=== EasyOCR Text ===')
print(text)
print()

# Парсинг товаров
items = []
total_amount = None
for line in text.split('\n'):
    if ('итого' in line.lower() or 'всего' in line.lower()) and not total_amount:
        match = re.search(r'(\d+[.,]\d{2})', line)
        if match:
            total_amount = float(match.group(1).replace(',', '.'))
    match = re.search(r'\d+\.\s*(.+?)(?:\s*[x×]\s*(\d+))?\s*([\d,]+\.\d{2})', line)
    if match:
        name, qty, price = match.groups()
        qty = int(qty) if qty else 1
        items.append({
            'name': name.strip(),
            'price': float(price.replace(',', '.')),
            'qty': qty,
            'total': float(price.replace(',', '.')) * qty
        })

print('=== Parsed Items ===')
if items:
    for item in items:
        print(f'{item["name"]}: {item["price"]} ₽ × {item["qty"]} = {item["total"]} ₽')
else:
    print('Товары не распознаны.')
print()

if total_amount:
    print(f'=== Total Amount ===')
    print(f'{total_amount} ₽')
else:
    print('Итоговая сумма не распознана.')