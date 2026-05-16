"""
ml_taste.py — Taste Profile Re-Ranker.

Stage 4/§8 of visual-product-search skill. Reads the user's
Memory Lane history, builds a time-decayed token-frequency profile
of what they like vs dislike, and uses it to re-rank candidate
search results.

Pipeline:
    1. build_taste_profile(conn) → {positive_tokens, negative_tokens}
       Each token weighted by exp(-age_days / decay_days), so recent
       reactions matter more than old ones. Tokens are extracted from
       style_tags, topic, brand, vision name/description, and the
       structured attributes_json (Stage 1).

    2. taste_score(candidate_text, profile) → float in [-1, 1]
       Positive matches add up, negative matches subtract, the sum
       is squashed through tanh.

    3. rank_candidates(canonical_rows, profile, attrs=None) →
       sorted list with combined score:
           final = w_taste · taste + w_trust · trust + w_price · price_advantage

       Visual similarity (Stage 5/CLIP) will join the formula once the
       embeddings are available — until then its 0.40 weight is
       redistributed across taste/trust/price.

Pure logic — DB access is parameterised so tests use in-memory data.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from collections import defaultdict
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source trust map (used in §3 brand-authority cascade)
# ---------------------------------------------------------------------------
SOURCE_TRUST: dict[str, float] = {
    # Tier 1 — official brand sites (caller passes 'brand_site')
    'brand_site': 1.00,
    # Tier 2 — authorized retailers
    'lamoda': 0.90, 'brandshop': 0.90, 'sneakerhead': 0.90,
    # Tier 4 — category specialists (medium-high)
    'dns': 0.85, 'citilink': 0.85, 'mvideo': 0.85,
    'hoff': 0.85, 'mrdoors': 0.85, 'ikea': 0.85,
    'goldapple': 0.85, 'iledebeaute': 0.85,
    # Tier 3 — generalist marketplaces
    'ozon': 0.70, 'wildberries': 0.70, 'wb': 0.70,
    'yandex_market': 0.70, 'yandex market': 0.70, 'ym': 0.70,
    'я.маркет': 0.70, 'яндекс.маркет': 0.70,
    # Tier 5 — C2C / grey market
    'avito': 0.40, 'юла': 0.40, 'youla': 0.40,
}

DEFAULT_TRUST = 0.50    # unknown source


def get_trust(source: Optional[str]) -> float:
    """Fuzzy-lookup trust score for a source name. Default 0.50."""
    if not source:
        return DEFAULT_TRUST
    s = str(source).strip().lower()
    if s in SOURCE_TRUST:
        return SOURCE_TRUST[s]
    # Substring fallback — handle "Yandex.Market via SerpAPI", "Ozon Express",
    # etc. We collapse [._-\s] to '' so 'yandex.market' and 'yandex_market'
    # both match the canonical 'yandexmarket' core.
    def _core(x: str) -> str:
        return re.sub(r'[\s._\-]+', '', x)
    s_core = _core(s)
    for key, v in SOURCE_TRUST.items():
        k_core = _core(key)
        if k_core and (k_core in s_core or s_core in k_core):
            return v
    return DEFAULT_TRUST


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------
_WORD_RX = re.compile(r"[\w]+", re.UNICODE)

# Tokens that contribute no taste signal (mirrors ml_canonical stop list +
# the trigger words that go into liked_features / disliked_features).
_STOP = frozenset({
    # Stop words
    'для', 'с', 'и', 'или', 'без', 'на', 'из', 'в', 'по', 'от', 'до',
    'это', 'эта', 'эти', 'тот', 'та', 'не',
    # Trigger words (stored in liked_features but not informative as taste)
    'нравится', 'нравиться', 'классно', 'круто', 'хочу',
    'купить', 'добавить', 'запомни', 'запомнить', 'сохрани', 'сохранить',
    'похожее', 'like', 'find', 'similar',
    'не нравится', 'не нравиться', 'не хочу', 'уродство', 'ужас', 'фу',
    'dislike',
    'memory', 'lane',
    # Generic noise
    'купить', 'оригинал', 'оригинальный', 'sale', 'новый',
})


def _tokens(text: Any) -> list[str]:
    """Lowercase Unicode tokens, drop stop-words and length-1 fragments."""
    if not text:
        return []
    if isinstance(text, (list, tuple, set)):
        out: list[str] = []
        for piece in text:
            out.extend(_tokens(piece))
        return out
    s = str(text).lower().lstrip('#').strip()
    if not s or s in _STOP:
        return []
    raw = _WORD_RX.findall(s)
    return [t for t in raw if len(t) > 1 and t not in _STOP]


# ---------------------------------------------------------------------------
# Profile construction
# ---------------------------------------------------------------------------
def _decay_weight(age_days: float, decay_days: float) -> float:
    """exp(-age / decay). Clamp age at 0 (future dates → weight 1)."""
    if age_days <= 0 or decay_days <= 0:
        return 1.0
    return math.exp(-age_days / decay_days)


def _item_sentiment(liked_raw: str, disliked_raw: str) -> int:
    """+1 positive, -1 negative, 0 neutral / ambiguous (skip)."""
    try:
        liked = json.loads(liked_raw or '[]') or []
    except (TypeError, json.JSONDecodeError):
        liked = []
    try:
        disliked = json.loads(disliked_raw or '[]') or []
    except (TypeError, json.JSONDecodeError):
        disliked = []
    has_pos = bool(liked)
    has_neg = bool(disliked)
    if has_pos and not has_neg:
        return 1
    if has_neg and not has_pos:
        return -1
    return 0   # both empty OR both filled — ambiguous, skip


def _extract_item_tokens(row: dict) -> list[str]:
    """Collect all descriptive tokens from one memory_lane_items row."""
    out: list[str] = []

    # style_tags is JSON of strings
    try:
        tags = json.loads(row.get('style_tags') or '[]') or []
    except (TypeError, json.JSONDecodeError):
        tags = []
    out.extend(_tokens(tags))

    # Simple text fields
    for k in ('topic', 'brand', 'name', 'description'):
        out.extend(_tokens(row.get(k)))

    # attributes_json from Stage 1 (when available)
    raw_attrs = row.get('attributes_json')
    if raw_attrs:
        try:
            attrs = json.loads(raw_attrs)
        except (TypeError, json.JSONDecodeError):
            attrs = {}
        if isinstance(attrs, dict):
            for k in ('subcategory', 'category', 'brand', 'model',
                      'primary_color', 'material', 'fit', 'length',
                      'season', 'gender'):
                out.extend(_tokens(attrs.get(k)))
            out.extend(_tokens(attrs.get('secondary_colors')))
            out.extend(_tokens(attrs.get('style')))

    return out


def build_taste_profile(
    conn: sqlite3.Connection,
    *,
    decay_days: float = 180.0,
    profile_id: str = 'default',
) -> dict:
    """Aggregate user's Memory Lane history into a taste profile.

    Returns:
        {
            'positive': {token: weight},
            'negative': {token: weight},
            'n_positive_items': int,
            'n_negative_items': int,
            'decay_days': float,
        }
    """
    has_deleted_at = _has_column(conn, 'memory_lane_items', 'deleted_at')
    has_attrs_json = _has_column(conn, 'memory_lane_items', 'attributes_json')
    has_name = _has_column(conn, 'memory_lane_items', 'name')

    select_cols = [
        'liked_features', 'disliked_features', 'style_tags', 'topic',
        "julianday('now') - julianday(created_at) AS age_days",
    ]
    if has_name:
        select_cols += ['name', 'description', 'brand']
    else:
        select_cols += ["NULL AS name", "NULL AS description", "NULL AS brand"]
    if has_attrs_json:
        select_cols.append('attributes_json')
    else:
        select_cols.append("NULL AS attributes_json")

    where = ['profile_id = ?']
    params: list[Any] = [profile_id]
    if has_deleted_at:
        where.append('deleted_at IS NULL')

    sql = (
        f"SELECT {', '.join(select_cols)} FROM memory_lane_items "
        f"WHERE {' AND '.join(where)}"
    )
    try:
        cur = conn.execute(sql, params)
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    except sqlite3.OperationalError as e:
        log.warning("ml_taste: cannot read memory_lane_items: %s", e)
        return _empty_profile(decay_days)

    positive: dict[str, float] = defaultdict(float)
    negative: dict[str, float] = defaultdict(float)
    n_pos = n_neg = 0

    for row in rows:
        sentiment = _item_sentiment(row.get('liked_features'),
                                    row.get('disliked_features'))
        if sentiment == 0:
            continue

        age = row.get('age_days') or 0.0
        try:
            age = float(age)
        except (TypeError, ValueError):
            age = 0.0
        w = _decay_weight(age, decay_days)

        tokens = _extract_item_tokens(row)
        # Dedupe within one item — many tokens repeat across fields
        seen: set[str] = set()
        for t in tokens:
            if t in seen:
                continue
            seen.add(t)
            if sentiment > 0:
                positive[t] += w
            else:
                negative[t] += w

        if sentiment > 0:
            n_pos += 1
        else:
            n_neg += 1

    return {
        'positive': dict(positive),
        'negative': dict(negative),
        'n_positive_items': n_pos,
        'n_negative_items': n_neg,
        'decay_days': decay_days,
    }


def _empty_profile(decay_days: float) -> dict:
    return {
        'positive': {}, 'negative': {},
        'n_positive_items': 0, 'n_negative_items': 0,
        'decay_days': decay_days,
    }


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in rows)
    except sqlite3.OperationalError:
        return False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def taste_score(candidate_text: Any, profile: Optional[dict]) -> float:
    """Score one candidate against the profile, in [-1, 1].

    A score of 0 means either no profile data or no overlap.
    """
    if not profile or (not profile.get('positive') and not profile.get('negative')):
        return 0.0

    tokens = set(_tokens(candidate_text))
    if not tokens:
        return 0.0

    pos = sum(profile['positive'].get(t, 0.0) for t in tokens)
    neg = sum(profile['negative'].get(t, 0.0) for t in tokens)

    # tanh squashes to [-1, 1]. The divisor controls saturation — picked
    # so a strongly matching candidate (sum ≈ 5) hits ~tanh(1) ≈ 0.76.
    return math.tanh((pos - neg) / 5.0)


# ---------------------------------------------------------------------------
# Final ranking
# ---------------------------------------------------------------------------
# Weights — must sum to ~1.0. visual_sim slot is 0.0 until Stage 5.
WEIGHT_VISUAL = 0.00     # Stage 5
WEIGHT_TASTE = 0.50
WEIGHT_TRUST = 0.20
WEIGHT_PRICE = 0.30


def _build_candidate_text(row: dict) -> str:
    """Concatenate the text fields used for taste matching on a result row."""
    fields = []
    for k in ('title', 'name', 'description'):
        v = row.get(k)
        if v:
            fields.append(str(v))
    if row.get('primary'):
        for k in ('title', 'name', 'description'):
            v = row['primary'].get(k)
            if v:
                fields.append(str(v))
    return ' '.join(fields)


def _price_advantage(prices: list[Optional[int]]) -> list[float]:
    """Return per-row price advantage in [0, 1]. Cheapest = 1.0,
    most expensive = 0.0. None prices → 0.5 (neutral)."""
    if not prices:
        return []
    real = [p for p in prices if p is not None and p > 0]
    if not real:
        return [0.5] * len(prices)
    lo, hi = min(real), max(real)
    if lo == hi:
        # All prices equal — no differential signal
        return [0.5 if p is None else 1.0 for p in prices]
    out = []
    for p in prices:
        if p is None or p <= 0:
            out.append(0.5)
        else:
            # Linear inversion: lo → 1.0, hi → 0.0
            out.append(1.0 - (p - lo) / (hi - lo))
    return out


def rank_candidates(
    canonical_rows: list[dict],
    profile: Optional[dict],
    *,
    attrs: Optional[dict] = None,
    weights: Optional[dict] = None,
) -> list[dict]:
    """Re-rank canonical groups by combined score.

    Each output row keeps the original fields plus:
        _taste, _trust, _price_advantage, _final_score, _score_breakdown

    Sorted by _final_score descending.
    """
    if not canonical_rows:
        return []

    w = {
        'visual': WEIGHT_VISUAL, 'taste': WEIGHT_TASTE,
        'trust': WEIGHT_TRUST, 'price': WEIGHT_PRICE,
    }
    if weights:
        w.update(weights)

    prices = [r.get('price_min') for r in canonical_rows]
    advantages = _price_advantage(prices)

    enriched = []
    for row, advantage in zip(canonical_rows, advantages):
        text = _build_candidate_text(row)
        ts = taste_score(text, profile)
        trust = get_trust(row.get('store'))
        visual = float(row.get('_visual_sim') or 0.0)

        final = (
            w['visual'] * visual
            + w['taste']  * ts
            + w['trust']  * trust
            + w['price']  * advantage
        )

        enriched.append({
            **row,
            '_taste': ts,
            '_trust': trust,
            '_price_advantage': advantage,
            '_visual_sim': visual,
            '_final_score': final,
            '_score_breakdown': {
                'visual': w['visual'] * visual,
                'taste':  w['taste']  * ts,
                'trust':  w['trust']  * trust,
                'price':  w['price']  * advantage,
            },
        })

    enriched.sort(key=lambda r: r['_final_score'], reverse=True)
    return enriched
