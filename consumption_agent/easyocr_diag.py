#!/usr/bin/env python3
import argparse
import os
import re
import sys


def run_easyocr(image_path: str) -> int:
    import easyocr

    if not os.path.exists(image_path):
        print(f'Файл не найден: {image_path}')
        return 1

    reader = easyocr.Reader(['ru', 'en'])
    results = reader.readtext(image_path, detail=0)
    text = '\n'.join(results)
    print('=== EasyOCR Text ===')
    print(text)
    print()

    items = []
    total_amount = None
    for line in text.split('\n'):
        if ('итого' in line.lower() or 'всего' in line.lower()) and total_amount is None:
            match = re.search(r'(\d+[.,]\d{2})', line)
            if match:
                total_amount = float(match.group(1).replace(',', '.'))
        match = re.search(r'\d+\.\s*(.+?)(?:\s*[x×]\s*(\d+))?\s*([\d,]+\.\d{2})', line)
        if match:
            name, qty, price = match.groups()
            qty = int(qty) if qty else 1
            price_value = float(price.replace(',', '.'))
            items.append({
                'name': name.strip(),
                'price': price_value,
                'qty': qty,
                'total': price_value * qty,
            })

    print('=== Parsed Items ===')
    if items:
        for item in items:
            print(f'{item["name"]}: {item["price"]} ₽ × {item["qty"]} = {item["total"]} ₽')
    else:
        print('Товары не распознаны.')
    print()

    if total_amount is not None:
        print('=== Total Amount ===')
        print(f'{total_amount} ₽')
    else:
        print('Итоговая сумма не распознана.')

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Локальный EasyOCR-прогон для чеков')
    parser.add_argument('image', nargs='?', default='receipts/tesseract_test.jpg')
    args = parser.parse_args()
    return run_easyocr(args.image)


if __name__ == '__main__':
    sys.exit(main())
