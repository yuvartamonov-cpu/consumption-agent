#!/usr/bin/env python3
"""
memory_lane.py — Phase B fast path: save user-tagged photos.

When the owner sends a photo with a caption like "нравится", "запомни",
"#пальто", we save the image (deduped by sha256) plus a small JSON of
taste signals (liked / disliked / style_tags / topic). Embedding search,
LLM-driven enrichment and `/ml_profile` are deliberately NOT in this slice.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from typing import Iterable, List

# Keyword sets are intentionally simple. They match whole words (case-insensitive)
# against the caption. Russian and a few English tokens.
LIKED_KEYWORDS = (
    'нравится', 'нравиться', 'нравиться', 'классно', 'круто', 'хочу',
    'купить', 'добавить', 'запомни', 'запомнить', 'сохрани', 'сохранить',
    'find similar', 'найди похожее', 'похожее', 'like',
)
DISLIKED_KEYWORDS = (
    'не нравится', 'не нравиться', 'не хочу', 'уродство', 'ужас', 'фу',
    'dislike',
)
SAVE_TRIGGERS = LIKED_KEYWORDS + DISLIKED_KEYWORDS + ('memory lane',)

TOPIC_RULES = {
    'одежда': ('одежда', 'пальто', 'куртка', 'платье', 'рубашка', 'джинс',
               'свитер', 'футболк', 'юбка', 'брюки', 'кросс', 'обувь', 'ботинк',
               'пиджак', 'пуловер', 'костюм', 'кофт', 'толстовк', 'худи',
               'жилет', 'шарф', 'шапк', 'кепк', 'перчатк', 'ремень', 'галстук',
               'носк', 'колготк', 'плавк', 'купальник', 'халат', 'пижам'),
    'мебель': ('мебель', 'диван', 'кресло', 'стол', 'стул', 'шкаф', 'кровать',
               'комод', 'полка', 'стеллаж', 'трюмо', 'тумб', 'вешалк'),
    'интерьер': ('интерьер', 'светильник', 'лампа', 'ковёр', 'ковер', 'штора',
                 'плед', 'подушк', 'ваз', 'картин', 'постер', 'свеч',
                 'декор', 'зеркал'),
    'еда': ('еда', 'блюдо', 'ресторан', 'кафе', 'торт', 'напиток', 'завтрак',
            'обед', 'ужин', 'кофе', 'чай', 'суп', 'салат', 'десерт'),
    'техника': ('техника', 'ноутбук', 'телефон', 'наушник', 'зарядк', 'монитор',
                'клавиатур', 'мышк', 'планшет', 'колонк', 'роутер', 'камер',
                'принтер', 'провод'),
    'аксессуары': ('аксессуар', 'браслет', 'кольцо', 'серёжк', 'часы', 'сумк',
                   'рюкзак', 'кошелек', 'портмоне', 'очк'),
    'косметика': ('косметик', 'духи', 'парфюм', 'крем', 'лосьон', 'шампунь',
                  'бальзам', 'помад', 'тени', 'тушь'),
}

MEDIA_SUBDIR = os.path.join('data', 'media')

# Reused by Telegram handler to gate the branch.
_TRIGGER_RX = re.compile(r'(' + '|'.join(re.escape(k) for k in SAVE_TRIGGERS) + r')', re.IGNORECASE)
_HASHTAG_RX = re.compile(r'#([\w\-]+)', re.UNICODE)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_memory_lane_schema(conn: sqlite3.Connection) -> None:
    """Create memory_lane_items, media_assets and the topic index. Idempotent."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            sha256 TEXT UNIQUE,
            mime TEXT,
            size_bytes INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_lane_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT DEFAULT (datetime('now')),
            caption TEXT,
            liked_features TEXT DEFAULT '[]',
            disliked_features TEXT DEFAULT '[]',
            style_tags TEXT DEFAULT '[]',
            topic TEXT,
            media_asset_id INTEGER REFERENCES media_assets(id),
            source TEXT DEFAULT 'telegram'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ml_topic ON memory_lane_items(topic)")
    # Таблица ассоциаций слово → тема (обучаемая, user и default правила)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS topic_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            topic TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            usage_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_topic_rules_keyword ON topic_rules(keyword)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_topic_rules_topic ON topic_rules(topic)")
    conn.commit()
    # Засеиваем дефолтные правила из TOPIC_RULES
    seed_default_topic_rules(conn)


