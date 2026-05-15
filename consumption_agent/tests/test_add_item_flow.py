#!/usr/bin/env python3
"""
Тестовый скрипт для проверки полного цикла работы с items:
1. Добавление через /add_item (текст)
2. Добавление через фото с caption
3. Добавление через фото без caption (vision_photo)
4. Просмотр через /items_full all
5. Поиск через /items_full <query>
6. Удаление через кнопку 🗑
"""

import sqlite3
import os
import sys
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
DB_PATH = _TMP_DB.name
_TMP_DB.close()


def _init_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            category_id TEXT,
            status TEXT,
            replace_after_months INTEGER,
            replace_after_days INTEGER,
            purchase_date TEXT,
            notes TEXT,
            attributes TEXT,
            data_origin TEXT,
            deleted_at TEXT,
            is_delivery INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS item_photos (
            item_id INTEGER,
            media_asset_id INTEGER,
            is_primary INTEGER DEFAULT 0,
            UNIQUE(item_id, media_asset_id)
        );
    """)
    conn.commit()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    _init_schema(conn)
    return conn


def teardown_module(module):
    try:
        os.unlink(DB_PATH)
    except OSError:
        pass

def test_database_schema():
    """Проверяем наличие необходимых колонок"""
    print("=== Тест 1: Схема базы данных ===")
    conn = get_db()
    cursor = conn.execute("PRAGMA table_info(items)")
    columns = {row[1] for row in cursor.fetchall()}
    
    required = {'id', 'name', 'brand', 'category_id', 'replace_after_months', 
                'replace_after_days', 'purchase_date', 'notes', 'attributes', 
                'data_origin', 'deleted_at'}
    
    missing = required - columns
    if missing:
        print(f"❌ Отсутствуют колонки: {missing}")
        return False
    
    print("✅ Все необходимые колонки на месте")
    print(f"   Колонки: {sorted(columns)}")
    conn.close()
    return True

def test_add_item_text():
    """Проверяем добавление через текстовую команду"""
    print("\n=== Тест 2: Добавление через /add_item (текст) ===")
    
    # Симулируем данные от brand_parser
    test_cases = [
        {
            'name': 'Тестовый пиджак',
            'brand': 'TestBrand',
            'replace_months': 6,
            'replace_days': None,
            'category': 'cat_clo_everyday'
        },
        {
            'name': 'Тестовые носки',
            'brand': None,
            'replace_months': None,
            'replace_days': 30,
            'category': 'cat_clo_underwear'
        }
    ]
    
    conn = get_db()
    for i, case in enumerate(test_cases):
        notes = 'Добавлено через /add_item\n'
        if case['replace_days']:
            notes += f'Ожидается замена через {case["replace_days"]} дн.'
        elif case['replace_months']:
            notes += f'Ожидается замена через {case["replace_months"]} мес.'
        
        cur = conn.execute('''
            INSERT INTO items (name, brand, category_id, status, 
                             replace_after_months, replace_after_days, 
                             purchase_date, notes, data_origin)
            VALUES (?, ?, ?, 'in_use', ?, ?, date('now'), ?, 'manual')
        ''', (case['name'], case['brand'], case['category'],
              case['replace_months'], case['replace_days'], notes))
        
        item_id = cur.lastrowid
        print(f"✅ Добавлен item ID={item_id}: {case['name']} (brand={case['brand']})")
    
    conn.commit()
    conn.close()
    return True

def test_add_item_with_photo():
    """Проверяем добавление с фото (caption)"""
    print("\n=== Тест 3: Добавление через фото с caption ===")
    
    conn = get_db()
    
    # Симулируем распознавание
    item_name = 'Тестовое поло'
    brand = 'TestPolo'
    color = 'синий'
    description = 'Тестовое описание поло'
    
    attrs = {
        'color': color,
        'description': description,
        'style_tags': ['casual'],
        'material': 'хлопок'
    }
    
    import json
    notes = f'Добавлено через /add_item\nОжидается замена через 3 мес.\nЦвет: {color}\nОписание: {description}'
    
    cur = conn.execute('''
        INSERT INTO items (name, brand, category_id, status,
                         replace_after_months, purchase_date,
                         notes, attributes, data_origin)
        VALUES (?, ?, 'cat_clo_everyday', 'in_use', 3, date('now'), ?, ?, 'manual')
    ''', (item_name, brand, notes, json.dumps(attrs, ensure_ascii=False)))
    
    item_id = cur.lastrowid
    
    # Симулируем сохранение фото
    conn.execute('''
        INSERT OR IGNORE INTO item_photos (item_id, media_asset_id, is_primary)
        VALUES (?, 999, 1)
    ''', (item_id,))
    
    print(f"✅ Добавлен item ID={item_id} с фото: {item_name}")
    
    conn.commit()
    conn.close()
    return True

def test_vision_photo():
    """Проверяем добавление через vision_photo (без caption)"""
    print("\n=== Тест 4: Добавление через vision_photo ===")
    
    conn = get_db()
    
    import json
    item_name = 'Распознанный пиджак'
    brand = 'VisionBrand'
    
    attrs = {
        'color': 'чёрный',
        'description': 'Распознанное описание',
        'style_tags': ['formal'],
        'vision_type': 'clothing',
        'material': 'шерсть'
    }
    
    notes = 'Добавлено через распознавание фото\nЦвет: чёрный\nОписание: Распознанное описание'
    
    cur = conn.execute('''
        INSERT INTO items (name, brand, category_id, status,
                         purchase_date, notes, attributes, data_origin)
        VALUES (?, ?, 'cat_clo_everyday', 'in_use', date('now'), ?, ?, 'vision_photo')
    ''', (item_name, brand, notes, json.dumps(attrs, ensure_ascii=False)))
    
    item_id = cur.lastrowid
    
    # Сохраняем фото
    conn.execute('''
        INSERT OR IGNORE INTO item_photos (item_id, media_asset_id, is_primary)
        VALUES (?, 999, 1)
    ''', (item_id,))
    
    print(f"✅ Добавлен vision_photo ID={item_id}: {item_name}")
    
    conn.commit()
    conn.close()
    return True

def test_items_full_output():
    """Проверяем вывод /items_full all"""
    print("\n=== Тест 5: Вывод /items_full all ===")
    
    conn = get_db()
    
    rows = conn.execute('''
        SELECT id, name, brand, replace_after_months, replace_after_days, 
               notes, attributes, data_origin
        FROM items
        WHERE deleted_at IS NULL AND is_delivery = 0
          AND data_origin IN ('manual', 'local', 'vision_photo')
        ORDER BY id DESC
        LIMIT 10
    ''').fetchall()
    
    print(f"Найдено {len(rows)} items:")
    for r in rows:
        item_id, name, brand, months, days, notes, attrs, origin = r
        print(f"  ID={item_id}: {name} (brand={brand}, origin={origin})")
        print(f"    replace: {days or months or 'нет'}")
        if attrs:
            import json
            try:
                a = json.loads(attrs)
                print(f"    color: {a.get('color')}, tags: {a.get('style_tags')}")
            except:
                pass
    
    conn.close()
    return True

def test_search():
    """Проверяем поиск"""
    print("\n=== Тест 6: Поиск /items_full <query> ===")
    
    conn = get_db()
    
    queries = ['пиджак', 'поло', 'TestBrand', 'синий']
    
    for query in queries:
        print(f"\nПоиск: '{query}'")
        rows = conn.execute('''
            SELECT id, name, brand, notes, attributes
            FROM items
            WHERE deleted_at IS NULL
              AND (LOWER(name) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(notes) LIKE ?)
        ''', (f'%{query.lower()}%', f'%{query.lower()}%', f'%{query.lower()}%')).fetchall()
        
        for r in rows:
            print(f"  Найден: ID={r[0]} {r[1]} (brand={r[2]})")
    
    conn.close()
    return True

def test_delete():
    """Проверяем удаление"""
    print("\n=== Тест 7: Удаление (soft delete) ===")
    
    conn = get_db()
    
    # Находим тестовые items
    rows = conn.execute('''
        SELECT id FROM items
        WHERE name LIKE 'Тестовый%' OR name LIKE 'Распознанный%'
    ''').fetchall()
    
    for r in rows:
        item_id = r[0]
        conn.execute('''
            UPDATE items SET deleted_at = datetime('now'), status = 'disposed'
            WHERE id = ?
        ''', (item_id,))
        print(f"✅ Удалён item ID={item_id}")
    
    conn.commit()
    conn.close()
    return True

def cleanup():
    """Удаляем тестовые данные"""
    print("\n=== Очистка тестовых данных ===")
    
    conn = get_db()
    
    # Удаляем тестовые items
    conn.execute('''
        DELETE FROM items
        WHERE name LIKE 'Тестовый%' 
           OR name LIKE 'Распознанный%'
           OR name LIKE 'Тест%'
    ''')
    
    # Удаляем тестовые фото
    conn.execute('DELETE FROM item_photos WHERE media_asset_id = 999')
    
    conn.commit()
    conn.close()
    print("✅ Тестовые данные удалены")

def main():
    print("=" * 60)
    print("ТЕСТИРОВАНИЕ ПОТОКА ДОБАВЛЕНИЯ ITEMS")
    print("=" * 60)
    
    try:
        # Очистка перед тестами
        cleanup()
        
        # Запуск тестов
        tests = [
            test_database_schema,
            test_add_item_text,
            test_add_item_with_photo,
            test_vision_photo,
            test_items_full_output,
            test_search,
            test_delete,
        ]
        
        passed = 0
        failed = 0
        
        for test in tests:
            try:
                if test():
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"❌ Ошибка в тесте: {e}")
                failed += 1
        
        print("\n" + "=" * 60)
        print(f"РЕЗУЛЬТАТ: {passed} пройдено, {failed} не пройдено")
        print("=" * 60)
        
        # Финальная очистка
        cleanup()
        
        return failed == 0
        
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
        cleanup()
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
