#!/usr/bin/env python3
"""
Проверка структуры таблиц в consumption.db
"""

import sqlite3

conn = sqlite3.connect('consumption.db')
cursor = conn.cursor()

# Получаем список таблиц
tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
print("=== Таблицы в БД ===")
for table in tables:
    print(f"- {table[0]}")

# Структура таблицы purchases
print("\n=== Структура таблицы 'purchases' ===")
cursor.execute("PRAGMA table_info(purchases);")
for column in cursor.fetchall():
    print(f"- {column[1]} ({column[2]})")

conn.close()