def seed_default_topic_rules(conn: sqlite3.Connection) -> None:
    """Заполняет таблицу topic_rules дефолтными правилами из TOPIC_RULES, если их там нет."""
    for topic, keywords in TOPIC_RULES.items():
        for kw in keywords:
            conn.execute(
                "INSERT OR IGNORE INTO topic_rules (keyword, topic, source) VALUES (?, ?, 'default')",
                (kw, topic)
            )
    conn.commit()


def lookup_topic(conn: sqlite3.Connection, text: str) -> str | None:
    """Ищет тему по тексту в таблице topic_rules. Возвращает первую подходящую или None."""
    if not text:
        return None
    lowered = text.lower()
    # Сначала ищем точное совпадение целого слова
    rows = conn.execute(
        "SELECT keyword, topic FROM topic_rules ORDER BY usage_count DESC"
    ).fetchall()
    for keyword, topic in rows:
        if keyword in lowered:
            # Обновляем счётчик использования
            conn.execute(
                "UPDATE topic_rules SET usage_count = usage_count + 1, updated_at = datetime('now') WHERE keyword = ?",
                (keyword,)
            )
            return topic
    return None


def set_topic_rule(conn: sqlite3.Connection, keyword: str, topic: str) -> bool:
    """Добавляет или обновляет правило ассоциации слово → тема.
    Возвращает True, если создано новое."""
    existing = conn.execute(
        "SELECT id, source FROM topic_rules WHERE keyword = ?", (keyword.lower(),)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE topic_rules SET topic = ?, source = 'user', updated_at = datetime('now') WHERE id = ?",
            (topic, existing[0])
        )
        return False
    else:
        conn.execute(
            "INSERT INTO topic_rules (keyword, topic, source) VALUES (?, ?, 'user')",
            (keyword.lower(), topic)
        )
        return True


