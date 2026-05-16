"""
ml_query_expansion.py — Query Expansion Tree for Memory Lane product search.

Stage 1/§2 of visual-product-search skill: given structured attributes
(from ml_attributes.extract_attributes), generate a fan-out of search
queries at decreasing specificity levels. Different marketplaces rank
exact vs broad differently — querying at multiple specificity levels
maximises recall while preserving precision via the visual gate later.

Specificity tags (T1=most precise → T5=broadest):
    article          — pure article/SKU
    brand_article    — brand + article ("Nike 315122-111")
    brand_model      — brand + model ("Nike Air Force 1")
    brand_subcat     — brand + subcategory + colour
    descriptive      — subcategory + colour + material + fit (no brand)
    style_broad      — subcategory + 1-2 style tags
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Order is meaningful: callers may stop at first non-empty hit, so most
# specific must come first.
SPECIFICITY_ORDER = (
    'article',
    'brand_article',
    'brand_model',
    'brand_subcat',
    'descriptive',
    'style_broad',
)


def _clean(s: str | None) -> str:
    """Collapse whitespace, strip None-like strings."""
    if not s:
        return ''
    s = str(s).strip()
    if s.lower() in ('null', 'none', '—', '-', ''):
        return ''
    return re.sub(r'\s+', ' ', s)


def _join(*parts: str | None) -> str:
    """Join non-empty parts with single space."""
    cleaned = [_clean(p) for p in parts]
    return ' '.join(p for p in cleaned if p)


def expand_queries(attrs: dict, *, include_purchase_intent: bool = False) -> List[Tuple[str, str]]:
    """Return [(query, specificity_tag), ...] from most precise to broadest.

    Args:
        attrs: validated attribute dict from ml_attributes.validate_attributes
        include_purchase_intent: append "купить" to each query (helps on
            marketplaces but hurts on brand sites). Default False.

    Returns:
        Ordered list of distinct (query, tag) pairs. Empty if attrs has
        nothing usable.
    """
    if not isinstance(attrs, dict):
        return []

    article = _clean(attrs.get('article'))
    brand = _clean(attrs.get('brand'))
    model = _clean(attrs.get('model'))
    subcat = _clean(attrs.get('subcategory'))
    cat = _clean(attrs.get('category'))
    color = _clean(attrs.get('primary_color'))
    material = _clean(attrs.get('material'))
    fit = _clean(attrs.get('fit'))
    length = _clean(attrs.get('length'))
    styles = [s for s in (attrs.get('style') or []) if _clean(s)]

    # Subcategory falls back to category for generic queries
    base_noun = subcat or cat

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def push(query: str, tag: str) -> None:
        q = _clean(query)
        if not q:
            return
        key = q.lower()
        if key in seen:
            return
        seen.add(key)
        out.append((q, tag))

    # T1: pure article (most precise)
    if article:
        push(article, 'article')

    # T2: brand + article
    if article and brand:
        push(_join(brand, article), 'brand_article')

    # T3: brand + model
    if brand and model:
        push(_join(brand, model), 'brand_model')

    # T4: brand + subcategory + colour
    if brand and base_noun:
        push(_join(brand, base_noun, color), 'brand_subcat')

    # T5: descriptive — subcategory + colour + material + fit/length
    if base_noun:
        # Pick fit OR length to avoid noise (one usually empty anyway)
        modifier = fit or length
        push(_join(base_noun, color, material, modifier), 'descriptive')

    # T6: style-broad — subcategory + up to 2 styles
    if base_noun and styles:
        push(_join(base_noun, *styles[:2]), 'style_broad')

    # T7: category_noun — when subcategory is long/compound, also try
    # just category + key words extracted from subcategory
    if subcat and cat and subcat != cat:
        # If subcat is multi-word, try cat + first word of subcat
        words = subcat.split()
        if len(words) >= 2:
            push(_join(cat, words[0], color), 'category_noun')
        # Also try subcat alone without modifiers (shorter = broader)
        if len(words) >= 3:
            push(_join(words[0], color), 'noun_color')

    if include_purchase_intent:
        out = [(_join(q, 'купить'), tag) for q, tag in out]

    return out


def top_query(attrs: dict) -> str | None:
    """Convenience: return the most specific query string, or None."""
    expanded = expand_queries(attrs)
    return expanded[0][0] if expanded else None


def queries_for_source(attrs: dict, source: str, max_n: int = 3) -> List[str]:
    """Choose appropriate query slice for a given source type.

    Heuristics:
        - brand sites: use article/brand_model only (precise)
        - marketplaces (ozon/wb/ym): use top-N including descriptive
        - reverse image search: not relevant here (uses image, not text)
    """
    expanded = expand_queries(attrs)
    if not expanded:
        return []

    if source.startswith('brand:'):
        # Brand-site search: only precise tags
        precise = {'article', 'brand_article', 'brand_model'}
        return [q for q, tag in expanded if tag in precise][:max_n]

    # Default: marketplaces — full ladder, capped
    return [q for q, _ in expanded][:max_n]
