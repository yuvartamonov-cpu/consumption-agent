"""
ml_canonical.py — Cross-Marketplace Product Canonicalization.

Stage 2/§5 of visual-product-search skill. Given a heterogeneous list
of marketplace listings for one Memory Lane item, group identical
products across Ozon / WB / Yandex.Market into canonical groups so the
user sees one row per actual product (with price spread), not three
duplicate rows.

Strategy ladder (first hit wins):

    Tier A — attribute-strong:  brand + (article | model)
    Tier B — attribute-medium:  subcategory + primary_color
    Tier C — text fallback:     top-N normalized name tokens (sorted)

Tier A produces stable hashes that match the same product across
marketplaces. Tier B is for generic items without a brand. Tier C
is the catch-all that prevents the whole list collapsing into one
group when nothing is known.

Note: SKILL.md §5 mentions a CLIP-embedding fallback in the fingerprint.
This module deliberately ships *without* CLIP — that arrives in Stage 5.
Until then we use Tier C text-token signature.
"""
from __future__ import annotations

import hashlib
import re
import statistics
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
# Strip everything that isn't a word-char or whitespace. Hyphens map to space
# so "315122-111" → "315122 111" (better matching across stores that drop the
# hyphen). NB: we don't NFKD-decompose — Cyrillic letters like 'й'/'ё' get
# split into base+combining-mark by NFKD which corrupts the text.
_PUNCT_RX = re.compile(r"[^\w\s]+", flags=re.UNICODE)
_WS_RX = re.compile(r"\s+")

# Tokens that contribute nothing to product identity. Lowercase, no punct.
_STOP_TOKENS = frozenset({
    'купить', 'оригинал', 'оригинальный', 'новый', 'новая', 'новое',
    'распродажа', 'sale', 'скидка', 'акция', 'хит', 'тренд',
    'для', 'с', 'и', 'или', 'без', 'на', 'из', 'в',
    'цвет', 'размер', 'модель',
    'official', 'original', 'new', 'best',
})


def normalize(s: Any) -> str:
    """Lowercase, NFKD, strip punctuation, collapse whitespace.

    None / empty / 'null' / 'none' / '—' → ''.
    """
    if s is None:
        return ''
    s = str(s).strip()
    if not s or s.lower() in ('null', 'none', '—', '-'):
        return ''
    s = s.lower()
    s = _PUNCT_RX.sub(' ', s)
    s = _WS_RX.sub(' ', s).strip()
    return s


def _tokens(s: str, keep_stops: bool = False) -> list[str]:
    """Tokenize normalized string. Optionally keep stop-tokens."""
    norm = normalize(s)
    if not norm:
        return []
    toks = norm.split(' ')
    if not keep_stops:
        toks = [t for t in toks if t and t not in _STOP_TOKENS]
    return [t for t in toks if t]


# ---------------------------------------------------------------------------
# Price parsing
# ---------------------------------------------------------------------------
_PRICE_RX = re.compile(r'(\d[\d\s,. ]*\d|\d)')


