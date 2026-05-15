# Vision API Integration

## recognize_item(image_path)

Analyzes product photo and returns structured data.

### Return Value

```python
{
    'type': 'clothing',
    'name': 'пиджак',
    'brand': 'Corneliani',
    'category': 'cat_clo_everyday',
    'color': 'темно-синий',
    'material': 'шерсть',
    'style_tags': ['formal', 'casual'],
    'description': 'Темно-синий пиджак с текстурированной поверхностью...',
    'estimated_price_rub': 15000
}
```

## Data Storage

Vision data is saved in two places:

### 1. attributes (JSON)
```json
{
    "color": "темно-синий",
    "description": "Темно-синий пиджак...",
    "style_tags": ["formal", "casual"],
    "material": "шерсть",
    "estimated_price_rub": 15000
}
```

### 2. notes (text)
```
Добавлено через /add_item
Ожидается замена через 20 дн.
Цвет: темно-синий
Материал: шерсть
Описание: Темно-синий пиджак...
Оценочная цена: ~15000 ₽
```

### Display in /items_full
- Attributes shown as structured fields (🎨 Цвет, 🧵 Материал, etc.)
- Notes filtered to remove duplicate Vision lines
- Only unique info (like replacement period) shown from notes

## Photo Storage Flow

1. User sends photo with caption
2. Photo saved to `media_assets` table (file_path = hash.jpg)
3. Link created in `item_photos` (item_id → media_asset_id)
4. Vision API analyzes photo
5. Data saved to `items.attributes` and `items.notes`

## Photo Retrieval in /items_full

```sql
SELECT ma.file_path 
FROM item_photos ip
JOIN media_assets ma ON ip.media_asset_id = ma.id
WHERE ip.item_id = ?
```

Button "📷 Фото" sends photo from `file_path` when clicked.
