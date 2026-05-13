#!/usr/bin/env python3
"""
Consumption Agent — инициализация/миграция БД (SQLite для MVP).
Создаёт расширенную схему на основе архитектурного DDL,
переносит данные из старой схемы.
"""
import sqlite3
import os
import json
from datetime import date, datetime
from consumption.db import DB_PATH, connect

OLD_TABLES = ['purchases', 'purchase_items', 'recognized_items', 'cheques_log']


def load_old_data(conn):
    """Выгружает данные из старой схемы в dict."""
    old = {}
    for table in OLD_TABLES:
        try:
            rows = conn.execute(f'SELECT * FROM {table}').fetchall()
            columns = [d[0] for d in conn.execute(f'PRAGMA table_info({table})').fetchall()]
            old[table] = [dict(zip(columns, r)) for r in rows]
        except sqlite3.OperationalError:
            old[table] = []
    return old


def create_new_schema(conn):
    """Создаёт таблицы по архитектурному DDL (адаптировано под SQLite)."""
    
    conn.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        -- 1. profiles
        CREATE TABLE IF NOT EXISTS profiles (
            id                TEXT PRIMARY KEY DEFAULT 'default',
            name              TEXT DEFAULT 'Default',
            currency          TEXT DEFAULT 'RUB',
            timezone          TEXT DEFAULT 'Europe/Moscow',
            notification_config TEXT DEFAULT '{"quiet_hours":"23:00-08:00","max_daily":3}',
            created_at        TEXT DEFAULT (datetime('now')),
            updated_at        TEXT DEFAULT (datetime('now'))
        );

        -- 2. categories (древовидная, без ltree для SQLite)
        CREATE TABLE IF NOT EXISTS categories (
            id                TEXT PRIMARY KEY,
            parent_id         TEXT REFERENCES categories(id),
            name              TEXT NOT NULL,
            slug              TEXT NOT NULL,
            sort_order        INTEGER DEFAULT 0,
            is_active         INTEGER DEFAULT 1,
            created_at        TEXT DEFAULT (datetime('now'))
        );

        -- 3. purchases (журнал покупок)
        CREATE TABLE IF NOT EXISTS purchases (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id        TEXT NOT NULL DEFAULT 'default',
            purchase_date     TEXT NOT NULL,
            total_amount      REAL,
            currency          TEXT DEFAULT 'RUB',
            payment_method    TEXT,
            source            TEXT,
            store_name        TEXT,
            order_number      TEXT,
            receipt_url       TEXT,
            email_message_id  TEXT UNIQUE,
            notes             TEXT,
            data_origin       TEXT DEFAULT 'local',
            created_at        TEXT DEFAULT (datetime('now')),
            deleted_at        TEXT
        );

        -- 4. items (инвентарь: купленное + вишлист)
        CREATE TABLE IF NOT EXISTS items (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id        TEXT NOT NULL DEFAULT 'default',
            category_id       TEXT REFERENCES categories(id),
            name              TEXT NOT NULL,
            brand             TEXT,
            model             TEXT,
            sku               TEXT,
            description       TEXT,
            attributes        TEXT DEFAULT '{}',
            status            TEXT DEFAULT 'in_use'
                              CHECK (status IN ('wishlist','in_use','low_stock','storage','expired','broken','disposed','replaced')),
            quantity          INTEGER DEFAULT 1,
            unit              TEXT,
            remaining         REAL,
            purchase_date     TEXT,
            purchase_price    REAL,
            purchase_currency TEXT DEFAULT 'RUB',
            purchase_source   TEXT,
            purchase_url      TEXT,
            purchase_id       INTEGER REFERENCES purchases(id),
            warranty_months   INTEGER,
            expiry_date       TEXT,
            lifespan_months   INTEGER,
            priority          TEXT CHECK (priority IN ('critical','must','planned','backlog','wish')),
            target_price      REAL,
            current_price     REAL,
            price_tracking    INTEGER DEFAULT 0,
            discovery_source  TEXT,
            replaces_id       INTEGER REFERENCES items(id),
            notes             TEXT,
            tags              TEXT DEFAULT '[]',
            data_origin       TEXT DEFAULT 'local',
            created_at        TEXT DEFAULT (datetime('now')),
            updated_at        TEXT DEFAULT (datetime('now')),
            deleted_at        TEXT
        );

        -- 5. recognized_items_log (источник: распознанные товары из скриншотов/чеков)
        CREATE TABLE IF NOT EXISTS recognized_items_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file       TEXT NOT NULL,
            source_type       TEXT NOT NULL,
            recognized_product TEXT NOT NULL,
            confidence        TEXT,
            matched_item_id   INTEGER REFERENCES items(id),
            notes             TEXT,
            imported_at       TEXT DEFAULT (datetime('now'))
        );

        -- 6. cheques_log (логи обработанных чеков)
        CREATE TABLE IF NOT EXISTS cheques_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            email_uid         TEXT UNIQUE,
            source            TEXT DEFAULT 'ozon',
            cheque_date       TEXT,
            subject           TEXT,
            receipt_url       TEXT,
            imported_at       TEXT DEFAULT (datetime('now'))
        );

        -- 7. alerts (уведомления)
        CREATE TABLE IF NOT EXISTS alerts (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id        TEXT NOT NULL DEFAULT 'default',
            item_id           INTEGER REFERENCES items(id),
            purchase_id       INTEGER REFERENCES purchases(id),
            alert_type        TEXT NOT NULL CHECK (alert_type IN (
                                  'warranty_expiring','warranty_expired',
                                  'expiry_approaching','expired',
                                  'low_stock','price_drop',
                                  'seasonal_reminder','dependency_alert','budget_warning'
                              )),
            title             TEXT NOT NULL,
            message           TEXT,
            scheduled_at      TEXT,
            sent_at           TEXT,
            status            TEXT DEFAULT 'pending' CHECK (status IN ('pending','sent','dismissed','actioned')),
            created_at        TEXT DEFAULT (datetime('now'))
        );

        -- 8. subscriptions (подписки)
        CREATE TABLE IF NOT EXISTS subscriptions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id        TEXT NOT NULL DEFAULT 'default',
            name              TEXT NOT NULL,
            provider          TEXT,
            price_monthly     REAL,
            price_yearly      REAL,
            currency          TEXT DEFAULT 'RUB',
            billing_date      INTEGER,
            next_billing      TEXT,
            status            TEXT DEFAULT 'active'
                              CHECK (status IN ('active','paused','cancelled','expired')),
            auto_renew        INTEGER DEFAULT 1,
            notes             TEXT,
            created_at        TEXT DEFAULT (datetime('now'))
        );
    ''')


def seed_categories(conn):
    """Заполняет дерево категорий."""
    categories = [
        # Корневые
        ('cat_clothing',      None,  'Одежда и обувь',        'clothing',      10),
        ('cat_tech',          None,  'Техника и электроника',  'tech',          20),
        ('cat_food',          None,  'Продукты питания',      'food',          30),
        ('cat_cosmetics',     None,  'Косметика и уход',      'cosmetics',     40),
        ('cat_health',        None,  'Здоровье и аптека',     'health',        50),
        ('cat_home',          None,  'Дом и ремонт',          'home',          60),
        ('cat_sports',        None,  'Спорт и активный отдых','sports',        70),
        ('cat_auto',          None,  'Авто и транспорт',      'auto',          80),
        ('cat_hobbies',       None,  'Хобби и развлечения',   'hobbies',       90),
        ('cat_digital',       None,  'Цифровое',              'digital',      100),
        ('cat_pets',          None,  'Животные',              'pets',         110),
        ('cat_subscriptions', None,  'Подписки',              'subscriptions',120),
        # Подкатегории: Одежда
        ('cat_clo_outer',     'cat_clothing', 'Верхняя одежда',   'outerwear',    1),
        ('cat_clo_everyday',  'cat_clothing', 'Повседневная одежда','everyday',   2),
        ('cat_clo_shoes',     'cat_clothing', 'Обувь',            'shoes',        3),
        ('cat_clo_access',    'cat_clothing', 'Аксессуары',       'accessories',  4),
        ('cat_clo_underwear', 'cat_clothing', 'Бельё и домашнее',  'underwear',   5),
        # Подкатегории: Техника
        ('cat_tech_comp',     'cat_tech',     'Компьютеры и планшеты', 'computers',    1),
        ('cat_tech_audio',    'cat_tech',     'Аудио и видео',       'audio_video',   2),
        ('cat_tech_phone',    'cat_tech',     'Телефоны и носимые',  'phones',        3),
        ('cat_tech_appl',     'cat_tech',     'Бытовые приборы',     'appliances',    4),
        ('cat_tech_kitchen',  'cat_tech',     'Кухонная техника',    'kitchen',       5),
        # Подкатегории: Животные
        ('cat_pets_food',     'cat_pets',     'Корм для животных',   'pet_food',      1),
        ('cat_pets_med',      'cat_pets',     'Ветеринария',         'vet',           2),
        ('cat_pets_access',   'cat_pets',     'Зоотовары',           'pet_access',    3),
        # Подкатегории: Дом
        ('cat_home_furn',     'cat_home',     'Мебель',              'furniture',     1),
        ('cat_home_decor',    'cat_home',     'Декор',               'decor',         2),
        ('cat_home_kitchen',  'cat_home',     'Кухня и хранение',    'home_kitchen',  3),
        # Подкатегории: Спорт / хобби / прочее
        ('cat_sport',         'cat_sports',   'Спортивные товары',   'sport_goods',   1),
        ('cat_culture_books', 'cat_hobbies',  'Книги и культура',    'books',         1),
        ('cat_sexual',        'cat_hobbies',  'Интимные товары',     'sexual',        2),
        ('cat_other',         'cat_hobbies',  'Прочее',              'other',        99),
        # Подкатегории: Здоровье
        ('cat_health_med',    'cat_health',   'Лекарства',           'medicine',      1),
        ('cat_health_vit',    'cat_health',   'Витамины и БАДы',    'vitamins',      2),
    ]
    for cid, parent, name, slug, sort_order in categories:
        conn.execute('''
            INSERT OR IGNORE INTO categories (id, parent_id, name, slug, sort_order)
            VALUES (?, ?, ?, ?, ?)
        ''', (cid, parent, name, slug, sort_order))
    conn.commit()


def ensure_default_profile(conn):
    """Создаёт профиль по умолчанию."""
    conn.execute('''
        INSERT OR IGNORE INTO profiles (id, name) VALUES ('default', 'Default')
    ''')
    conn.commit()


def check_is_initialized(conn):
    """Проверяет, инициализирована ли БД (есть ли новая таблица profiles)."""
    tbls = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    return 'profiles' in tbls


def migrate(conn):
    """Переносит данные из старой схемы в новую."""
    old = load_old_data(conn)
    
    # 1. Перенос покупок
    migrated_purchase_ids = {}
    for p in old.get('purchases', []):
        cur = conn.execute('''
            INSERT OR IGNORE INTO purchases 
                (id, purchase_date, source, store_name, order_number, email_message_id, receipt_url, notes, data_origin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            p['id'], p['purchase_date'], p.get('source', 'ozon'),
            p.get('store_name', 'Ozon'), p.get('order_number'),
            p.get('email_uid'), p.get('receipt_url'), p.get('notes'),
            p.get('data_origin', 'local')
        ))
        if cur.lastrowid:
            migrated_purchase_ids[p['id']] = cur.lastrowid
    
    # 2. Перенос позиций в items
    for pi in old.get('purchase_items', []):
        conn.execute('''
            INSERT OR IGNORE INTO items 
                (name, status, purchase_id, purchase_source, data_origin)
            VALUES (?, 'in_use', ?, ?, 'local')
        ''', (pi['name'], pi.get('purchase_id'), 'ozon'))
    
    # 3. Перенос recognised_items
    for ri in old.get('recognized_items', []):
        conn.execute('''
            INSERT OR IGNORE INTO recognized_items_log 
                (source_file, source_type, recognized_product, confidence, notes)
            VALUES (?, ?, ?, ?, ?)
        ''', (ri.get('source_file', ''), ri.get('source_type', ''),
              ri['recognized_product'], ri.get('confidence', ''),
              ri.get('notes', '')))
    
    # 4. Перенос cheques_log
    for cl in old.get('cheques_log', []):
        conn.execute('''
            INSERT OR IGNORE INTO cheques_log 
                (email_uid, source, cheque_date, subject, receipt_url)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            cl.get('email_uid'), 'ozon',
            cl.get('cheque_date'), cl.get('subject'),
            cl.get('receipt_url')
        ))
    
    conn.commit()
    print(f"  Мигрировано: {len(old.get('purchases',[]))} покупок, {len(old.get('purchase_items',[]))} позиций, {len(old.get('recognized_items',[]))} распознанных товаров, {len(old.get('cheques_log',[]))} чеков")


def main():
    # Сначала просто пересоздадим БД, если нужна миграция
    # Для первого запуска удаляем старые таблицы и создаём новые
    db_exists = os.path.exists(DB_PATH)
    
    conn = connect(DB_PATH)
    
    if db_exists and check_is_initialized(conn):
        print("БД уже инициализирована. Пропускаю.")
        conn.close()
        return
    
    # Загружаем старые данные (если есть)
    old_data = load_old_data(conn) if db_exists else {}
    
    # Пересоздаём схему
    for tbl in ['subscriptions','alerts','cheques_log','recognized_items_log','items',
                'purchases','categories','profiles']:
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")

    
    create_new_schema(conn)
    ensure_default_profile(conn)
    seed_categories(conn)
    
    if old_data:
        # Переносим старые данные
        for table in OLD_TABLES:
            if table == 'purchases':
                for p in old_data.get('purchases', []):
                    conn.execute('''
                        INSERT OR IGNORE INTO purchases 
                            (id, purchase_date, source, store_name, order_number, email_message_id, receipt_url, notes, data_origin)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (p['id'], p['purchase_date'], p.get('source', 'ozon'),
                          p.get('store_name', 'Ozon'), p.get('order_number'),
                          p.get('email_uid'), p.get('receipt_url'), p.get('notes'),
                          p.get('data_origin', 'local')))
            elif table == 'purchase_items':
                for pi in old_data.get('purchase_items', []):
                    conn.execute('''
                        INSERT OR IGNORE INTO items 
                            (name, status, purchase_id, purchase_source, data_origin)
                        VALUES (?, 'in_use', ?, ?, 'local')
                    ''', (pi['name'], pi.get('purchase_id'), 'ozon'))
            elif table == 'recognized_items':
                for ri in old_data.get('recognized_items', []):
                    conn.execute('''
                        INSERT OR IGNORE INTO recognized_items_log 
                            (source_file, source_type, recognized_product, confidence, notes)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (ri.get('source_file', ''), ri.get('source_type', ''),
                          ri['recognized_product'], ri.get('confidence', ''),
                          ri.get('notes', '')))
            elif table == 'cheques_log':
                for cl in old_data.get('cheques_log', []):
                    conn.execute('''
                        INSERT OR IGNORE INTO cheques_log 
                            (email_uid, source, cheque_date, subject, receipt_url)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (cl.get('email_uid'), 'ozon', cl.get('cheque_date'),
                          cl.get('subject'), cl.get('receipt_url')))
        
        conn.commit()
        print(f"Миграция завершена. Перенесено: {len(old_data.get('purchases',[]))} покупок, {len(old_data.get('purchase_items',[]))} позиций, {len(old_data.get('recognized_items',[]))} распознанных товаров, {len(old_data.get('cheques_log',[]))} чеков")
    else:
        conn.commit()
        print("База данных инициализирована.")
    
    conn.close()


if __name__ == '__main__':
    main()
