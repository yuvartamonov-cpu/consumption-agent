"""
ml_official_sites.py — Resolver для официальных сайтов, дистрибьюторов
и авторизованных ритейлеров брендов.

Вместо generic Google/Yandex поисковых ссылок на «бренд + купить»,
возвращаем прямые entry points:
  1. Официальный сайт бренда (ru-версия если есть)
  2. Российский дистрибьютор / авторизованный ритейлер
  3. Брендовая страница на крупных маркетплейсах

Ordering: official > distributor > authorized_retailer > brand_page

Public API:
    resolve_brand_links(brand, query, category) -> list[dict]
    KNOWN_BRANDS -> dict
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Optional, Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Справочник брендов: official + distributor + authorized
# ---------------------------------------------------------------------------
# Формат:
#   brand_key: {
#       'official': 'https://...',        # офиц. сайт (ru-версия)
#       'official_search': 'https://...{query}',  # поиск на офиц. сайте
#       'distributor': [('title', 'url')],  # дистрибьюторы в РФ
#       'authorized': [('title', 'url')],   # авторизованные ритейлеры
#       'brand_pages': [('title', 'url')],  # страницы бренда на маркетплейсах
#       'aliases': ['...'],                 # другие варианты написания
#   }
#
# Ключ — lowercase бренда. Если бренд не найден, fallback на web-search.

KNOWN_BRANDS: dict[str, dict] = {
    # ─── Одежда / обувь ───
    'nike': {
        'official': 'https://www.nike.com/ru/',
        'official_search': 'https://www.nike.com/ru/w?q={query}',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/1057/brand-nike/'),
            ('Sneakerhead', 'https://sneakerhead.ru/catalogsearch/result/?q={query}'),
        ],
        'brand_pages': [
            ('Wildberries', 'https://www.wildberries.ru/brands/nike'),
        ],
    },
    'adidas': {
        'official': 'https://www.adidas.ru/',
        'official_search': 'https://www.adidas.ru/search?q={query}',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/1058/brand-adidas/'),
            ('Sneakerhead', 'https://sneakerhead.ru/catalogsearch/result/?q={query}'),
        ],
        'brand_pages': [
            ('Wildberries', 'https://www.wildberries.ru/brands/adidas'),
        ],
    },
    'puma': {
        'official': 'https://ru.puma.com/',
        'official_search': 'https://ru.puma.com/search?q={query}',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/2285/brand-puma/'),
        ],
    },
    'new balance': {
        'official': 'https://www.newbalance.com/ru/',
        'official_search': 'https://www.newbalance.com/search?q={query}',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/1826/brand-new-balance/'),
            ('Sneakerhead', 'https://sneakerhead.ru/catalogsearch/result/?q={query}'),
        ],
        'aliases': ['nb', 'newbalance'],
    },
    'reebok': {
        'official': 'https://www.reebok.ru/',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/1096/brand-reebok/'),
        ],
    },
    'asics': {
        'official': 'https://www.asics.com/ru/ru-ru/',
        'official_search': 'https://www.asics.com/ru/ru-ru/search?q={query}',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/2074/brand-asics/'),
        ],
    },
    'ralph lauren': {
        'official': 'https://www.ralphlauren.com/',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/2305/brand-polo-ralph-lauren/'),
            ('Brandshop', 'https://brandshop.ru/search/?q={query}'),
        ],
        'aliases': ['polo ralph lauren', 'ralph'],
    },
    'tommy hilfiger': {
        'official': 'https://ru.tommy.com/',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/1124/brand-tommy-hilfiger/'),
        ],
        'aliases': ['tommy'],
    },
    "levi's": {
        'official': 'https://www.levi.com/RU/ru/',
        'authorized': [
            ('Lamoda', 'https://www.lamoda.ru/b/1045/brand-levis/'),
        ],
        'aliases': ['levis', 'levi s', 'левис', 'левайс'],
    },
    'zara': {
        'official': 'https://www.zara.com/ru/',
        'official_search': 'https://www.zara.com/ru/ru/search?searchTerm={query}',
    },
    'h&m': {
        'official': 'https://www2.hm.com/ru_ru/',
        'official_search': 'https://www2.hm.com/ru_ru/search-results.html?q={query}',
        'aliases': ['hm', 'h and m'],
    },
    'uniqlo': {
        'official': 'https://www.uniqlo.com/ru/ru/',
        'official_search': 'https://www.uniqlo.com/ru/ru/search?q={query}',
    },
    'cos': {
        'official': 'https://www.cos.com/en_eur/',
    },
    'mango': {
        'official': 'https://shop.mango.com/ru/',
        'official_search': 'https://shop.mango.com/ru/search?kw={query}',
    },
    'massimo dutti': {
        'official': 'https://www.massimodutti.com/ru/',
    },

    # ─── Техника ───
    'apple': {
        'official': 'https://www.apple.com/ru/',
        'official_search': 'https://www.apple.com/ru/shop/go/search/{query}',
        'distributor': [
            ('re:Store', 'https://www.re-store.ru/search/?q={query}'),
        ],
        'authorized': [
            ('DNS', 'https://www.dns-shop.ru/search/?q={query}'),
            ('М.Видео', 'https://www.mvideo.ru/search?text={query}'),
            ('Citilink', 'https://www.citilink.ru/search/?text={query}'),
        ],
    },
    'samsung': {
        'official': 'https://www.samsung.com/ru/',
        'official_search': 'https://www.samsung.com/ru/search/?searchvalue={query}',
        'authorized': [
            ('DNS', 'https://www.dns-shop.ru/search/?q={query}'),
            ('М.Видео', 'https://www.mvideo.ru/search?text={query}'),
        ],
    },
    'xiaomi': {
        'official': 'https://www.mi.com/ru/',
        'distributor': [
            ('Mi Store', 'https://store.mi.com/ru/search?keyword={query}'),
        ],
        'authorized': [
            ('DNS', 'https://www.dns-shop.ru/search/?q={query}'),
        ],
        'aliases': ['mi', 'сяоми'],
    },
    'sony': {
        'official': 'https://www.sony.ru/',
        'authorized': [
            ('DNS', 'https://www.dns-shop.ru/search/?q={query}'),
            ('М.Видео', 'https://www.mvideo.ru/search?text={query}'),
        ],
    },
    'dyson': {
        'official': 'https://www.dyson.ru/',
        'official_search': 'https://www.dyson.ru/search?q={query}',
        'authorized': [
            ('М.Видео', 'https://www.mvideo.ru/search?text={query}'),
        ],
    },
    'jbl': {
        'official': 'https://ru.jbl.com/',
        'authorized': [
            ('DNS', 'https://www.dns-shop.ru/search/?q={query}'),
            ('М.Видео', 'https://www.mvideo.ru/search?text={query}'),
        ],
    },
    'bose': {
        'official': 'https://www.bose.com/ru/',
        'authorized': [
            ('М.Видео', 'https://www.mvideo.ru/search?text={query}'),
        ],
    },

    # ─── Мебель / интерьер ───
    'ikea': {
        'official': 'https://www.ikea.com/ru/ru/',
        'official_search': 'https://www.ikea.com/ru/ru/search/?q={query}',
        'aliases': ['икея', 'икеа'],
    },
    'hoff': {
        'official': 'https://hoff.ru/',
        'official_search': 'https://hoff.ru/search/?q={query}',
        'aliases': ['хофф'],
    },

    # ─── Косметика ───
    'mac': {
        'official': 'https://www.maccosmetics.ru/',
        'authorized': [
            ('Золотое Яблоко', 'https://goldapple.ru/catalogsearch/result/?q={query}'),
            ('Иль де Ботэ', 'https://iledebeaute.ru/search/?q={query}'),
        ],
        'aliases': ['mac cosmetics', 'мак'],
    },
    'estee lauder': {
        'official': 'https://www.esteelauder.ru/',
        'authorized': [
            ('Золотое Яблоко', 'https://goldapple.ru/catalogsearch/result/?q={query}'),
        ],
        'aliases': ['эсти лаудер'],
    },
    'clinique': {
        'official': 'https://www.clinique.ru/',
        'authorized': [
            ('Золотое Яблоко', 'https://goldapple.ru/catalogsearch/result/?q={query}'),
        ],
    },
    'la roche-posay': {
        'official': 'https://www.laroche-posay.ru/',
        'authorized': [
            ('Золотое Яблоко', 'https://goldapple.ru/catalogsearch/result/?q={query}'),
        ],
        'aliases': ['laroche posay', 'ларош позе'],
    },
}

# Обратный индекс: alias -> brand_key
_ALIAS_INDEX: dict[str, str] = {}
for _bk, _info in KNOWN_BRANDS.items():
    _ALIAS_INDEX[_bk] = _bk
    for _alias in _info.get('aliases', []):
        _ALIAS_INDEX[_alias.lower()] = _bk

_NONWORD = re.compile(r'[^\w]+', re.UNICODE)


def _normalize_brand(brand: str) -> str:
    """Приводим к lowercase, убираем лишние символы."""
    return _NONWORD.sub(' ', brand.lower()).strip()


def lookup_brand(brand: str) -> Optional[dict]:
    """Найти бренд в справочнике по имени или алиасу.

    Exact match по ключу или алиасу. Partial match только если
    alias является полным словом внутри запроса (минимум 3 символа).
    """
    key = _normalize_brand(brand)
    if not key:
        return None
    canon = _ALIAS_INDEX.get(key)
    if canon:
        return KNOWN_BRANDS.get(canon)
    # Partial match: alias как целое слово внутри key (или наоборот),
    # но только для алиасов >= 3 символов, чтобы не ловить мусор.
    key_words = set(key.split())
    for alias, canon_key in _ALIAS_INDEX.items():
        if len(alias) < 3:
            continue
        alias_words = set(alias.split())
        # Все слова алиаса должны присутствовать в запросе
        if alias_words and alias_words.issubset(key_words):
            return KNOWN_BRANDS.get(canon_key)
    return None


# ---------------------------------------------------------------------------
# Генерация ссылок
# ---------------------------------------------------------------------------

def _fmt_url(template: str, query: str) -> str:
    encoded = urllib.parse.quote(query)
    return template.format(query=encoded)


def resolve_brand_links(
    brand: str,
    query: str = '',
    category: str = '',
) -> list[dict]:
    """Возвращает упорядоченные ссылки для бренда.

    Порядок: DB user links > DB category retailers > official > distributor > authorized > brand_page > fallback_search.
    Каждый элемент — dict с полями title, url, store, source, tier, _link_only.

    tier: 'user_db' | 'category_db' | 'official' | 'distributor' | 'authorized' | 'brand_page' | 'search_fallback'
    """
    out: list[dict] = []
    
    # 0. User DB links
    try:
        import sqlite3
        from consumption.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT store_name, url FROM user_brand_links WHERE brand = ?", (brand.lower().strip(),)).fetchall()
        for r in rows:
            store_name = r[0]
            url_pattern = r[1]
            url = url_pattern.replace('{query}', urllib.parse.quote(query)) if '{query}' in url_pattern else url_pattern
            out.append({
                'title': f'💾 Из вашей базы: {store_name} ({brand})',
                'url': url,
                'store': store_name,
                'source': 'user_db',
                'tier': 'user_db',
                '_link_only': True,
            })
        conn.close()
    except Exception as e:
        log.warning(f"Failed to fetch user brand links: {e}")

    # 0.5 Category DB links (LLM discovered)
    if category:
        try:
            from ml_retailer_discovery import get_or_discover_retailers
            retailers = get_or_discover_retailers(category)
            for r in retailers:
                store_name = r['store_name']
                url_pattern = r['url_template']
                search_term = query or brand or category
                url = url_pattern.replace('{query}', urllib.parse.quote(search_term))
                out.append({
                    'title': f'🌐 Продавец ({category}): {store_name}',
                    'url': url,
                    'store': store_name,
                    'source': 'category_db',
                    'tier': 'category_db',
                    '_link_only': True,
                })
        except Exception as e:
            log.warning(f"Failed to fetch category retailers: {e}")

    info = lookup_brand(brand)

    if not info and not out:
        # Бренд не в справочнике и нет в БД — делаем web-search fallback
        return _search_fallback_links(brand, query)

    search_query = query or brand

    # 1. Официальный сайт
    official_url = info.get('official_search')
    if official_url:
        out.append(_link(
            title=f'{brand} — официальный сайт',
            url=_fmt_url(official_url, search_query),
            store='Официальный сайт',
            source='official_site',
            tier='official',
            brand=brand,
        ))
    elif info.get('official'):
        out.append(_link(
            title=f'{brand} — официальный сайт',
            url=info['official'],
            store='Официальный сайт',
            source='official_site',
            tier='official',
            brand=brand,
        ))

    # 2. Дистрибьюторы
    for dist_title, dist_url in info.get('distributor', []):
        out.append(_link(
            title=f'{brand} → {dist_title} (дистрибьютор)',
            url=_fmt_url(dist_url, search_query),
            store=dist_title,
            source='distributor',
            tier='distributor',
            brand=brand,
        ))

    # 3. Авторизованные ритейлеры
    for auth_title, auth_url in info.get('authorized', []):
        out.append(_link(
            title=f'{brand} → {auth_title}',
            url=_fmt_url(auth_url, search_query),
            store=auth_title,
            source='authorized_retailer',
            tier='authorized',
            brand=brand,
        ))

    # 4. Страницы бренда на маркетплейсах
    for bp_title, bp_url in info.get('brand_pages', []):
        out.append(_link(
            title=f'{brand} на {bp_title}',
            url=_fmt_url(bp_url, search_query) if '{query}' in bp_url else bp_url,
            store=bp_title,
            source='brand_page',
            tier='brand_page',
            brand=brand,
        ))

    if not out:
        return _search_fallback_links(brand, query)

    log.info('[official] %s: %d entry points (official=%d, dist=%d, auth=%d)',
             brand, len(out),
             sum(1 for x in out if x['tier'] == 'official'),
             sum(1 for x in out if x['tier'] == 'distributor'),
             sum(1 for x in out if x['tier'] == 'authorized'))
    return out


def _link(*, title: str, url: str, store: str, source: str,
          tier: str, brand: str) -> dict:
    return {
        'title': f'🏷 {title}',
        'brand': brand,
        'url': url,
        'price': None,
        'store': store,
        'source': source,
        'tier': tier,
        'image_url': '',
        '_link_only': True,
    }


# Web-search fallback для неизвестных брендов
WEB_SEARCH_ENGINES = [
    ('Google', 'https://www.google.com/search?q={query}'),
    ('Yandex', 'https://yandex.ru/search/?text={query}'),
]


def _search_fallback_links(brand: str, query: str) -> list[dict]:
    """Generic web-search ссылки для брендов, которых нет в справочнике."""
    out: list[dict] = []
    search_text = f'{brand} {query} официальный сайт купить'.strip()
    for engine_name, template in WEB_SEARCH_ENGINES:
        out.append(_link(
            title=f'{brand}: {engine_name} — поиск офиц. сайта',
            url=_fmt_url(template, search_text),
            store=engine_name,
            source='search_fallback',
            tier='search_fallback',
            brand=brand,
        ))
    return out


# ---------------------------------------------------------------------------
# Tier ordering для сортировки результатов
# ---------------------------------------------------------------------------
TIER_PRIORITY = {
    'official': 0,
    'distributor': 1,
    'authorized': 2,
    'brand_page': 3,
    'search_fallback': 4,
}


def sort_by_tier(links: list[dict]) -> list[dict]:
    """Сортирует ссылки по приоритету tier."""
    return sorted(links, key=lambda x: TIER_PRIORITY.get(x.get('tier', ''), 99))
