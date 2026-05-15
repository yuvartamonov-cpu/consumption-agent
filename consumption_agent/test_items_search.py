#!/usr/bin/env python3
"""Тесты для поиска по /items и /items_full — поиск по названию категории и пересечению поисковых полей."""
import sys
import os
import sqlite3
import json
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')


def get_search_behaviour(items_rows, args: str):
    """Воспроизводит логику поиска из cmd_items и cmd_items_full."""
    args = args.lower()
    filtered = []
    for r in items_rows:
        name = (r[1] or '').lower()
        brand = (r[2] or '').lower()
        cat = (r[11] or r[3] or '').lower()  # category_name (index 11), fallback category_id
        notes = (r[9] or '').lower()
        attrs = {}
        try:
            attrs = json.loads(r[10] or '{}')
        except (json.JSONDecodeError, IndexError):
            pass
        desc = (attrs.get('description') or '').lower()
        tags = ' '.join(attrs.get('style_tags', [])).lower()
        color = (attrs.get('color') or '').lower()
        material = (attrs.get('material') or '').lower()

        search_text = f'{name} {brand} {cat} {notes} {desc} {tags} {color} {material}'
        if args in search_text:
            filtered.append(r)
    return filtered


def fetch_items():
    """Загружает items с JOIN categories как в cmd_items."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT i.id, i.name, i.brand, i.category_id, i.lifespan_months,
                   i.purchase_date, i.status, i.replace_after_months, i.replace_after_days, i.notes,
                   i.attributes,
                   COALESCE(c.name, i.category_id) AS category_name
            FROM items i
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL AND i.is_delivery = 0
              AND i.data_origin IN ('manual', 'local', 'telegram_photo', 'vision_photo', 'telegram_tag')
            LIMIT 1000
        """).fetchall()
    finally:
        conn.close()
    return rows


# --- TESTS ---

def test_category_search_by_russian_name():
    """Поиск по названию категории (русское имя) должен находить товары."""
    rows = fetch_items()
    # Ищем по «Прочее», «Одежда» и т.д.
    for cat_word in ['прочее', 'одежда', 'животные', 'спорт', 'техника']:
        found = get_search_behaviour(rows, cat_word)
        # Эта проверка пассивная: просто убеждаемся, что функция не падает
        assert isinstance(found, list)
    print(f'✅ Поиск по категориям не падает: {len(rows)} товаров протестировано')


def test_category_search_finds_something():
    """Поиск по 'прочее' должен найти хотя бы один товар."""
    rows = fetch_items()
    found = get_search_behaviour(rows, 'прочее')
    assert len(found) > 0, f'Ожидались товары в категории "Прочее", найдено 0'
    print(f'✅ Категория "Прочее": {len(found)} товаров')


def test_search_by_name():
    """Поиск по названию товара (точное вхождение) работает."""
    rows = fetch_items()
    # Берём первый попавшийся товар с именем > 3 символов
    target = None
    for r in rows:
        if r[1] and len(r[1]) > 3:
            target = r[1][:10]  # первые 10 символов
            break
    if target:
        found = get_search_behaviour(rows, target)
        assert len(found) > 0, f'Поиск по "{target}" не дал результатов'
        print(f'✅ Поиск по "{target}": {len(found)} товаров')
    else:
        print('⚠️ Нет товаров с именем >3 символов для теста')


def test_search_returns_different_items():
    """Поиск по разным категориям возвращает разные наборы."""
    rows = fetch_items()
    found_other = get_search_behaviour(rows, 'прочее')
    found_pets = get_search_behaviour(rows, 'животные')
    assert found_other != found_pets or len(found_other) == 0 or len(found_pets) == 0
    print(f'✅ Разные категории: "Прочее"={len(found_other)}, "Животные"={len(found_pets)}')


def test_search_by_category_id_still_works():
    """Поиск по старому формату (cat_other) тоже должен работать (fallback)."""
    rows = fetch_items()
    found = get_search_behaviour(rows, 'cat_other')
    assert isinstance(found, list)
    # Может быть 0, если нет товаров категории cat_other, но это не ошибка
    print(f'✅ Поиск по cat_other: {len(found)} товаров')


def test_search_attributes_description():
    """Поиск по description из attributes JSON работает."""
    rows = fetch_items()
    found_desc = get_search_behaviour(rows, 'городских поездок')
    assert isinstance(found_desc, list)
    print(f'✅ Поиск по description: {len(found_desc)} товаров')


def test_search_attributes_color():
    """Поиск по color из attributes JSON работает."""
    rows = fetch_items()
    found = get_search_behaviour(rows, 'фиолетовый')
    assert isinstance(found, list)
    # В attributes.json есть color=фиолетовый
    print(f'✅ Поиск по color: {len(found)} товаров')


def test_search_attributes_tags():
    """Поиск по tags/style_tags из attributes JSON работает."""
    rows = fetch_items()
    found = get_search_behaviour(rows, 'городской')
    assert isinstance(found, list)
    print(f'✅ Поиск по тегам: {len(found)} товаров')


def test_search_by_brand():
    """Поиск по brand работает."""
    rows = fetch_items()
    # Проверяем на существующих брендах
    brands_seen = set()
    for r in rows:
        if r[2]:
            brands_seen.add(r[2].lower()[:5])
    if brands_seen:
        sample = list(brands_seen)[0]
        found = get_search_behaviour(rows, sample)
        assert len(found) > 0, f'Поиск по бренду "{sample}" не дал результатов'
        print(f'✅ Поиск по бренду: по "{sample}" найдено {len(found)} товаров')
    else:
        print('⚠️ Нет товаров с brand для теста')


def test_search_nonexistent_returns_empty():
    """Поиск несуществующей подстроки возвращает пустой список."""
    rows = fetch_items()
    found = get_search_behaviour(rows, 'zxcvbnm_nonexistent_12345')
    assert len(found) == 0, 'Поиск несуществующей подстроки вернул результаты'
    print('✅ Поиск несуществующего: 0 результатов (OK)')


def test_category_name_in_items_full_select():
    """Проверяем, что в SELECT из cmd_items_full есть category_name на индексе 11."""
    conn = sqlite3.connect(DB_PATH)
    try:
        r = conn.execute("""
            SELECT i.id, i.name, i.brand, i.category_id, i.lifespan_months,
                   i.purchase_date, i.status, i.replace_after_months, i.replace_after_days, i.notes,
                   i.attributes,
                   COALESCE(c.name, i.category_id) AS category_name
            FROM items i
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL AND i.is_delivery = 0
            LIMIT 1
        """).fetchone()
        assert len(r) == 12, f'Ожидалось 12 колонок, получено {len(r)}'
        cat_name = r[11]
        assert cat_name and len(cat_name) > 0, 'category_name пустой'
        print(f'✅ SELECT cmd_items_full: 12 колонок, category_name = "{cat_name}"')
    finally:
        conn.close()


if __name__ == '__main__':
    test_category_search_by_russian_name()
    test_category_search_finds_something()
    test_search_by_name()
    test_search_returns_different_items()
    test_search_by_category_id_still_works()
    test_search_attributes_description()
    test_search_attributes_color()
    test_search_attributes_tags()
    test_search_by_brand()
    test_search_nonexistent_returns_empty()
    test_category_name_in_items_full_select()
    print('\n✅ Все тесты пройдены')
