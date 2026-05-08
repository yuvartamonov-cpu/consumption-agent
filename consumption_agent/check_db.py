#!/usr/bin/env python3
"""
Проверка БД (финальная версия)
"""

import sqlite3

conn = sqlite3.connect('consumption.db')
cursor = conn.cursor()

# Запрос 1: Последние 10 покупок
print("=== Последние 10 покупок ===")
cursor.execute("""
SELECT 
    p.purchase_date AS date, 
    i.name AS item, 
    p.total_amount AS price, 
    c.name AS category, 
    i.status
FROM 
    purchases p 
JOIN 
    items i ON p.id = i.purchase_id 
JOIN 
    categories c ON i.category_id = c.id 
ORDER BY 
    p.purchase_date DESC 
LIMIT 10;
""")

for row in cursor.fetchall():
    print(f"{row[0]} | {row[1][:40]}... | {row[2]} ₽ | {row[3]} | {row[4]}")

# Запрос 2: Статистика инвентаря (по категориям и статусам)
print("\n=== Статистика инвентаря ===")
cursor.execute("""
SELECT 
    c.name AS category, 
    i.status, 
    COUNT(i.id) AS count
FROM 
    items i 
JOIN 
    categories c ON i.category_id = c.id 
GROUP BY 
    c.name, i.status
ORDER BY 
    count DESC;
""")

for row in cursor.fetchall():
    print(f"{row[0]} | {row[1]} | {row[2]} шт.")

# Запрос 3: Сумма потраченного по категориям
print("\n=== Сумма потраченного по категориям ===")
cursor.execute("""
SELECT 
    c.name AS category, 
    SUM(p.total_amount) AS total_spent
FROM 
    purchases p 
JOIN 
    items i ON p.id = i.purchase_id 
JOIN 
    categories c ON i.category_id = c.id 
GROUP BY 
    c.name
ORDER BY 
    total_spent DESC;
""")

for row in cursor.fetchall():
    print(f"{row[0]} | {row[1]} ₽")

conn.close()