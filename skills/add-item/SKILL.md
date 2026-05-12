---
name: add-item
description: Add new items to the consumption database via Telegram bot. Use when the user wants to (1) add items via /add_item command with text input, (2) add items by uploading a photo with caption/description, (3) specify replacement period in days or months, (4) parse brand and item name from natural language input, (5) view full item details with photos via /items_full, (6) search items by name/brand/description/tags. Works with the consumption_agent Telegram bot and SQLite database.
---

# Add Item

## Overview

Skill for adding new items to the consumption inventory database through the Telegram bot. Supports both text commands and photo uploads with automatic brand/period parsing and Vision API enrichment.

## Commands

### /add_item

Add item via text command. Format:

```
/add_item <name> [<brand>] [<period>]
```

**Period formats:**
- `6 мес` / `6 месяцев` / `6 months` — replacement in 6 months
- `30 дн` / `30 дней` / `30 days` — replacement in 30 days (exact, stored as days)
- `2 года` / `2 years` — replacement in 24 months

**Examples:**
- `/add_item носки Nike 6 мес`
- `/add_item пиджак Corneliani 90 дней`
- `/add_item футболка` (no replacement period)

### Photo Upload

Send photo with caption describing the item. The bot auto-detects item descriptions and redirects to /add_item handler.

**Caption formats:**
- `Нравится пиджак Corneliani замена 3 мес`
- `поло Hemington 60 дней`
- `куртка` (item name only)

**Vision API enrichment** (when photo uploaded):
- Color detection
- Material identification
- Style tags (formal, casual, etc.)
- Description generation
- Estimated price in rubles

**Photo without caption** — auto-classified as item/clothing/food/etc. and added to inventory with Vision API data.

## Viewing Items

### /items all
Standard list view — grouped by category, shows items with replacement ≤90 days by default.

### /items_full
Full detailed view with search support:

- `/items_full all` — show all items
- `/items_full пиджак` — search by name
- `/items_full corneliani` — search by brand
- `/items_full formal` — search by style tags
- `/items_full синий` — search by color/description
- `/items_full` (no args) — items with replacement ≤30 days (with 🔴/🟡)

Each item shows:
- All attributes (color, material, tags, description, price)
- 📷 **Фото** button — sends item photo from database
- 🗑 **Удалить** button — appears when replacement ≤30 days
- Exact day calculation (not rounded to months)

## Workflow

1. **Parse input** — Extract name, brand, replacement period via `brand_parser.py`
2. **Determine category** — Auto-assign based on keywords (clothing, tech, home, etc.)
3. **Calculate dates** — `purchase_date = today`, exact days stored in `replace_after_days`
4. **Generate description** — Add "Ожидается замена через X дн./мес." to notes
5. **Save to DB** — Insert into `items` table with `data_origin = 'manual'`
6. **Vision API** (if photo) — Enrich with color, material, style tags, save to `attributes` and `notes`
7. **Photo storage** — Save to `media_assets` + link in `item_photos`

## Database Schema

Table: `items`
- `name` — item name (required)
- `brand` — brand name (optional, parsed from input or Vision API)
- `category_id` — auto-assigned category
- `replace_after_months` — replacement period in months (approximate, for compatibility)
- `replace_after_days` — **exact** replacement period in days (new, precise calculation)
- `purchase_date` — date added
- `notes` — includes "Добавлено через /add_item" and replacement info
- `attributes` — JSON with Vision API data: color, material, description, style_tags, estimated_price_rub
- `data_origin` — `'manual'` for /add_item, `'vision_photo'` for photo uploads

## Key Files

- `telegram_bot.py` — Command handlers (`cmd_add_item`, `cmd_items`, `cmd_items_full`, `photo_handler`)
- `brand_parser.py` — Natural language parsing (name, brand, period with days/months/years)
- `vision_item.py` — Photo enrichment via GPT-4o-mini

## Category Mapping

Auto-assigned based on keywords in item name:
- Clothing: `пиджак`, `поло`, `футболка`, `куртка`, `джинсы`, `носки` → `cat_clo_*`
- Tech: `телефон`, `ноутбук`, `наушники` → `cat_tech`
- Home: `стремянка`, `пылесос`, `чайник` → `cat_home*` / `cat_home_kitchen`
- Shoes: `кроссовки`, `ботинки`, `туфли` → `cat_clo_shoes`
- Full mapping in `telegram_bot.py` → `cat_map` dict

## Notes

- `/items` (without `all`) shows only items with replacement ≤90 days
- `/items all` — standard compact view
- `/items_full all` — full view with photos, attributes, and action buttons
- `/items_full <query>` — search across name, brand, description, tags
- Items with `replace_after_days` show exact day count (e.g., "20 дн.", not "1 мес.")
- Vision API data is stored in both `attributes` (JSON) and `notes` (text), but displayed without duplication in `/items_full`
- Photo without caption is auto-classified and added with `data_origin = 'vision_photo'`
