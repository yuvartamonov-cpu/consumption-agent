"""
ml_search_v2.py — Orchestrator that wires Stages 1-4 together.

Pipeline:
    1. Load memory_lane_items row + photo path
    2. Get attributes (cached attributes_json OR fresh Vision extraction)
    3. Build query expansion tree
    4. Run federated search via injectable candidates_provider
    5. Canonicalize results across marketplaces
    6. Flag price anomalies
    7. Detect inventory collisions
    8. Build user taste profile + re-rank by combined score
    9. Format for Telegram

Both the Vision call and the marketplace search are injectable so this
module is fully testable without network or OpenAI API access.

Public API:
    search_ml_item_v2(conn, item_id, ...) -> dict
    format_search_result_telegram(result) -> str
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
import sqlite3
from typing import Any, Awaitable, Callable, Optional

import ml_anomaly
import ml_attributes
import ml_bandit
import ml_canonical
import ml_clicks
import ml_inventory
import ml_query_expansion
import ml_taste

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source routing (text-only mirror of SKILL.md §3 brand authority cascade)
# ---------------------------------------------------------------------------
# Источники по категориям. Иностранные маркетплейсы помечены тегом _foreign_
# и фильтруются по геолокации клиента в route_sources().
CATEGORY_SOURCES: dict[str, list[str]] = {
    'одежда':    ['lamoda', 'brandshop', 'wildberries', 'yandex_market', 'aliexpress', 'alibaba'],
    'обувь':     ['lamoda', 'brandshop', 'sneakerhead', 'wildberries', 'yandex_market', 'aliexpress'],
    'техника':   ['dns', 'citilink', 'mvideo', 'yandex_market', 'wildberries', 'aliexpress', 'alibaba'],
    'мебель':    ['hoff', 'mrdoors', 'ikea', 'yandex_market', 'wildberries', 'alibaba'],
    'интерьер':  ['hoff', 'ikea', 'yandex_market', 'wildberries', 'aliexpress', 'alibaba'],
    'косметика': ['goldapple', 'iledebeaute', 'wildberries', 'yandex_market', 'aliexpress'],
    'аксессуары':['lamoda', 'brandshop', 'wildberries', 'yandex_market', 'aliexpress', 'alibaba'],
}
DEFAULT_SOURCES = ['wildberries', 'yandex_market', 'lamoda', 'aliexpress', 'alibaba']


def _filter_sources_by_geo(sources: list[str]) -> list[str]:
    """Убирает иностранные источники, недоступные в текущем регионе."""
    try:
        import ml_providers
        allowed_foreign = set(ml_providers.foreign_sources_for_geo())
        return [
            s for s in sources
            if not ml_providers.is_foreign_source(s) or s in allowed_foreign
        ]
    except ImportError:
        return sources

# How many query variants we ask each source to try (most-specific first)
QUERIES_PER_SOURCE = 3

_NONWORD_RX = re.compile(r'[^\w]+', flags=re.UNICODE)


def route_sources(
    attrs: dict,
    *,
    top_n: int = 7,
    conn: Optional[sqlite3.Connection] = None,
) -> list[str]:
    """Pick marketplaces appropriate for the item's category, plus the
    brand site if a brand was recognised.

    When `conn` is provided, the bandit (Stage 6) reorders the candidate
    list by Thompson sampling — sources that historically led to user
    clicks for this category float to the top. Brand site stays pinned
    at position 0.
    """
    cat = (attrs.get('category') or '').lower()
    base = list(CATEGORY_SOURCES.get(cat, DEFAULT_SOURCES))
    # Фильтруем иностранные маркетплейсы по геолокации клиента
    base = _filter_sources_by_geo(base)
    if conn is not None:
        try:
            base = ml_bandit.sample_sources(conn, cat, base, k=len(base))
        except Exception as e:
            log.warning("ml_search_v2: bandit sampling failed, "
                        "falling back to static order: %s", e)
    out = base[:top_n]
    if attrs.get('brand'):
        out.insert(0, f"brand:{attrs['brand']}")
    return out


# ---------------------------------------------------------------------------
# Provider types (typed for clarity, not enforced)
# ---------------------------------------------------------------------------
AttributeExtractor = Callable[[Optional[str], str], Awaitable[dict]]
CandidatesProvider = Callable[[list[str], list[str], Optional[str]], Awaitable[list[dict]]]


# ---------------------------------------------------------------------------
# Default providers
# ---------------------------------------------------------------------------
async def _default_attribute_extractor(photo_path: Optional[str], caption: str) -> dict:
    """Production default — calls ml_attributes.extract_attributes_async."""
    if not photo_path:
        # No photo, nothing to call Vision on. Return defaults.
        return ml_attributes.validate_attributes({})
    return await ml_attributes.extract_attributes_async(photo_path, caption)


async def _default_candidates_provider(
    queries: list[str],
    sources: list[str],
    photo_path: Optional[str],
) -> list[dict]:
    """Production default — delegates to ml_providers.composite_provider.

    Fetches real candidates from Wildberries (public API), Ozon (if
    cookies available), and Yandex Market (link-only fallback).
    """
    try:
        import ml_providers
        return await ml_providers.composite_provider(queries, sources, photo_path)
    except ImportError:
        log.warning("ml_search_v2: ml_providers not available, returning empty")
        return []
    except Exception as e:
        log.warning("ml_search_v2: composite_provider failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Item loading
# ---------------------------------------------------------------------------
def _load_item(conn: sqlite3.Connection, item_id: int) -> Optional[dict]:
    """Fetch memory_lane_items row joined with media file path."""
    has_attrs = _has_column(conn, 'memory_lane_items', 'attributes_json')
    has_vision = _has_column(conn, 'memory_lane_items', 'name')

    cols = ['m.id', 'm.caption', 'm.topic', 'm.style_tags', 'm.media_asset_id']
    if has_vision:
        cols += ['m.name', 'm.description', 'm.brand']
    else:
        cols += ['NULL AS name', 'NULL AS description', 'NULL AS brand']
    if has_attrs:
        cols.append('m.attributes_json')
    else:
        cols.append('NULL AS attributes_json')
    cols.append('a.file_path')

    sql = (
        f"SELECT {', '.join(cols)} "
        f"FROM memory_lane_items m "
        f"LEFT JOIN media_assets a ON a.id = m.media_asset_id "
        f"WHERE m.id = ?"
    )
    try:
        cur = conn.execute(sql, (item_id,))
        row = cur.fetchone()
    except sqlite3.OperationalError as e:
        log.warning("ml_search_v2: cannot load item %d: %s", item_id, e)
        return None
    if not row:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in rows)
    except sqlite3.OperationalError:
        return False


def _normalize_for_match(value: Any) -> str:
    """Lowercase and collapse punctuation for conservative exact matching."""
    text = ml_canonical.normalize(value)
    if not text:
        return ''
    return _NONWORD_RX.sub(' ', text).strip()


def _contains_normalized_phrase(haystack: Any, needle: Any) -> bool:
    """True when normalized phrase appears as a whole phrase."""
    n = _normalize_for_match(needle)
    if not n:
        return False
    h = _normalize_for_match(haystack)
    if not h:
        return False
    return f' {n} ' in f' {h} '


def _select_provider_queries(
    expanded: list[tuple[str, str]],
    attrs: dict,
    *,
    max_n: int = QUERIES_PER_SOURCE,
) -> list[str]:
    """Keep brand searches brand-constrained to avoid noisy fallbacks."""
    if not expanded:
        return []

    brand = attrs.get('brand')
    if brand:
        branded = [q for q, _ in expanded if _contains_normalized_phrase(q, brand)]
        if branded:
            return branded[:max_n]
    return [q for q, _ in expanded][:max_n]


_TIER_PRIORITY = {
    'official': 0,
    'distributor': 1,
    'authorized': 2,
    'brand_page': 3,
    'search_fallback': 9,
}


def _sort_by_tier(candidates: list[dict]) -> list[dict]:
    """Ставим official/distributor/authorized ссылки выше generic результатов."""
    def _key(c: dict) -> int:
        return _TIER_PRIORITY.get(c.get('tier', ''), 5)
    return sorted(candidates, key=_key)


def _candidate_matches_brand(candidate: dict, brand: str) -> bool:
    """Conservative exact brand match; no fuzzy hamington/remington collisions."""
    for field in (
        candidate.get('brand'),
        candidate.get('title'),
        candidate.get('name'),
        candidate.get('url'),
    ):
        if _contains_normalized_phrase(field, brand):
            return True
    return False


def _candidate_has_explicit_brand(candidate: dict) -> bool:
    """True when provider returned a concrete brand value for the listing."""
    return bool(_normalize_for_match(candidate.get('brand')))


def _filter_candidates_for_exact_brand(
    candidates: list[dict],
    attrs: dict,
) -> tuple[list[dict], Optional[str]]:
    """Drop candidates that do not mention the recognised brand."""
    brand = (attrs.get('brand') or '').strip()
    if not brand:
        return candidates, None

    matched: list[dict] = []
    neutral: list[dict] = []
    conflicting: list[dict] = []
    for cand in candidates:
        if _candidate_matches_brand(cand, brand):
            matched.append(cand)
        elif _candidate_has_explicit_brand(cand):
            conflicting.append(cand)
        else:
            neutral.append(cand)

    if matched:
        return matched + neutral, None
    if neutral:
        return neutral, None
    if conflicting:
        return [], f"точных совпадений по бренду {brand} не найдено"
    return [], None


# ---------------------------------------------------------------------------
# Attribute resolution (cached vs fresh)
# ---------------------------------------------------------------------------
async def _resolve_attributes(
    conn: sqlite3.Connection,
    item: dict,
    extractor: AttributeExtractor,
    *,
    force_refresh: bool = False,
) -> dict:
    """Return validated attribute dict for the item.

    If attributes_json was cached (Stage 1 migration done) and not stale,
    use it. Otherwise call the extractor, validate, persist.
    """
    cached_raw = item.get('attributes_json')
    if cached_raw and not force_refresh:
        try:
            import json
            cached = json.loads(cached_raw)
            return ml_attributes.validate_attributes(cached)
        except (TypeError, ValueError):
            log.warning("ml_search_v2: corrupt attributes_json for item %s", item.get('id'))

    photo_path = item.get('file_path')
    caption = item.get('caption') or ''
    attrs = await extractor(photo_path, caption)

    # Persist for next time (best-effort)
    try:
        ml_attributes.save_attributes(conn, item['id'], attrs)
    except sqlite3.OperationalError as e:
        log.warning("ml_search_v2: could not cache attributes: %s", e)
    return attrs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def search_ml_item_v2(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    candidates_provider: Optional[CandidatesProvider] = None,
    attribute_extractor: Optional[AttributeExtractor] = None,
    force_refresh_attrs: bool = False,
    profile_id: str = 'default',
    decay_days: float = 180.0,
) -> dict:
    """Run the full visual-product-search pipeline.

    Returns a dict; never raises on missing data — falls back to defaults
    and reports them in `result['errors']`.
    """
    extractor = attribute_extractor or _default_attribute_extractor
    fetcher = candidates_provider or _default_candidates_provider

    result: dict[str, Any] = {
        'item_id': item_id,
        'attributes': {},
        'queries': [],
        'sources_used': [],
        'canonical_groups': [],
        'inventory_collisions': [],
        'collision_warning': None,
        'summary': {},
        'errors': [],
    }

    # 1. Load item
    item = _load_item(conn, item_id)
    if not item:
        result['errors'].append(f"item {item_id} not found")
        return result

    # 2. Attributes
    try:
        attrs = await _resolve_attributes(conn, item, extractor,
                                          force_refresh=force_refresh_attrs)
    except Exception as e:
        log.exception("ml_search_v2: attribute extraction failed")
        result['errors'].append(f"attributes: {e}")
        attrs = ml_attributes.validate_attributes({})
    # Merge any item-level brand/name/topic into attrs as a soft prior
    # (Vision result wins, but if it's empty we use what we have)
    if not attrs.get('brand') and item.get('brand'):
        attrs['brand'] = item['brand']
    if not attrs.get('category') and item.get('topic'):
        attrs['category'] = item['topic']
    if not attrs.get('subcategory') and item.get('name'):
        attrs['subcategory'] = item['name']
    result['attributes'] = attrs

    # 3. Query expansion + 4. Source routing (bandit-aware)
    expanded = ml_query_expansion.expand_queries(attrs)
    # If item has a name that differs from expanded queries, prepend it
    # as a direct "item_name" tier — the user-visible name is often the
    # most effective search query.
    item_name = (item.get('name') or '').strip()
    if item_name:
        existing_texts = {q for q, _ in expanded}
        if item_name not in existing_texts:
            expanded.insert(0, (item_name, 'item_name'))
    result['queries'] = expanded
    sources = route_sources(attrs, conn=conn)
    result['sources_used'] = sources

    if not expanded:
        result['errors'].append('no queries could be expanded from attributes')
        return result

    # 5. Federated search
    queries = _select_provider_queries(expanded, attrs)
    try:
        candidates = await fetcher(queries, sources, item.get('file_path'))
    except Exception as e:
        log.exception("ml_search_v2: candidates_provider failed")
        result['errors'].append(f"candidates: {e}")
        candidates = []

    candidates, brand_filter_error = _filter_candidates_for_exact_brand(candidates, attrs)
    if brand_filter_error:
        result['errors'].append(brand_filter_error)

    # 5b. Sort official/distributor links to top by tier priority
    try:
        import ml_official_sites
        candidates = _sort_by_tier(candidates)
    except ImportError:
        pass

    # 6. Canonicalize
    canonical = ml_canonical.canonicalize(candidates, attrs)
    result['summary'] = ml_canonical.group_stats(canonical)

    # 7. Anomalies (with brand-history from items table)
    def _brand_avg(brand):
        return ml_anomaly.avg_paid_for_brand(conn, brand) if brand else None
    flagged = ml_anomaly.detect_all(
        canonical, attrs, brand_history_provider=_brand_avg
    )

    # 8. Taste profile + re-rank
    profile = ml_taste.build_taste_profile(
        conn, decay_days=decay_days, profile_id=profile_id
    )
    ranked = ml_taste.rank_candidates(flagged, profile, attrs=attrs)
    result['canonical_groups'] = ranked

    # 9. Inventory collision check
    collisions = ml_inventory.find_inventory_collisions(conn, attrs)
    result['inventory_collisions'] = collisions
    result['collision_warning'] = ml_inventory.format_collision_warning(collisions)

    # 10. Log impressions for active learning (Stage 9)
    try:
        ml_clicks.log_impressions(
            conn, item_id, ranked, category=attrs.get('category')
        )
    except Exception as e:
        log.warning("ml_search_v2: log_impressions failed: %s", e)

    return result


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------
def _esc(s: Any) -> str:
    if s is None:
        return ''
    return _html.escape(str(s))


def _fmt_price_range(g: dict) -> str:
    pmin = g.get('price_min')
    pmax = g.get('price_max')
    if pmin is None:
        return 'цена по ссылке'
    if pmax is None or pmax == pmin:
        return f"{pmin:,} ₽".replace(',', ' ')
    return f"{pmin:,}–{pmax:,} ₽".replace(',', ' ')


def _fmt_anomaly(a: Optional[dict]) -> str:
    if not a:
        return ''
    icon = '⚠️' if a['kind'] == 'suspicious_cheap' else '💸'
    return f"{icon} {_esc(a['reason'])}"


# Лимит Telegram для одного сообщения
TG_MESSAGE_LIMIT = 4096
# Сколько групп на одну страницу (если результат не помещается)
GROUPS_PER_PAGE = 5


def _format_header(result: dict) -> str:
    """Формирует заголовок результата поиска (общий для всех страниц)."""
    attrs = result.get('attributes', {})
    groups = result.get('canonical_groups', [])

    lines = []
    name = attrs.get('subcategory') or attrs.get('category') or 'товар'
    brand = attrs.get('brand')
    title = f"🔍 <b>{_esc(name)}</b>"
    if brand:
        title += f" · {_esc(brand)}"
    lines.append(title)

    bits = []
    if attrs.get('primary_color'):
        bits.append(_esc(attrs['primary_color']))
    if attrs.get('material'):
        bits.append(_esc(attrs['material']))
    if attrs.get('style'):
        bits.append(', '.join(_esc(s) for s in attrs['style'][:2]))
    if bits:
        lines.append('· '.join(bits))

    if result.get('collision_warning'):
        lines.append('')
        lines.append(_esc(result['collision_warning']))

    if not groups:
        lines.append('')
        if result.get('errors'):
            lines.append(f"⚠️ {_esc('; '.join(result['errors']))}")
        else:
            lines.append('Ничего не нашёл по этим параметрам.')
        return '\n'.join(lines)

    summary = result.get('summary') or {}
    if summary.get('groups'):
        lines.append('')
        lines.append(
            f"<b>Найдено</b>: {summary['groups']} товаров "
            f"в {summary['total_listings']} листингах"
        )
    return '\n'.join(lines)


def _format_group(i: int, g: dict) -> str:
    """Форматирует один элемент результата (один товар/ссылка)."""
    store = _esc(g.get('store', '?'))
    url = g.get('url')
    title_txt = _esc((g.get('title') or g.get('name') or '—')[:80])
    price_str = _fmt_price_range(g)
    n_sources = g.get('sources_count', 1)
    n_sources_label = f" · {n_sources} площадки" if n_sources > 1 else ''

    line = (f"<b>{i}.</b> {title_txt}\n"
            f"   🛒 <b>{store}</b>{n_sources_label} · {price_str}")
    anomaly_line = _fmt_anomaly(g.get('anomaly'))
    if anomaly_line:
        line += f"\n   {anomaly_line}"
    if url:
        line += f"\n   <a href=\"{_esc(url)}\">🔗 открыть</a>"
    return line


def format_search_result_telegram(result: dict, *, max_groups: int = 5) -> str:
    """Render a Telegram HTML reply for the /ml_search command.

    Для обратной совместимости — возвращает одну строку (первую страницу).
    """
    pages = format_search_pages(result, groups_per_page=max_groups)
    return pages[0] if pages else ''


def format_search_pages(
    result: dict,
    *,
    groups_per_page: int = GROUPS_PER_PAGE,
    char_limit: int = TG_MESSAGE_LIMIT,
) -> list[str]:
    """Разбивает результат поиска на страницы, каждая ≤ char_limit.

    Возвращает list[str] — одна строка на страницу.
    Первая страница содержит заголовок + первые N групп.
    Последующие — только группы с нумерацией.
    """
    groups = result.get('canonical_groups', [])
    header = _format_header(result)

    if not groups:
        return [header]

    pages: list[str] = []
    page_lines: list[str] = [header, '']
    page_len = len(header) + 1
    groups_on_page = 0
    total_remaining = len(groups)

    for i, g in enumerate(groups, 1):
        group_text = _format_group(i, g)
        total_remaining -= 1

        # Проверяем, влезет ли этот элемент на текущую страницу
        new_len = page_len + len(group_text) + 2  # +2 для \n
        page_full = (groups_on_page >= groups_per_page or
                     new_len > char_limit - 100)  # запас 100 для footer

        if page_full and groups_on_page > 0:
            # Завершаем текущую страницу
            if total_remaining + 1 > 0:
                page_lines.append('')
                page_lines.append(f'… ещё {total_remaining + 1} вариантов')
            pages.append('\n'.join(page_lines))
            # Начинаем новую страницу
            page_lines = [f'🔍 <b>Продолжение</b> (с {i}):']
            page_len = len(page_lines[0]) + 1
            groups_on_page = 0

        page_lines.append(group_text)
        page_len += len(group_text) + 1
        groups_on_page += 1

    if page_lines:
        pages.append('\n'.join(page_lines))

    return pages


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main_cli():
    import argparse
    import os
    import sys
    parser = argparse.ArgumentParser(description='visual-product-search v2 pipeline')
    parser.add_argument('item_id', type=int, help='memory_lane_items.id')
    parser.add_argument('--db', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'consumption.db'))
    parser.add_argument('--force-refresh-attrs', action='store_true')
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    result = asyncio.run(search_ml_item_v2(
        conn, args.item_id, force_refresh_attrs=args.force_refresh_attrs
    ))
    conn.close()
    print(format_search_result_telegram(result))


if __name__ == '__main__':
    _main_cli()
