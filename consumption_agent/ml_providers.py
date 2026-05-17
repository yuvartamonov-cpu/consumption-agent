"""
ml_providers.py — Marketplace API providers for the v2 search pipeline.

Provides async functions that fetch real product candidates from:
  - Wildberries (public v5 search API, no auth)
  - Ozon (requires cookies from browser export)
  - Yandex Market (link-only fallback — API blocked for non-browsers)

Each provider returns list[dict] in canonical format:
    {title, url, price, store, image_url, brand, source}

The composite provider runs all available sources in parallel and
merges results.  Plug into ml_search_v2 as:
    search_ml_item_v2(conn, item_id,
                      candidates_provider=composite_provider)

Public API:
    search_wildberries(queries, limit=10) -> list[dict]
    search_ozon(queries, cookies_path=..., limit=10) -> list[dict]
    yandex_market_links(queries) -> list[dict]
    composite_provider(queries, sources, photo_path) -> list[dict]
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import json
import logging
import os
import re
import urllib.parse
from typing import Optional, Sequence

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_TIMEOUT = 12.0   # seconds per request
_WB_DEST = "-1257786"  # Moscow region (default dest for WB)

OZON_COOKIES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "www.ozon.ru_cookies.txt",
)

RETAILER_SEARCH_URLS = {
    'lamoda': 'https://www.lamoda.ru/catalogsearch/result/?q={query}',
    'brandshop': 'https://brandshop.ru/search/?q={query}',
    'sneakerhead': 'https://sneakerhead.ru/search/?q={query}',
    'dns': 'https://www.dns-shop.ru/search/?q={query}',
    'citilink': 'https://www.citilink.ru/search/?text={query}',
    'mvideo': 'https://www.mvideo.ru/search?text={query}',
    'hoff': 'https://hoff.ru/search/?q={query}',
    'mrdoors': 'https://www.mrdoors.ru/search/?q={query}',
    'ikea': 'https://www.ikea.com/ru/ru/search/?q={query}',
    'goldapple': 'https://goldapple.ru/catalogsearch/result/?q={query}',
    'iledebeaute': 'https://iledebeaute.ru/search/?q={query}',
    'aliexpress': 'https://www.aliexpress.com/wholesale?SearchText={query}',
    'alibaba': 'https://www.alibaba.com/trade/search?SearchText={query}',
}

RETAILER_TITLES = {
    'lamoda': 'Lamoda',
    'brandshop': 'Brandshop',
    'sneakerhead': 'Sneakerhead',
    'dns': 'DNS',
    'citilink': 'Citilink',
    'mvideo': 'М.Видео',
    'hoff': 'Hoff',
    'mrdoors': 'Mr.Doors',
    'ikea': 'IKEA',
    'goldapple': 'Золотое Яблоко',
    'iledebeaute': 'Иль де Ботэ',
    'aliexpress': 'AliExpress',
    'alibaba': 'Alibaba',
}

WEB_SEARCH_ENGINES = [
    ('Google', 'https://www.google.com/search?q={query}'),
    ('Yandex', 'https://yandex.ru/search/?text={query}'),
]

FOREIGN_RETAILERS = frozenset({'aliexpress', 'alibaba'})

QUERY_TRANSLATIONS = {
    'джемпер': 'sweater',
    'свитер': 'sweater',
    'пуловер': 'pullover',
    'кардиган': 'cardigan',
    'худи': 'hoodie',
    'толстовка': 'sweatshirt',
    'футболка': 't-shirt',
    'майка': 'tank top',
    'рубашка': 'shirt',
    'поло': 'polo',
    'пальто': 'coat',
    'куртка': 'jacket',
    'ветровка': 'windbreaker',
    'парка': 'parka',
    'пиджак': 'blazer',
    'костюм': 'suit',
    'брюки': 'pants',
    'джинсы': 'jeans',
    'юбка': 'skirt',
    'платье': 'dress',
    'кроссовки': 'sneakers',
    'кеды': 'trainers',
    'ботинки': 'boots',
    'туфли': 'shoes',
    'сумка': 'bag',
    'рюкзак': 'backpack',
    'часы': 'watch',
    'очки': 'glasses',
    'наушники': 'headphones',
    'ноутбук': 'laptop',
    'смартфон': 'smartphone',
    'телефон': 'phone',
    'планшет': 'tablet',
    'диван': 'sofa',
    'кресло': 'armchair',
    'стол': 'table',
    'стул': 'chair',
    'шкаф': 'wardrobe',
    'серый': 'gray',
    'серая': 'gray',
    'серое': 'gray',
    'серые': 'gray',
    'черный': 'black',
    'черная': 'black',
    'черное': 'black',
    'белый': 'white',
    'белая': 'white',
    'белое': 'white',
    'синий': 'blue',
    'синяя': 'blue',
    'синее': 'blue',
    'красный': 'red',
    'красная': 'red',
    'красное': 'red',
    'зеленый': 'green',
    'зеленая': 'green',
    'зеленое': 'green',
    'желтый': 'yellow',
    'желтая': 'yellow',
    'желтое': 'yellow',
    'коричневый': 'brown',
    'коричневая': 'brown',
    'бежевый': 'beige',
    'бежевая': 'beige',
    'кожаный': 'leather',
    'кожаная': 'leather',
    'шерстяной': 'wool',
    'шерстяная': 'wool',
    'хлопковый': 'cotton',
    'хлопковая': 'cotton',
    'льняной': 'linen',
    'льняная': 'linen',
    'casual': 'casual',
    'sport': 'sport',
    'sporty': 'sport',
}

_QUERY_WORD_RX = re.compile(r"[\wА-Яа-яЁё]+", re.UNICODE)


def translate_query_for_source(query: str, source: str) -> str:
    """Translate Russian product terms to English for foreign marketplaces."""
    if not query:
        return query
    src = (source or "").strip().lower()
    if src not in FOREIGN_RETAILERS:
        return query

    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        return QUERY_TRANSLATIONS.get(word.lower(), word)

    translated = _QUERY_WORD_RX.sub(repl, query)
    translated = re.sub(r"\s+", " ", translated).strip()
    return translated or query


# ---------------------------------------------------------------------------
# Wildberries — public v5 search API
# ---------------------------------------------------------------------------
def _wb_extract_price(product: dict) -> Optional[int]:
    """Extract price in rubles from WB product dict.

    WB stores prices in kopecks (×100).  The `sizes[0].price.product`
    field is the discounted price the buyer actually pays.
    """
    sizes = product.get("sizes") or []
    if not sizes:
        return None
    price_block = sizes[0].get("price") or {}
    # 'product' = discounted, 'basic' = crossed-out
    raw = price_block.get("product") or price_block.get("basic")
    if raw and isinstance(raw, (int, float)):
        return int(raw) // 100
    return None


def _wb_image_url(product_id: int) -> str:
    """Construct image URL from WB product ID using their CDN scheme."""
    vol = product_id // 100000
    part = product_id // 1000
    # WB CDN basket assignment by volume range
    if vol <= 143:
        host = "basket-01"
    elif vol <= 287:
        host = "basket-02"
    elif vol <= 431:
        host = "basket-03"
    elif vol <= 719:
        host = "basket-04"
    elif vol <= 1007:
        host = "basket-05"
    elif vol <= 1061:
        host = "basket-06"
    elif vol <= 1115:
        host = "basket-07"
    elif vol <= 1169:
        host = "basket-08"
    elif vol <= 1313:
        host = "basket-09"
    elif vol <= 1601:
        host = "basket-10"
    elif vol <= 1655:
        host = "basket-11"
    elif vol <= 1919:
        host = "basket-12"
    elif vol <= 2045:
        host = "basket-13"
    elif vol <= 2189:
        host = "basket-14"
    elif vol <= 2405:
        host = "basket-15"
    elif vol <= 2621:
        host = "basket-16"
    elif vol <= 2837:
        host = "basket-17"
    else:
        host = "basket-18"
    return f"https://{host}.wbbasket.ru/vol{vol}/part{part}/{product_id}/images/big/1.webp"


async def search_wildberries(
    queries: Sequence[str],
    *,
    limit: int = 10,
    timeout: float = _TIMEOUT,
) -> list[dict]:
    """Search Wildberries via public v5 API.

    Tries each query in order, returns results from the first one
    that yields products (most-specific query first per expansion tree).
    """
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    results: list[dict] = []

    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        for query in queries:
            if results:
                break  # already got results from more specific query
            encoded = urllib.parse.quote(query)
            url = (
                f"https://search.wb.ru/exactmatch/ru/common/v5/search"
                f"?appType=1&curr=rub&dest={_WB_DEST}"
                f"&query={encoded}&resultset=catalog"
                f"&sort=popular&spp=30&limit={limit}"
            )
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    log.warning("wb: rate limited on query %r", query)
                    await asyncio.sleep(1.5)
                    continue
                if resp.status_code != 200:
                    log.warning("wb: HTTP %d for query %r", resp.status_code, query)
                    continue
                data = resp.json()
                products = data.get("products") or []
                if not products:
                    # v5 might also nest under data.products
                    products = (data.get("data") or {}).get("products") or []
                for p in products[:limit]:
                    pid = p.get("id")
                    if not pid:
                        continue
                    price = _wb_extract_price(p)
                    results.append({
                        "title": p.get("name", ""),
                        "brand": p.get("brand", ""),
                        "url": f"https://www.wildberries.ru/catalog/{pid}/detail.aspx",
                        "price": price,
                        "store": "Wildberries",
                        "source": "wildberries",
                        "image_url": _wb_image_url(pid),
                        "_wb_id": pid,
                    })
                if results:
                    log.info("wb: %d results for query %r", len(results), query)
            except httpx.TimeoutException:
                log.warning("wb: timeout for query %r", query)
            except Exception as e:
                log.warning("wb: error for query %r: %s", query, e)

    return results


# ---------------------------------------------------------------------------
# Ozon — requires cookie file (Netscape format)
# ---------------------------------------------------------------------------
def _load_ozon_cookies(path: str) -> dict[str, str]:
    """Load cookies from Netscape-format cookie file into dict."""
    jar = http.cookiejar.MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        log.warning("ozon: cannot load cookies from %s: %s", path, e)
        return {}
    return {c.name: c.value for c in jar}


async def search_ozon(
    queries: Sequence[str],
    *,
    cookies_path: str = OZON_COOKIES_PATH,
    limit: int = 10,
    timeout: float = _TIMEOUT,
) -> list[dict]:
    """Search Ozon using their internal JSON API.

    Requires valid browser cookies (exported in Netscape format).
    Returns empty if cookies are missing/expired.
    """
    cookies = _load_ozon_cookies(cookies_path)
    if not cookies:
        log.info("ozon: no cookies available, skipping")
        return []

    headers = {
        "User-Agent": _UA,
        "Accept": "application/json",
    }
    results: list[dict] = []

    async with httpx.AsyncClient(
        headers=headers,
        cookies=cookies,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        for query in queries:
            if results:
                break
            encoded = urllib.parse.quote(query)
            url = (
                f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
                f"?url=/search/?text={encoded}"
                f"&layout_container=searchMegapagination"
                f"&layout_page_index=1"
            )
            try:
                resp = await client.get(url)
                if resp.status_code in (307, 403, 401):
                    log.warning("ozon: HTTP %d — cookies likely expired", resp.status_code)
                    return []
                if resp.status_code != 200:
                    log.warning("ozon: HTTP %d for query %r", resp.status_code, query)
                    continue
                data = resp.json()
                # Ozon nests search results in widgetStates
                widget_states = data.get("widgetStates") or {}
                for key, val_raw in widget_states.items():
                    if "searchResultsV2" not in key:
                        continue
                    try:
                        val = json.loads(val_raw) if isinstance(val_raw, str) else val_raw
                    except (json.JSONDecodeError, TypeError):
                        continue
                    items = val.get("items") or []
                    for item in items[:limit]:
                        main = item.get("mainState") or []
                        title = ""
                        price_raw = ""
                        for atom in main:
                            if atom.get("atom", {}).get("type") == "textAtom":
                                txt = atom["atom"].get("text", "")
                                if not title and len(txt) > 5:
                                    title = txt
                            if atom.get("id") == "atom-price":
                                txt = atom.get("atom", {}).get("price", "")
                                if txt:
                                    price_raw = txt
                        # Fallback title
                        if not title:
                            title = item.get("title") or item.get("name") or ""
                        # Parse price
                        price = None
                        if price_raw:
                            digits = re.sub(r"[^\d]", "", price_raw)
                            if digits:
                                price = int(digits)
                        # URL
                        link = item.get("action", {}).get("link", "")
                        if link and not link.startswith("http"):
                            link = "https://www.ozon.ru" + link
                        # Image
                        imgs = item.get("tileImage") or {}
                        image_url = imgs.get("imageUrl") or ""

                        if title or link:
                            results.append({
                                "title": title,
                                "brand": "",
                                "url": link,
                                "price": price,
                                "store": "Ozon",
                                "source": "ozon",
                                "image_url": image_url,
                            })
                if results:
                    log.info("ozon: %d results for query %r", len(results), query)
            except httpx.TimeoutException:
                log.warning("ozon: timeout for query %r", query)
            except Exception as e:
                log.warning("ozon: error for query %r: %s", query, e)

    return results


# ---------------------------------------------------------------------------
# Yandex Market — link-only fallback (API is geo-blocked)
# ---------------------------------------------------------------------------
def yandex_market_links(queries: Sequence[str], *, limit: int = 3) -> list[dict]:
    """Generate direct search links for Yandex Market.

    YM blocks programmatic API access (403 / VPN detection), so we
    provide clickable search URLs the user can open in browser.
    """
    results: list[dict] = []
    for query in queries[:limit]:
        encoded = urllib.parse.quote(query)
        results.append({
            "title": f"🔗 Поиск: {query[:60]}",
            "brand": "",
            "url": f"https://market.yandex.ru/search?text={encoded}",
            "price": None,
            "store": "Яндекс.Маркет",
            "source": "yandex_market",
            "image_url": "",
            "_link_only": True,
        })
    return results


def retailer_links(
    queries: Sequence[str],
    sources: Sequence[str],
    *,
    limit_per_source: int = 1,
) -> list[dict]:
    """Generate direct search links for explicit retailer sources."""
    out: list[dict] = []
    plain_sources = [s.lower() for s in sources if not s.lower().startswith("brand:")]
    for src in plain_sources:
        template = RETAILER_SEARCH_URLS.get(src)
        if not template:
            continue
        title = RETAILER_TITLES.get(src, src)
        for query in list(queries)[:limit_per_source]:
            source_query = translate_query_for_source(query, src)
            encoded = urllib.parse.quote(source_query)
            out.append({
                "title": f"🔗 {title}: {source_query[:60]}",
                "brand": "",
                "url": template.format(query=encoded),
                "price": None,
                "store": title,
                "source": src,
                "image_url": "",
                "_link_only": True,
            })
    return out


def brand_site_links(
    queries: Sequence[str],
    sources: Sequence[str],
    *,
    category: str = '',
) -> list[dict]:
    """Generate brand entry-point links via ml_official_sites resolver.

    For known brands returns official site / distributor / authorized retailer
    links in priority order. For unknown brands falls back to web-search.
    """
    brand_sources = [s for s in sources if s.lower().startswith("brand:")]
    if not brand_sources:
        return []

    try:
        import ml_official_sites
    except ImportError:
        log.warning("ml_providers: ml_official_sites не найден, fallback на web-search")
        return _brand_site_links_fallback(queries, brand_sources)

    out: list[dict] = []
    for src in brand_sources:
        brand = src.split(":", 1)[1].strip()
        if not brand:
            continue
        query = queries[0] if queries else ''
        links = ml_official_sites.resolve_brand_links(brand, query, category)
        out.extend(links)
    return out


def _brand_site_links_fallback(
    queries: Sequence[str],
    brand_sources: Sequence[str],
) -> list[dict]:
    """Fallback если ml_official_sites недоступен."""
    out: list[dict] = []
    for src in brand_sources:
        brand = src.split(":", 1)[1].strip()
        if not brand:
            continue
        for query in list(queries)[:1]:
            official_query = f"{brand} {query} официальный сайт купить"
            for engine_name, template in WEB_SEARCH_ENGINES[:2]:
                encoded = urllib.parse.quote(official_query)
                out.append({
                    "title": f"🔗 {brand}: {engine_name} официальный поиск",
                    "brand": brand,
                    "url": template.format(query=encoded),
                    "price": None,
                    "store": "Официальный сайт",
                    "source": "brand_site",
                    "image_url": "",
                    "_link_only": True,
                })
    return out


# ---------------------------------------------------------------------------
# Composite provider — the one you plug into ml_search_v2
# ---------------------------------------------------------------------------
async def composite_provider(
    queries: list[str],
    sources: list[str],
    photo_path: Optional[str],
) -> list[dict]:
    """Fetch candidates with an emphasis on direct seller links.

    This is the production `CandidatesProvider` for ml_search_v2.
    Matches the signature: (queries, sources, photo_path) -> list[dict].

    `sources` is the bandit-ranked list from route_sources(). We only
    query APIs or emit links for sources that appear in the list.
    """
    src_set = {s.lower() for s in sources}

    tasks: list[asyncio.Task] = []
    task_labels: list[str] = []
    all_results: list[dict] = []

    all_results.extend(brand_site_links(queries, sources))
    all_results.extend(retailer_links(queries, sources))

    if any(s in src_set for s in ("wildberries", "wb")):
        tasks.append(asyncio.ensure_future(search_wildberries(queries)))
        task_labels.append("wb")

    # YM link-only — run synchronously (no network call)
    if any(s in src_set for s in ("yandex_market", "ym")):
        all_results.extend(yandex_market_links(queries))

    # Wait for async providers
    if tasks:
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for label, result in zip(task_labels, done):
            if isinstance(result, Exception):
                log.warning("composite_provider: %s failed: %s", label, result)
                continue
            if isinstance(result, list):
                all_results.extend(result)

    log.info(
        "composite_provider: %d total candidates from %d sources "
        "(queries=%d)",
        len(all_results), len(sources), len(queries),
    )
    return all_results
