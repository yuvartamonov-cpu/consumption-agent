"""
ml_inventory.py — Inventory Collision Check.

Stage 3/§7 of visual-product-search skill (text-only variant; CLIP-based
visual similarity is deferred to Stage 5).

Before showing a search recommendation to the user, look through their
existing `items` inventory for anything that already covers the same
need. If something matches strongly enough, the search response should
warn: "У вас уже есть похожее: «Парка Uniqlo», куплено N месяцев назад".

Match strategy (text-only):
    1. SQL pre-filter: not soft-deleted, brand/category narrow if known
    2. Build a "query text" from Vision attrs (subcategory + brand +
       model + colour + material)
    3. rapidfuzz token_set_ratio against each candidate's `name`
       (with a small boost when brand/model exact-match)
    4. Return hits ≥ threshold, sorted desc, capped

This module never raises on missing tables/columns — schema variants
across the project are handled defensively.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
    _HAS_FUZZ = True
except ImportError:
    fuzz = None  # type: ignore
    _HAS_FUZZ = False


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD = 75           # rapidfuzz token_set_ratio
DEFAULT_LIMIT = 5                # max hits returned
SQL_PREFETCH_LIMIT = 200         # how many candidates the SQL stage may yield
BRAND_BOOST = 10                 # similarity bonus if brand matches exactly
MODEL_BOOST = 10                 # similarity bonus if model substring match


# ---------------------------------------------------------------------------
# Build query text from attrs
# ---------------------------------------------------------------------------
def _attr(attrs: dict, key: str) -> str:
    v = attrs.get(key)
    if not v:
        return ''
    s = str(v).strip()
    if s.lower() in ('null', 'none', '—', '-'):
        return ''
    return s


def build_query_text(attrs: dict) -> str:
    """Compose a textual probe from Vision attributes.

    The order is: brand → model → subcategory → primary_color → material.
    Article alone (without context) makes a bad text-similarity probe;
    we use it only for the brand-boost step.
    """
    if not isinstance(attrs, dict):
        return ''
    parts = [
        _attr(attrs, 'brand'),
        _attr(attrs, 'model'),
        _attr(attrs, 'subcategory') or _attr(attrs, 'category'),
        _attr(attrs, 'primary_color'),
        _attr(attrs, 'material'),
    ]
    return ' '.join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# SQL pre-filter
# ---------------------------------------------------------------------------
def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in rows)
    except sqlite3.OperationalError:
        return False


def _fetch_candidates(conn: sqlite3.Connection, attrs: dict, limit: int) -> list[dict]:
    """Return a small pool of inventory items relevant to attrs.

    Filters (in order):
        - status != 'disposed'
        - deleted_at IS NULL (if column exists)
        - brand match if attrs.brand is known (LIKE, case-insensitive)
    Falls back to recent items if no constraints.
    """
    has_deleted_at = _has_column(conn, 'items', 'deleted_at')

    where = ["(status IS NULL OR status != 'disposed')"]
    params: list = []
    if has_deleted_at:
        where.append("deleted_at IS NULL")

    brand = _attr(attrs, 'brand')
    if brand:
        where.append("LOWER(brand) LIKE LOWER(?)")
        params.append(brand)
    # else don't constrain — we still want collision detection for
    # generic items like "белый чайник".

    sql = f"""
        SELECT id, name, brand, model, sku, purchase_date, purchase_price, status
        FROM items
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT ?
    """
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        log.warning("ml_inventory: SQL failed: %s", e)
        return []
    cols = ('id', 'name', 'brand', 'model', 'sku', 'purchase_date',
            'purchase_price', 'status')
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_candidate(query: str, attrs: dict, item: dict) -> int:
    """Compute similarity score for one inventory item vs the query.

    Score is in [0, 100], with explicit boosts when brand/model match.
    """
    if not _HAS_FUZZ:
        # Fallback: substring scoring (no rapidfuzz)
        return _fallback_score(query, attrs, item)

    name = (item.get('name') or '').strip()
    if not name and not item.get('brand'):
        return 0

    haystack = name
    if item.get('brand'):
        haystack = f"{item['brand']} {haystack}".strip()
    if item.get('model'):
        haystack = f"{haystack} {item['model']}".strip()

    base = int(fuzz.token_set_ratio(query.lower(), haystack.lower()))

    boost = 0
    q_brand = _attr(attrs, 'brand').lower()
    if q_brand and item.get('brand') and item['brand'].lower() == q_brand:
        boost += BRAND_BOOST
    q_model = _attr(attrs, 'model').lower()
    if q_model and item.get('model') and q_model in item['model'].lower():
        boost += MODEL_BOOST

    return min(100, base + boost)


def _fallback_score(query: str, attrs: dict, item: dict) -> int:
    """rapidfuzz-free heuristic. Coarse but deterministic."""
    q = query.lower()
    qtoks = set(t for t in q.split() if t)
    if not qtoks:
        return 0
    text = ((item.get('name') or '') + ' ' +
            (item.get('brand') or '') + ' ' +
            (item.get('model') or '')).lower()
    itoks = set(t for t in text.split() if t)
    if not itoks:
        return 0
    inter = qtoks & itoks
    score = int(100 * len(inter) / max(len(qtoks), 1))
    if _attr(attrs, 'brand').lower() == (item.get('brand') or '').lower() and item.get('brand'):
        score += BRAND_BOOST
    return min(100, score)


def find_inventory_collisions(
    conn: sqlite3.Connection,
    attrs: dict,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
    prefetch: int = SQL_PREFETCH_LIMIT,
) -> list[dict]:
    """Return inventory items that look similar to the target attrs.

    Each hit has: id, name, brand, model, purchase_date, purchase_price,
    status, similarity.
    """
    query = build_query_text(attrs)
    if not query and not _attr(attrs, 'brand'):
        return []

    candidates = _fetch_candidates(conn, attrs, prefetch)
    if not candidates:
        return []

    scored = []
    for it in candidates:
        s = score_candidate(query, attrs, it)
        if s >= threshold:
            scored.append({**it, 'similarity': s})

    scored.sort(key=lambda x: x['similarity'], reverse=True)
    return scored[:limit]


def format_collision_warning(collisions: list[dict], *, max_show: int = 2) -> Optional[str]:
    """Render a short user-facing warning string for Telegram.

    Returns None if collisions is empty.
    """
    if not collisions:
        return None
    lines = ["🟡 У вас уже есть похожее:"]
    for c in collisions[:max_show]:
        name = c.get('name') or '—'
        brand = c.get('brand') or ''
        pd = c.get('purchase_date') or ''
        label = f"«{name}»"
        if brand:
            label = f"«{name}» ({brand})"
        ago = f" — куплено {pd}" if pd else ''
        lines.append(f"  • {label}{ago} · совпадение {c['similarity']}%")
    if len(collisions) > max_show:
        lines.append(f"  • ещё {len(collisions) - max_show} похожих")
    return '\n'.join(lines)
