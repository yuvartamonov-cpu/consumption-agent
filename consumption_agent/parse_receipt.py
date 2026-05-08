#!/usr/bin/env python3
import re

# Распознанный текст (с исправлениями)
text = '''
Коссовый чек № 772
29.04.2026 16:12
Attpsifeww.ozce.rul _
Ингерчет Решения, COO
ИНН 7204217370
Виш Залотообложении: ОСН
Приход

1 Konerpynt0b Гарри Поттер мзбер *Xoreapre: Тайная комнатэ° 1176 деталей (замок,
совместим с lego harry patter, REPO среместимый; Подарок AAR мальчиков  ДеБОчАК)
1x 1954.60 #195400
81.4. НДС SACS. 593.08
ИН продзеца: 142101082058 `
Кальямова Валентниз Борисовна. ИП

2.  Достаена
1x 130.00
ву. НДС 22/122 23.44

итог
8 Tt HAC SACS

т.м. HAC 2222
Прелвзрительная оплата

безналижными
2084.00
НИ

eH: 17380440903213119

PH KT: 0099408624080578

on 274851.

epa: 507533917

Gam enc: wove лают
'''

# Исправляем ошибки OCR вручную
text = text.replace('Konerpynt0b', 'Конструктор')
text = text.replace('мзбер', 'набор')
text = text.replace('Xoreapre', 'Хогвартс')
text = text.replace('Attpsifeww.ozce.rul', 'https://www.ozon.ru')
text = text.replace('Ингерчет Решения', 'Интернет Решения')
text = text.replace('Виш Залотообложении', 'Вид налогообложения')
text = text.replace('Достаена', 'Доставка')
text = text.replace('ву. НДС', 'в т.ч. НДС')
text = text.replace('т.м. HAC', 'в т.ч. НДС')

# Парсинг товаров
items = []
for line in text.split('\n'):
    # Ищем товары (формат: 1x цена)
    match = re.search(r'(\d+)x\s*([\d,]+\.\d{2})', line)
    if match:
        qty, price = match.groups()
        # Ищем название товара в предыдущих строках
        name_lines = []
        for prev_line in text.split('\n'):
            if prev_line.strip() and not re.search(r'\d+x', prev_line):
                name_lines.append(prev_line.strip())
            if prev_line == line:
                break
        name = ' '.join(name_lines[-3:]).replace('1', '').strip()  # Убираем номер товара
        items.append({
            'name': name,
            'price': float(price.replace(',', '.')),
            'qty': int(qty),
            'total': float(price.replace(',', '.')) * int(qty)
        })

# Итоговая сумма
total_match = re.search(r'(\d+[.,]\d{2})\s*$', text, re.MULTILINE)
if total_match:
    total_amount = float(total_match.group(1).replace(',', '.'))
else:
    total_amount = None

print('=== Исправленный текст ===')
print(text)
print()

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