def parse_price(value: Any) -> Optional[int]:
    """Best-effort price normalization to RUB integer.

    Accepts ints, floats, strings ("12 990 ₽", "12,990.00", "12990"),
    or dicts like {'value': 12990, 'currency': 'RUB'} (Yandex.Market).
    Returns None when nothing usable.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    if isinstance(value, dict):
        # Common shapes: {'value': N} or {'price': N}
        for k in ('value', 'price', 'amount', 'min'):
            if k in value:
                got = parse_price(value[k])
                if got is not None:
                    return got
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Match the first run of digits (with thousands separators)
    m = _PRICE_RX.search(s)
    if not m:
        return None
    raw = m.group(1)
    # Remove all separators: spaces, NBSP, commas. Keep only digits.
    # Treat trailing ",NN" or ".NN" as kopeks — drop them.
    raw = raw.replace(' ', ' ').strip()
    # Split on last decimal separator if present
    parts = re.split(r'[.,](\d{1,2})$', raw)
    if len(parts) >= 2 and parts[1]:
        whole = re.sub(r'\D', '', parts[0])
    else:
        whole = re.sub(r'\D', '', raw)
    if not whole:
        return None
    try:
        n = int(whole)
    except ValueError:
        return None
    return n if n > 0 else None


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def _hash12(payload: str) -> str:
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]


def canonical_fingerprint(cand: dict, attrs: dict | None = None,
                          *, token_topn: int = 4) -> str:
    """Return a stable fingerprint string for one candidate listing.

    Tiers tried in order:
        A: attr:brand|model|article       (deterministic across stores)
        B: attr-loose:subcat|color        (generic items)
        C: tokens:t1|t2|t3|t4             (text fallback)
        D: unknown:<sha12(repr)>          (last resort)
    """
    attrs = attrs or {}

    if cand.get('_link_only'):
        src = normalize(cand.get('source') or cand.get('store') or 'link')
        url = normalize(cand.get('url') or cand.get('title') or '')
        return f"link:{src}|{_hash12(url or repr(sorted(cand.items())))}"

    brand = normalize(attrs.get('brand') or cand.get('brand'))
    model = normalize(cand.get('model') or attrs.get('model'))
    article = normalize(cand.get('article') or attrs.get('article'))

    # Tier A — brand + (article OR model). Article wins if both present.
    if brand and (article or model):
        key = article or model
        return f"attr:{brand}|{key}"

    # Article alone is enough — many marketplaces show same article
    if article:
        return f"attr:|{article}"

    # Tier B — subcategory + primary colour (generic but stable)
    subcat = normalize(attrs.get('subcategory') or attrs.get('category'))
    color = normalize(attrs.get('primary_color'))
    if subcat and color:
        return f"attr-loose:{subcat}|{color}"

    # Tier C — top-N tokens from candidate's text
    text = (cand.get('title') or cand.get('name') or '')
    toks = _tokens(text)
    if toks:
        # Sort to make the fingerprint order-invariant
        sig = sorted(toks)[:token_topn]
        if sig:
            return f"tokens:{'|'.join(sig)}"

    # Tier D — opaque hash of repr (singleton groups)
    return f"unknown:{_hash12(repr(sorted(cand.items())))}"


# ---------------------------------------------------------------------------
# Group aggregation
# ---------------------------------------------------------------------------
def _safe_median(values: Iterable[int]) -> Optional[int]:
    vs = [v for v in values if v is not None]
    if not vs:
        return None
    return int(statistics.median(vs))


def canonicalize(candidates: list[dict], attrs: dict | None = None) -> list[dict]:
    """Collapse duplicate listings into canonical groups.

    Each output row has:
        fingerprint        — group key
        price_min/max/median (int RUB, or None when no prices)
        sources            — unique store names in the group
        sources_count      — len(group)
        all_listings       — full original candidate dicts
        primary            — cheapest listing (with price) or first one
        + top-level convenience fields from the primary
          (title/url/store/price/image_url if present)

    Groups are returned sorted by group size desc, then by price_min asc.
    """
    if not candidates:
        return []

    groups: dict[str, list[dict]] = {}
    for c in candidates:
        if not isinstance(c, dict):
            continue
        fp = canonical_fingerprint(c, attrs)
        groups.setdefault(fp, []).append(c)

    out: list[dict] = []
    for fp, items in groups.items():
        # Pre-parse prices once
        priced = [(parse_price(it.get('price')), it) for it in items]
        with_price = [(p, it) for p, it in priced if p is not None]

        if with_price:
            with_price.sort(key=lambda x: x[0])
            primary_price, primary = with_price[0]
            prices = [p for p, _ in with_price]
        else:
            primary = items[0]
            prices = []

        sources = []
        seen_src = set()
        for it in items:
            src = it.get('store')
            if src and src not in seen_src:
                seen_src.add(src)
                sources.append(src)

        group_row: dict[str, Any] = {
            **{k: primary.get(k) for k in (
                'title', 'name', 'url', 'image_url',
                'query_ru', 'query_en', 'query_local', 'query_lang', '_link_only',
            )},
            'store': primary.get('store'),
            'price': primary.get('price'),
            'fingerprint': fp,
            'price_min': prices[0] if prices else None,
            'price_max': prices[-1] if prices else None,
            'price_median': _safe_median(prices),
            'sources': sources,
            'sources_count': len(items),
            'all_listings': items,
            'primary': primary,
        }
        out.append(group_row)

    out.sort(key=lambda g: (
        -g['sources_count'],
        g['price_min'] if g['price_min'] is not None else 10**12,
    ))
    return out


def group_stats(canonical_rows: list[dict]) -> dict:
    """Summary stats for a canonicalized result set."""
    if not canonical_rows:
        return {'groups': 0, 'total_listings': 0, 'priced_groups': 0,
                'price_min': None, 'price_max': None, 'price_median': None}
    medians = [r['price_median'] for r in canonical_rows if r['price_median']]
    mins = [r['price_min'] for r in canonical_rows if r['price_min']]
    maxs = [r['price_max'] for r in canonical_rows if r['price_max']]
    return {
        'groups': len(canonical_rows),
        'total_listings': sum(r['sources_count'] for r in canonical_rows),
        'priced_groups': len(medians),
        'price_min': min(mins) if mins else None,
        'price_max': max(maxs) if maxs else None,
        'price_median': int(statistics.median(medians)) if medians else None,
    }
