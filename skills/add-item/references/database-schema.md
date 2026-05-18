# Database Schema Reference

## items table

```sql
CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE,
    source_type TEXT,
    source_id TEXT,
    category_id TEXT,
    name TEXT NOT NULL,
    brand TEXT,
    model TEXT,
    serial_number TEXT,
    barcode TEXT,
    price REAL,
    currency TEXT DEFAULT 'RUB',
    purchase_date TEXT,
    warranty_months INTEGER,
    warranty_expiry TEXT,
    lifespan_months INTEGER,
    status TEXT DEFAULT 'in_use',
    location TEXT,
    notes TEXT,
    attributes TEXT DEFAULT '{}',
    receipt_id INTEGER,
    is_delivery INTEGER DEFAULT 0,
    delivery_status TEXT,
    delivery_tracking TEXT,
    delivery_expected_date TEXT,
    data_origin TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    replace_after_months INTEGER,
    replace_notified_at TEXT
);
```

## Key Fields for /add_item

| Field | Source | Example |
|-------|--------|---------|
| name | Parsed from input | `пиджак` |
| brand | Parsed or Vision API | `Corneliani` |
| category_id | Auto-mapped | `cat_clo_everyday` |
| replace_after_months | Parsed + converted | `3` (from "90 дней") |
| purchase_date | Auto-set | `2026-05-12` |
| notes | Generated | `Добавлено через /add_item\nОжидается замена через 90 дн.` |
| data_origin | Fixed value | `manual` |

## Category IDs

| ID | Description |
|----|-------------|
| cat_clo_everyday | Повседневная одежда |
| cat_clo_underwear | Нижнее бельё / носки |
| cat_clo_shoes | Обувь |
| cat_clo_access | Аксессуары |
| cat_tech | Техника |
| cat_home | Хозтовары |
| cat_home_furn | Мебель |
| cat_home_kitchen | Кухня |
| cat_cosmetics | Косметика |
| cat_health_med | Здоровье |
| cat_culture_books | Книги |
| cat_hobbies | Хобби |
| cat_pets | Животные |
| cat_other | Прочее |