def list_topic_rules(conn: sqlite3.Connection, topic: str | None = None) -> list[dict]:
    """Возвращает все правила, опционально отфильтрованные по теме."""
    if topic:
        rows = conn.execute(
            "SELECT keyword, topic, source, usage_count FROM topic_rules WHERE topic = ? ORDER BY usage_count DESC, keyword",
            (topic,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT keyword, topic, source, usage_count FROM topic_rules ORDER BY topic, usage_count DESC"
        ).fetchall()
    return [{'keyword': r[0], 'topic': r[1], 'source': r[2], 'usage_count': r[3]} for r in rows]


# ---------------------------------------------------------------------------
# Caption parser
# ---------------------------------------------------------------------------
def is_memory_lane_caption(text: str | None) -> bool:
    """True if the caption contains any save-trigger keyword or a hashtag."""
    if not text:
        return False
    if _TRIGGER_RX.search(text):
        return True
    return bool(_HASHTAG_RX.search(text))


def _contains_any(haystack: str, needles: Iterable[str]) -> List[str]:
    hits = []
    for n in needles:
        if n.lower() in haystack:
            hits.append(n)
    return hits


def parse_caption(text: str | None, conn: sqlite3.Connection | None = None) -> dict:
    """Return a small dict of taste signals extracted from the caption.

    Keys: liked, disliked, style_tags, topic.  Lists are deduped while
    preserving insertion order.

    Если передан conn, сначала проверяет таблицу topic_rules (user-правила),
    потом статические TOPIC_RULES.
    """
    out = {'liked': [], 'disliked': [], 'style_tags': [], 'topic': None,
           'item_name': None, 'brand': None, 'replace_months': None}
    if not text:
        return out

    # Извлекаем название и бренд через brand_parser
    try:
        from brand_parser import parse_brand_and_name
        bp = parse_brand_and_name(text)
        out['item_name'] = bp.get('name')
        out['brand'] = bp.get('brand')
        out['replace_months'] = bp.get('replace_months')
    except Exception:
        pass

    lowered = text.lower()

    # Check disliked FIRST so "не нравится" doesn't also count as liked.
    disliked_hits = _contains_any(lowered, DISLIKED_KEYWORDS)
    if disliked_hits:
        out['disliked'] = list(dict.fromkeys(disliked_hits))

    # Build a scratch text where matched dislike phrases are removed,
    # so the liked scan won't see "не нравится" -> "нравится".
    liked_scratch = lowered
    for d in disliked_hits:
        liked_scratch = liked_scratch.replace(d.lower(), ' ')
    liked_hits = _contains_any(liked_scratch, LIKED_KEYWORDS)
    if liked_hits:
        out['liked'] = list(dict.fromkeys(liked_hits))

    # Style tags = hashtags, normalised to lower-case without '#'.
    tags = [h.lower() for h in _HASHTAG_RX.findall(text)]
    if tags:
        out['style_tags'] = list(dict.fromkeys(tags))

    # Topic detection: сначала таблица topic_rules, потом статические правила
    if conn:
        topic = lookup_topic(conn, lowered)
        if topic:
            out['topic'] = topic
            return out

    for topic, words in TOPIC_RULES.items():
        if any(w in lowered for w in words):
            out['topic'] = topic
            break

    return out


# ---------------------------------------------------------------------------
# Media storage
# ---------------------------------------------------------------------------
def save_media(
    conn: sqlite3.Connection,
    file_bytes: bytes,
    mime: str = 'image/jpeg',
    base_dir: str | None = None,
) -> int:
    """Save bytes to data/media/<sha256>.<ext> and a media_assets row.

    Returns the asset id. If a row with the same sha256 already exists,
    returns its id and does not rewrite the file.
    """
    ensure_memory_lane_schema(conn)
    sha = hashlib.sha256(file_bytes).hexdigest()

    existing = conn.execute(
        "SELECT id FROM media_assets WHERE sha256 = ?", (sha,)
    ).fetchone()
    if existing:
        return existing[0]

    if base_dir is None:
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), MEDIA_SUBDIR)
    os.makedirs(base_dir, exist_ok=True)

    ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}.get(mime, '.bin')
    file_path = os.path.join(base_dir, f'{sha}{ext}')
    if not os.path.exists(file_path):
        with open(file_path, 'wb') as fh:
            fh.write(file_bytes)

    cur = conn.execute(
        "INSERT INTO media_assets (file_path, sha256, mime, size_bytes) VALUES (?, ?, ?, ?)",
        (file_path, sha, mime, len(file_bytes)),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Memory Lane writes / reads
# ---------------------------------------------------------------------------
def save_memory_lane(
    conn: sqlite3.Connection,
    caption: str,
    media_asset_id: int | None,
    parsed: dict | None = None,
    source: str = 'telegram',
    vision_info: dict | None = None,
) -> int:
    """Insert a memory_lane_items row using parsed signals from parse_caption.
    
    vision_info: результат enrich_memory_lane (name, description, brand, color, estimated_price_rub)
    """
    ensure_memory_lane_schema(conn)
    # Добавляем колонки name/description/brand, если их нет
    _ensure_vision_columns(conn)
    parsed = parsed if parsed is not None else parse_caption(caption, conn)
    
    name = None
    description = None
    brand = None
    if vision_info and vision_info.get('name'):
        name = vision_info.get('name')
        description = vision_info.get('description')
        brand = vision_info.get('brand')
    
    cur = conn.execute(
        """
        INSERT INTO memory_lane_items
            (caption, liked_features, disliked_features, style_tags, topic, media_asset_id, source, name, description, brand)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            caption,
            json.dumps(parsed.get('liked', []), ensure_ascii=False),
            json.dumps(parsed.get('disliked', []), ensure_ascii=False),
            json.dumps(parsed.get('style_tags', []), ensure_ascii=False),
            parsed.get('topic'),
            media_asset_id,
            source,
            name,
            description,
            brand,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _ensure_vision_columns(conn):
    """Добавляет колонки name, description, brand в memory_lane_items, если их нет."""
    for col, col_type in [('name', 'TEXT'), ('description', 'TEXT'), ('brand', 'TEXT')]:
        try:
            conn.execute(f'ALTER TABLE memory_lane_items ADD COLUMN {col} {col_type}')
        except sqlite3.OperationalError:
            pass  # уже существует


def list_recent(conn: sqlite3.Connection, n: int = 10, topic: str | None = None) -> list:
    """Return last N memory_lane rows, newest first. Optionally filter by topic."""
    ensure_memory_lane_schema(conn)
    _ensure_vision_columns(conn)
    if topic:
        rows = conn.execute(
            """
            SELECT id, created_at, caption, style_tags, topic, media_asset_id,
                   name, description, brand
            FROM memory_lane_items
            WHERE topic = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (topic, n),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, created_at, caption, style_tags, topic, media_asset_id,
                   name, description, brand
            FROM memory_lane_items
            ORDER BY id DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
    return rows
