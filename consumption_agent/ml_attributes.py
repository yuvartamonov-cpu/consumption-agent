"""
ml_attributes.py — Structured attribute extraction for Memory Lane items.

Stage 1 of visual-product-search skill: replaces the flat `search_query`
text field with a strictly-typed JSON schema returned by Vision API.

These structured attributes are consumed by ml_query_expansion.py to
generate per-marketplace queries with different specificity levels.

Schema:
    {
        category, subcategory, brand, model, article,
        primary_color, secondary_colors[],
        material, fit, length, season,
        style[], gender,
        estimated_price_rub, confidence
    }
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
VALID_CATEGORIES = {
    'одежда', 'обувь', 'техника', 'мебель', 'интерьер',
    'косметика', 'аксессуары', 'еда', 'другое',
}
VALID_FITS = {'oversize', 'regular', 'slim', None}
VALID_LENGTHS = {'mini', 'midi', 'maxi', 'cropped', 'regular', None}
VALID_SEASONS = {'winter', 'spring', 'summer', 'autumn', 'all', None}
VALID_GENDERS = {'male', 'female', 'unisex', None}

DEFAULT_ATTRIBUTES = {
    'category': None,
    'subcategory': None,
    'brand': None,
    'model': None,
    'article': None,
    'primary_color': None,
    'secondary_colors': [],
    'material': None,
    'fit': None,
    'length': None,
    'season': None,
    'style': [],
    'gender': None,
    'estimated_price_rub': None,
    'confidence': 0.0,
}

ATTRIBUTES_PROMPT = """Посмотри на фото и извлеки структурированные атрибуты товара.
Верни ТОЛЬКО валидный JSON (без markdown, без ```), используя ровно эту схему:

{
  "category": "одежда|обувь|техника|мебель|интерьер|косметика|аксессуары|еда|другое",
  "subcategory": "узкий тип товара (например: пальто, кроссовки, чайник, кресло) или null",
  "brand": "бренд если виден, иначе null",
  "model": "модель/линейка если узнаётся (Air Force 1, iPhone 15) или null",
  "article": "артикул/код модели если видно на бирке/ценнике, иначе null",
  "primary_color": "доминирующий цвет на русском (например: серый, тёмно-синий) или null",
  "secondary_colors": ["дополнительный цвет 1", "..."],
  "material": "материал если можно определить (шерсть, кожа, хлопок, металл, пластик) или null",
  "fit": "oversize|regular|slim или null (для одежды)",
  "length": "mini|midi|maxi|cropped|regular или null (для одежды)",
  "season": "winter|spring|summer|autumn|all или null",
  "style": ["casual","sport","formal","streetwear","minimalism","retro","grunge","preppy"],
  "gender": "male|female|unisex или null (для одежды/обуви)",
  "estimated_price_rub": целое число в рублях или null,
  "confidence": число от 0.0 до 1.0
}

Правила:
- category — строго один из перечисленных вариантов
- subcategory — узкое, нарицательное название в единственном числе
- brand/model/article — null если не виден или неуверен
- primary_color — основной цвет (если палитра, выбери самый заметный)
- style — массив 1-3 стилевых тэгов; для не-одежды можно []
- confidence отражает суммарную уверенность атрибуции (0.0=наугад, 1.0=точно)
- Не добавляй полей, которых нет в схеме
- Не используй markdown, только JSON"""


# ---------------------------------------------------------------------------
# Schema ensure
# ---------------------------------------------------------------------------
def ensure_attributes_column(conn: sqlite3.Connection) -> None:
    """Add attributes_json column to memory_lane_items if missing. Idempotent."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_lane_items)").fetchall()}
    if 'attributes_json' not in cols:
        conn.execute("ALTER TABLE memory_lane_items ADD COLUMN attributes_json TEXT")
        conn.commit()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_attributes(data: dict) -> dict:
    """Type-check the dict and fill defaults. Returns a clean copy."""
    out = dict(DEFAULT_ATTRIBUTES)
    if not isinstance(data, dict):
        return out

    # Strings
    for k in ('subcategory', 'brand', 'model', 'article',
              'primary_color', 'material'):
        v = data.get(k)
        if isinstance(v, str) and v.strip() and v.strip().lower() not in ('null', 'none', ''):
            out[k] = v.strip()

    # Enums
    cat = data.get('category')
    if isinstance(cat, str):
        cat_lc = cat.strip().lower()
        if cat_lc in VALID_CATEGORIES:
            out['category'] = cat_lc

    for k, valid in (('fit', VALID_FITS), ('length', VALID_LENGTHS),
                     ('season', VALID_SEASONS), ('gender', VALID_GENDERS)):
        v = data.get(k)
        if isinstance(v, str) and v.strip().lower() in {x for x in valid if x}:
            out[k] = v.strip().lower()

    # Lists of strings
    for k in ('secondary_colors', 'style'):
        v = data.get(k)
        if isinstance(v, list):
            out[k] = [s.strip() for s in v if isinstance(s, str) and s.strip()][:5]

    # Numbers
    price = data.get('estimated_price_rub')
    if isinstance(price, (int, float)) and price > 0:
        out['estimated_price_rub'] = int(price)

    conf = data.get('confidence')
    if isinstance(conf, (int, float)):
        out['confidence'] = max(0.0, min(1.0, float(conf)))

    return out


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def _parse_vision_json(text: str) -> dict:
    """Strip optional markdown fences and parse JSON. Returns {} on failure."""
    if not text:
        return {}
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'\s*```$', '', text.strip(), flags=re.MULTILINE)
    # Try whole text, then extract first {...} block as fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if not m:
            return {}
        try:
            return json.loads(m.group())
        except json.JSONDecodeError as e:
            log.warning("ml_attributes: invalid JSON from Vision: %s", e)
            return {}


def extract_attributes(image_path: str, caption: str = "",
                       vision_caller=None) -> dict:
    """Call Vision API and return validated attribute dict.

    `vision_caller` is injectable for testing — by default it's
    vision_item._call_vision_with_timeout. Must return (text, timed_out).
    """
    if vision_caller is None:
        try:
            from vision_item import _call_vision_with_timeout as vision_caller  # type: ignore
        except ImportError:
            log.error("vision_item module unavailable")
            return validate_attributes({})

    prompt = ATTRIBUTES_PROMPT
    if caption:
        prompt += f"\n\nПодпись пользователя (для контекста): {caption}"

    try:
        text, timed_out = vision_caller(image_path, prompt, max_tokens=600, timeout=30.0)
        if timed_out:
            log.warning("ml_attributes: Vision API timed out for %s", image_path)
            return validate_attributes({})
        raw = _parse_vision_json(text)
        return validate_attributes(raw)
    except Exception as e:
        log.error("ml_attributes.extract_attributes failed: %s", e)
        return validate_attributes({})


async def extract_attributes_async(image_path: str, caption: str = "") -> dict:
    """Async wrapper around extract_attributes."""
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, extract_attributes, image_path, caption, None)


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------
def save_attributes(conn: sqlite3.Connection, item_id: int, attrs: dict) -> None:
    """Persist attribute dict as JSON in memory_lane_items.attributes_json."""
    ensure_attributes_column(conn)
    conn.execute(
        "UPDATE memory_lane_items SET attributes_json = ? WHERE id = ?",
        (json.dumps(attrs, ensure_ascii=False), item_id),
    )
    conn.commit()


def load_attributes(conn: sqlite3.Connection, item_id: int) -> Optional[dict]:
    """Load attributes for a memory_lane_items row. Returns None if missing/empty."""
    row = conn.execute(
        "SELECT attributes_json FROM memory_lane_items WHERE id = ?", (item_id,)
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return validate_attributes(json.loads(row[0]))
    except json.JSONDecodeError:
        return None
