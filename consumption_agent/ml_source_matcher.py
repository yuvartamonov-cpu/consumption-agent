"""
ml_source_matcher.py — Динамический подбор источников по типу товара.

Заменяет жёсткие CATEGORY_SOURCES в ml_search_v2.py на обучаемую
систему, где источники классифицированы по item_type и tier,
а система учится по кликам пользователя.

Таблица search_sources в consumption.db:
    CREATE TABLE IF NOT EXISTS search_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        url_template TEXT,
        site_domain TEXT,
        category_tags TEXT,         -- JSON list: ['одежда', 'обувь', ...]
        item_types TEXT,            -- JSON list: ['luxury_clothing', 'streetwear', ...]
        geo TEXT DEFAULT 'RU',
        tier TEXT DEFAULT 'aggregator',  -- manufacturer | distributor | aggregator | marketplace
        score REAL DEFAULT 1.0,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS source_clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT NOT NULL,
        item_type TEXT NOT NULL,
        action TEXT NOT NULL,         -- 'click' | 'skip'
        created_at TEXT DEFAULT (datetime('now'))
    );
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Optional

log = logging.getLogger(__name__)

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'consumption.db')

# -----------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------

SEARCH_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS search_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    url_template TEXT,
    site_domain TEXT,
    category_tags TEXT,
    item_types TEXT,
    geo TEXT DEFAULT 'RU',
    tier TEXT DEFAULT 'aggregator',
    score REAL DEFAULT 1.0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS source_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    item_type TEXT NOT NULL,
    action TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_source_clicks_lookup
    ON source_clicks(source_key, item_type, action);
"""

# -----------------------------------------------------------------------
# Seed data — широкая база сайтов производителей, дистрибьюторов,
# агрегаторов, маркетплейсов с классификацией по типу товара
# -----------------------------------------------------------------------

SEED_SOURCES = [
    # ── Производители (tier=manufacturer) ──
    {
        'key': 'nike',
        'name': 'Nike',
        'url_template': 'https://www.nike.com/w?q={query}',
        'site_domain': 'nike.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['sportswear', 'footwear', 'streetwear',
                                  'running']),
        'geo': 'ALL',
        'tier': 'manufacturer',
    },
    {
        'key': 'adidas',
        'name': 'Adidas',
        'url_template': 'https://www.adidas.com/us/search?q={query}',
        'site_domain': 'adidas.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['sportswear', 'footwear', 'streetwear']),
        'geo': 'ALL',
        'tier': 'manufacturer',
    },
    {
        'key': 'apple',
        'name': 'Apple',
        'url_template': 'https://www.apple.com/search/{query}',
        'site_domain': 'apple.com',
        'category_tags': json.dumps(['техника']),
        'item_types': json.dumps(['electronics', 'computers', 'smartphones',
                                  'audio']),
        'geo': 'ALL',
        'tier': 'manufacturer',
    },
    {
        'key': 'ikea',
        'name': 'IKEA',
        'url_template': 'https://www.ikea.com/ru/ru/search/?q={query}',
        'site_domain': 'ikea.com',
        'category_tags': json.dumps(['мебель', 'интерьер']),
        'item_types': json.dumps(['furniture', 'home_decor', 'lighting']),
        'geo': 'RU',
        'tier': 'manufacturer',
    },
    # ── Дистрибьюторы / ритейлеры (tier=distributor) ──
    {
        'key': 'lamoda',
        'name': 'Lamoda',
        'url_template': 'https://www.lamoda.ru/catalogsearch/result/?q={query}',
        'site_domain': 'lamoda.ru',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'streetwear', 'footwear',
                                  'formal_wear', 'sportswear']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'brandshop',
        'name': 'Brandshop',
        'url_template': 'https://brandshop.ru/search/?q={query}',
        'site_domain': 'brandshop.ru',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['streetwear', 'footwear', 'sportswear']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'sneakerhead',
        'name': 'Sneakerhead',
        'url_template': 'https://sneakerhead.ru/search/?q={query}',
        'site_domain': 'sneakerhead.ru',
        'category_tags': json.dumps(['обувь', 'одежда', 'аксессуары']),
        'item_types': json.dumps(['footwear', 'streetwear']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'tsum',
        'name': 'ЦУМ',
        'url_template': '',
        'site_domain': 'tsum.ru',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'footwear',
                                  'accessories', 'formal_wear']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'oskelly',
        'name': 'Oskelly',
        'url_template': '',
        'site_domain': 'oskelly.ru',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'streetwear',
                                  'footwear', 'accessories']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'thecultt',
        'name': 'The Cultt',
        'url_template': '',
        'site_domain': 'thecultt.com',
        'category_tags': json.dumps(['одежда', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'streetwear',
                                  'accessories']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'rendezvous',
        'name': 'Rendez-Vous',
        'url_template': '',
        'site_domain': 'rendez-vous.ru',
        'category_tags': json.dumps(['обувь', 'аксессуары']),
        'item_types': json.dumps(['footwear', 'accessories']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'leform',
        'name': 'Leform',
        'url_template': 'https://leform.ru/search/?q={query}',
        'site_domain': 'leform.ru',
        'category_tags': json.dumps(['одежда']),
        'item_types': json.dumps(['luxury_clothing', 'streetwear',
                                  'formal_wear']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'peakstore',
        'name': 'Peak Store',
        'url_template': 'https://peakstore.ru/search/?q={query}',
        'site_domain': 'peakstore.ru',
        'category_tags': json.dumps(['одежда', 'аксессуары']),
        'item_types': json.dumps(['streetwear', 'sportswear', 'footwear']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'poisondrop',
        'name': 'Poison Drop',
        'url_template': '',
        'site_domain': 'poisondrop.ru',
        'category_tags': json.dumps(['аксессуары']),
        'item_types': json.dumps(['accessories', 'jewelry']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'dns',
        'name': 'DNS',
        'url_template': 'https://www.dns-shop.ru/search/?q={query}',
        'site_domain': 'dns-shop.ru',
        'category_tags': json.dumps(['техника']),
        'item_types': json.dumps(['electronics', 'computers', 'audio',
                                  'smartphones']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'citilink',
        'name': 'Citilink',
        'url_template': 'https://www.citilink.ru/search/?text={query}',
        'site_domain': 'citilink.ru',
        'category_tags': json.dumps(['техника']),
        'item_types': json.dumps(['electronics', 'computers', 'audio',
                                  'kitchen_appliances']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'mvideo',
        'name': 'М.Видео',
        'url_template': 'https://www.mvideo.ru/search?text={query}',
        'site_domain': 'mvideo.ru',
        'category_tags': json.dumps(['техника']),
        'item_types': json.dumps(['electronics', 'audio', 'kitchen_appliances',
                                  'smartphones', 'computers']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'restore',
        'name': 'restore:',
        'url_template': 'https://www.restore.ru/search/?q={query}',
        'site_domain': 'restore.ru',
        'category_tags': json.dumps(['техника']),
        'item_types': json.dumps(['electronics', 'computers', 'audio',
                                  'smartphones']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'goldapple',
        'name': 'Золотое Яблоко',
        'url_template': 'https://goldapple.ru/catalogsearch/result/?q={query}',
        'site_domain': 'goldapple.ru',
        'category_tags': json.dumps(['косметика']),
        'item_types': json.dumps(['cosmetics', 'skincare', 'perfume',
                                  'makeup']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'iledebeaute',
        'name': 'Иль де Ботэ',
        'url_template': 'https://iledebeaute.ru/search/?q={query}',
        'site_domain': 'iledebeaute.ru',
        'category_tags': json.dumps(['косметика']),
        'item_types': json.dumps(['cosmetics', 'skincare', 'perfume',
                                  'makeup']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'rivegauche',
        'name': 'Рив Гош',
        'url_template': 'https://rivegauche.ru/search?text={query}',
        'site_domain': 'rivegauche.ru',
        'category_tags': json.dumps(['косметика']),
        'item_types': json.dumps(['cosmetics', 'skincare', 'perfume',
                                  'makeup']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'hoff',
        'name': 'Hoff',
        'url_template': 'https://hoff.ru/search/?q={query}',
        'site_domain': 'hoff.ru',
        'category_tags': json.dumps(['мебель', 'интерьер']),
        'item_types': json.dumps(['furniture', 'home_decor']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'inmyroom',
        'name': 'InMyRoom',
        'url_template': 'https://www.inmyroom.ru/search?query={query}',
        'site_domain': 'inmyroom.ru',
        'category_tags': json.dumps(['мебель', 'интерьер']),
        'item_types': json.dumps(['furniture', 'home_decor', 'lighting']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'divan_ru',
        'name': 'Divan.ru',
        'url_template': 'https://www.divan.ru/search?query={query}',
        'site_domain': 'divan.ru',
        'category_tags': json.dumps(['мебель']),
        'item_types': json.dumps(['furniture']),
        'geo': 'RU',
        'tier': 'distributor',
    },
    {
        'key': 'farfetch',
        'name': 'Farfetch',
        'url_template': '',
        'site_domain': 'farfetch.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'footwear',
                                  'accessories']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'mytheresa',
        'name': 'Mytheresa',
        'url_template': '',
        'site_domain': 'mytheresa.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'footwear',
                                  'accessories']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'luisaviaroma',
        'name': 'Luisaviaroma',
        'url_template': '',
        'site_domain': 'luisaviaroma.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'footwear',
                                  'accessories']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'netaporter',
        'name': 'Net-a-Porter',
        'url_template': '',
        'site_domain': 'net-a-porter.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'footwear',
                                  'accessories']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'vestiairecollective',
        'name': 'Vestiaire Collective',
        'url_template': '',
        'site_domain': 'vestiairecollective.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['luxury_clothing', 'footwear',
                                  'accessories']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'grailed',
        'name': 'Grailed',
        'url_template': '',
        'site_domain': 'grailed.com',
        'category_tags': json.dumps(['одежда', 'обувь', 'аксессуары']),
        'item_types': json.dumps(['streetwear', 'footwear',
                                  'accessories']),  # resell + streetwear
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'stockx',
        'name': 'StockX',
        'url_template': '',
        'site_domain': 'stockx.com',
        'category_tags': json.dumps(['обувь', 'одежда', 'аксессуары']),
        'item_types': json.dumps(['footwear', 'streetwear',
                                  'accessories']),  # resell
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'goat',
        'name': 'GOAT',
        'url_template': '',
        'site_domain': 'goat.com',
        'category_tags': json.dumps(['обувь']),
        'item_types': json.dumps(['footwear']),  # resell
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'cultbeauty',
        'name': 'Cult Beauty',
        'url_template': '',
        'site_domain': 'cultbeauty.com',
        'category_tags': json.dumps(['косметика']),
        'item_types': json.dumps(['cosmetics', 'skincare', 'makeup']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'lookfantastic',
        'name': 'Lookfantastic',
        'url_template': '',
        'site_domain': 'lookfantastic.com',
        'category_tags': json.dumps(['косметика']),
        'item_types': json.dumps(['cosmetics', 'skincare', 'makeup']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    {
        'key': 'sephora',
        'name': 'Sephora',
        'url_template': '',
        'site_domain': 'sephora.com',
        'category_tags': json.dumps(['косметика']),
        'item_types': json.dumps(['cosmetics', 'skincare', 'perfume',
                                  'makeup']),
        'geo': 'ALL',
        'tier': 'distributor',
    },
    # ── Агрегаторы (tier=aggregator) ──
    {
        'key': 'yandex_market',
        'name': 'Яндекс.Маркет',
        'url_template': 'https://market.yandex.ru/search?text={query}',
        'site_domain': '',
        'category_tags': json.dumps(['одежда', 'обувь', 'техника', 'мебель',
                                     'интерьер', 'косметика', 'аксессуары',
                                     'еда']),
        'item_types': json.dumps(['all']),
        'geo': 'RU',
        'tier': 'aggregator',
    },
    {
        'key': 'onliner',
        'name': 'Onliner',
        'url_template': 'https://catalog.onliner.by/search?query={query}',
        'site_domain': '',
        'category_tags': json.dumps(['техника', 'мебель', 'интерьер']),
        'item_types': json.dumps(['electronics', 'computers',
                                  'kitchen_appliances', 'furniture']),
        'geo': 'BY',
        'tier': 'aggregator',
    },
    {
        'key': 'idealo',
        'name': 'Idealo',
        'url_template': 'https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q={query}',
        'site_domain': '',
        'category_tags': json.dumps(['техника', 'мебель', 'интерьер',
                                     'косметика', 'одежда']),
        'item_types': json.dumps(['electronics', 'computers', 'furniture',
                                  'home_decor', 'cosmetics']),
        'geo': 'EU',
        'tier': 'aggregator',
    },
    {
        'key': 'billiger',
        'name': 'Billiger',
        'url_template': 'https://www.billiger.de/search?searchterm={query}',
        'site_domain': '',
        'category_tags': json.dumps(['техника', 'мебель']),
        'item_types': json.dumps(['electronics', 'computers', 'furniture']),
        'geo': 'EU',
        'tier': 'aggregator',
    },
    {
        'key': 'google_shopping_us',
        'name': 'Google Shopping US',
        'url_template': 'https://www.google.com/search?tbm=shop&gl=us&hl=en&q={query}',
        'site_domain': '',
        'category_tags': json.dumps(['одежда', 'обувь', 'техника', 'мебель',
                                     'интерьер', 'косметика', 'аксессуары']),
        'item_types': json.dumps(['all']),
        'geo': 'US',
        'tier': 'aggregator',
    },
    {
        'key': 'shopzilla',
        'name': 'Shopzilla',
        'url_template': 'https://www.shopzilla.com/search?keyword={query}',
        'site_domain': '',
        'category_tags': json.dumps(['одежда', 'обувь', 'техника', 'мебель',
                                     'интерьер']),
        'item_types': json.dumps(['all']),
        'geo': 'US',
        'tier': 'aggregator',
    },
    {
        'key': 'kaspi',
        'name': 'Kaspi',
        'url_template': 'https://kaspi.kz/shop/search/?text={query}',
        'site_domain': '',
        'category_tags': json.dumps(['техника', 'мебель', 'интерьер',
                                     'косметика', 'одежда']),
        'item_types': json.dumps(['electronics', 'computers', 'furniture',
                                  'home_decor', 'cosmetics']),
        'geo': 'KZ',
        'tier': 'aggregator',
    },
    {
        'key': 'satu_kz',
        'name': 'Satu.kz',
        'url_template': 'https://satu.kz/search?search_term={query}',
        'site_domain': '',
        'category_tags': json.dumps(['техника', 'мебель', 'интерьер',
                                     'одежда']),
        'item_types': json.dumps(['electronics', 'furniture', 'home_decor']),
        'geo': 'KZ',
        'tier': 'aggregator',
    },
    # ── Маркетплейсы (tier=marketplace) — в последнюю очередь ──
    {
        'key': 'wildberries',
        'name': 'Wildberries',
        'url_template': '',
        'site_domain': 'wildberries.ru',
        'category_tags': json.dumps(['одежда', 'обувь', 'техника', 'мебель',
                                     'интерьер', 'косметика', 'аксессуары',
                                     'еда', 'другое']),
        'item_types': json.dumps(['all']),
        'geo': 'RU',
        'tier': 'marketplace',
    },
    {
        'key': 'ozon',
        'name': 'Ozon',
        'url_template': '',
        'site_domain': 'ozon.ru',
        'category_tags': json.dumps(['одежда', 'обувь', 'техника', 'мебель',
                                     'интерьер', 'косметика', 'аксессуары',
                                     'еда', 'другое']),
        'item_types': json.dumps(['all']),
        'geo': 'RU',
        'tier': 'marketplace',
    },
    {
        'key': 'amazon',
        'name': 'Amazon',
        'url_template': 'https://www.amazon.com/s?k={query}',
        'site_domain': '',
        'category_tags': json.dumps(['одежда', 'обувь', 'техника', 'мебель',
                                     'интерьер', 'косметика', 'аксессуары',
                                     'еда', 'другое']),
        'item_types': json.dumps(['all']),
        'geo': 'ALL',
        'tier': 'marketplace',
    },
    {
        'key': 'ebay',
        'name': 'eBay',
        'url_template': 'https://www.ebay.com/sch/i.html?_nkw={query}',
        'site_domain': '',
        'category_tags': json.dumps(['anything', 'all']),
        'item_types': json.dumps(['all']),
        'geo': 'ALL',
        'tier': 'marketplace',
    },
    {
        'key': 'aliexpress',
        'name': 'AliExpress',
        'url_template': 'https://www.aliexpress.com/wholesale?SearchText={query}',
        'site_domain': '',
        'category_tags': json.dumps(['anything', 'all']),
        'item_types': json.dumps(['all']),
        'geo': 'ALL',
        'tier': 'marketplace',
    },
    {
        'key': 'alibaba',
        'name': 'Alibaba',
        'url_template': 'https://www.alibaba.com/trade/search?SearchText={query}',
        'site_domain': '',
        'category_tags': json.dumps(['anything', 'all']),
        'item_types': json.dumps(['all']),
        'geo': 'ALL',
        'tier': 'marketplace',
    },
    # ── Новые источники (19.05.2026) ──
    {
        'key': 'megamarket',
        'name': 'Megamarket',
        'url_template': '',
        'site_domain': 'megamarket.ru',
        'category_tags': json.dumps(['anything', 'all']),
        'item_types': json.dumps(['all']),
        'geo': 'RU',
        'tier': 'marketplace',
    },
    {
        'key': 'price_ru',
        'name': 'Price.ru',
        'url_template': '',
        'site_domain': 'price.ru',
        'category_tags': json.dumps(['техника', 'мебель', 'интерьер', 'косметика', 'одежда']),
        'item_types': json.dumps(['all']),
        'geo': 'RU',
        'tier': 'aggregator',
    },
    {
        'key': 'goods_ru',
        'name': 'Goods.ru',
        'url_template': '',
        'site_domain': 'goods.ru',
        'category_tags': json.dumps(['техника', 'мебель', 'интерьер']),
        'item_types': json.dumps(['all']),
        'geo': 'RU',
        'tier': 'aggregator',
    },
    {
        'key': 'ekatalog',
        'name': 'E-Katalog',
        'url_template': '',
        'site_domain': 'ekatalog.ru',
        'category_tags': json.dumps(['техника']),
        'item_types': json.dumps(['electronics', 'computers', 'kitchen_appliances', 'audio']),
        'geo': 'RU',
        'tier': 'aggregator',
    },
]

# -----------------------------------------------------------------------
# Helper: определение item_type по Vision-атрибутам
# -----------------------------------------------------------------------

_ITEM_TYPE_RULES: list[tuple[list[str], str]] = [
    (['luxury', 'designer', 'premium', 'haute'], 'luxury_clothing'),
    (['streetwear', 'oversize', 'casual', 'baggy'], 'streetwear'),
    (['formal', 'business', 'elegant', 'office'], 'formal_wear'),
    (['sport', 'sporty', 'athletic', 'running', 'training'], 'sportswear'),
    (['gym', 'fitness', 'workout'], 'fitness'),
    (['cycling', 'bicycle', 'bike', 'velo'], 'cycling'),
    (['footwear', 'sneakers', 'boots', 'shoes'], 'footwear'),
    (['accessories', 'jewelry', 'watch', 'bag'], 'accessories'),
    (['electronics', 'gadget', 'digital'], 'electronics'),
    (['computer', 'laptop', 'gaming'], 'computers'),
    (['audio', 'headphones', 'speaker', 'microphone'], 'audio'),
    (['smartphone', 'phone', 'tablet'], 'smartphones'),
    (['kitchen', 'appliance', 'fridge', 'microwave'], 'kitchen_appliances'),
    (['furniture', 'sofa', 'chair', 'table', 'bed', 'wardrobe'], 'furniture'),
    (['lighting', 'lamp', 'chandelier', 'pendant'], 'lighting'),
    (['decor', 'vase', 'carpet', 'pillow', 'mirror'], 'home_decor'),
    (['cosmetics', 'makeup', 'skincare'], 'cosmetics'),
    (['skincare', 'cream', 'serum', 'moisturizer'], 'skincare'),
    (['perfume', 'fragrance', 'scent'], 'perfume'),
    (['makeup', 'lipstick', 'foundation', 'eyeshadow'], 'makeup'),
    (['fitness', 'exercise', 'yoga', 'dumbbell'], 'fitness'),
    (['sports', 'equipment', 'gear'], 'sports_equipment'),
    (['book', 'literature'], 'books'),
    (['toy', 'game', 'collectible'], 'toys'),
    (['pet', 'animal', 'dog', 'cat'], 'pet_supplies'),
    (['auto', 'car', 'vehicle', 'spare'], 'auto_parts'),
    (['tool', 'hardware', 'fixing'], 'hardware'),
]

_BRAND_LUXURY = frozenset({
    'gucci', 'prada', 'louis vuitton', 'dior', 'chanel', 'hermes',
    'valentino', 'versace', 'givenchy', 'yves saint laurent', 'ysl',
    'balenciaga', 'fendi', 'burberry', 'brioni', 'zegna',
    'bottega veneta', 'saint laurent', 'celine', 'loewe',
    'cartier', 'van cleef', 'tiffany', 'bvlgari',
})


def infer_item_type(attrs: dict[str, Any]) -> str:
    """Определяет тип товара по Vision-атрибутам.

    Возвращает строку item_type.
    """
    brand = (attrs.get('brand') or '').strip().lower()
    subcategory = (attrs.get('subcategory') or '').strip().lower()
    category = (attrs.get('category') or '').strip().lower()
    style = [s.lower() for s in (attrs.get('style') or [])]
    material = (attrs.get('material') or '').strip().lower()
    fit = (attrs.get('fit') or '').strip().lower()
    gender = (attrs.get('gender') or '').strip().lower()

    # Luxury brand → luxury_clothing (если одежда/обувь/аксессуары)
    if brand in _BRAND_LUXURY and category in ('одежда', 'обувь',
                                                'аксессуары'):
        return 'luxury_clothing'

    # По стилям
    all_tags = set(style)
    all_tags.add(subcategory)
    all_tags.add(material)

    for keywords, item_type in _ITEM_TYPE_RULES:
        if any(k in subcategory or k in all_tags for k in keywords):
            return item_type

    # Если обувь без уточнения
    if category == 'обувь':
        return 'footwear'

    # Для остального возвращаем category как тип
    cat_map = {
        'одежда': 'streetwear',
        'косметика': 'cosmetics',
        'техника': 'electronics',
        'мебель': 'furniture',
        'интерьер': 'home_decor',
        'аксессуары': 'accessories',
        'еда': 'grocery',
    }
    return cat_map.get(category, 'other')


def _get_default_conn() -> sqlite3.Connection:
    return sqlite3.connect(_DEFAULT_DB)


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def ensure_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    conn = conn or _get_default_conn()
    """Создаёт таблицы search_sources и source_clicks, если их нет."""
    conn.executescript(SEARCH_SOURCES_DDL)
    conn.commit()


def seed_sources(conn: Optional[sqlite3.Connection] = None) -> None:
    """Заполняет базу источников, если пуста."""
    conn = conn or _get_default_conn()
    ensure_schema(conn)
    count = conn.execute("SELECT COUNT(*) FROM search_sources").fetchone()[0]
    if count > 0:
        return  # уже есть данные

    for src in SEED_SOURCES:
        conn.execute(
            """INSERT OR IGNORE INTO search_sources
               (key, name, url_template, site_domain, category_tags,
                item_types, geo, tier, score, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            (src['key'], src['name'], src.get('url_template', ''),
             src.get('site_domain', ''),
             src.get('category_tags', '[]'),
             src.get('item_types', '[]'),
             src.get('geo', 'RU'),
             src.get('tier', 'aggregator'),
             src.get('score', 1.0)),
        )
    conn.commit()
    log.info("ml_source_matcher: seeded %d sources", len(SEED_SOURCES))


def get_sources(
    attrs: dict[str, Any],
    *,
    conn: Optional[sqlite3.Connection] = None,
    geo: str = 'RU',
    top_n: int = 12,
    min_score: float = 0.3,
) -> list[dict[str, Any]]:
    conn = conn or _get_default_conn()
    """Подбирает источники по типу товара с учётом истории кликов.

    Args:
        conn: Подключение к consumption.db
        attrs: Vision-атрибуты товара
        geo: Регион пользователя
        top_n: Сколько источников вернуть
        min_score: Минимальный скор для включения

    Returns:
        Список источников, отсортированный по приоритету:
        manufacturer > distributor > aggregator > marketplace,
        внутри — по score
    """
    item_type = infer_item_type(attrs)

    # Определяем категорию для fallback-фильтра
    category = (attrs.get('category') or '').strip().lower()

    # Выбираем активные источники, подходящие по item_type
    rows = conn.execute(
        """SELECT key, name, url_template, site_domain, category_tags,
                  item_types, geo, tier, score
           FROM search_sources
           WHERE is_active = 1 AND score >= ?
           ORDER BY
               CASE tier
                   WHEN 'manufacturer' THEN 0
                   WHEN 'distributor' THEN 1
                   WHEN 'aggregator' THEN 2
                   WHEN 'marketplace' THEN 3
                   ELSE 4
               END,
               score DESC""",
        (min_score,),
    ).fetchall()

    tier_order = {
        'manufacturer': 0, 'distributor': 1,
        'aggregator': 2, 'marketplace': 3,
    }

    scored: list[tuple[int, float, dict]] = []

    for row in rows:
        key, name, url_template, site_domain, cat_tags_raw, \
            item_types_raw, src_geo, tier, score = row

        # Проверка гео
        src_geo = (src_geo or 'RU').upper()
        if src_geo not in ('ALL', geo):
            continue

        # Проверка item_type
        try:
            item_types = json.loads(item_types_raw) if item_types_raw else []
        except (json.JSONDecodeError, TypeError):
            item_types = []

        if 'all' not in item_types:
            if item_type not in item_types:
                continue

        tier_priority = tier_order.get(tier, 4)
        scored.append((tier_priority, -score if item_types == ['all'] else score, {
            'key': key,
            'name': name,
            'url_template': url_template,
            'site_domain': site_domain,
            'tier': tier,
            'score': score,
        }))

    # Сортируем: сначала tier, потом score
    scored.sort(key=lambda x: (x[0], -x[1]))
    return [item for _, _, item in scored[:top_n]]


def record_click(
    conn: sqlite3.Connection,
    source_key: str,
    attrs: dict[str, Any],
) -> None:
    """Увеличивает score источника для данного типа товара при клике."""
    item_type = infer_item_type(attrs)
    conn.execute(
        "INSERT INTO source_clicks (source_key, item_type, action) VALUES (?,?,?)",
        (source_key, item_type, 'click'),
    )
    conn.execute(
        """UPDATE search_sources SET score = score + 0.1,
               updated_at = datetime('now')
           WHERE key = ? AND is_active = 1""",
        (source_key,),
    )
    conn.commit()
    log.info("ml_source_matcher: click on %s for %s (+0.1)",
             source_key, item_type)


def record_skip(
    conn: sqlite3.Connection,
    source_key: str,
    attrs: dict[str, Any],
) -> None:
    """Понижает score источника для данного типа товара при пропуске."""
    item_type = infer_item_type(attrs)
    conn.execute(
        "INSERT INTO source_clicks (source_key, item_type, action) VALUES (?,?,?)",
        (source_key, item_type, 'skip'),
    )
    conn.execute(
        """UPDATE search_sources SET score = score - 0.05,
               updated_at = datetime('now')
           WHERE key = ? AND is_active = 1 AND score > 0.2""",
        (source_key,),
    )
    conn.commit()
    log.info("ml_source_matcher: skip on %s for %s (-0.05)",
             source_key, item_type)


def list_sources(
    conn: sqlite3.Connection,
    *,
    tier: str | None = None,
    geo: str | None = None,
    is_active: bool = True,
) -> list[dict[str, Any]]:
    """Возвращает список источников с фильтрами."""
    conditions = ['1=1']
    params: list[Any] = []
    if tier:
        conditions.append('tier = ?')
        params.append(tier)
    if geo:
        conditions.append("(geo = 'ALL' OR geo = ?)")
        params.append(geo.upper())
    if is_active is not None:
        conditions.append('is_active = ?')
        params.append(1 if is_active else 0)

    rows = conn.execute(
        f"""SELECT key, name, url_template, site_domain,
                   category_tags, item_types, geo, tier, score, is_active
            FROM search_sources
            WHERE {' AND '.join(conditions)}
            ORDER BY
                CASE tier
                    WHEN 'manufacturer' THEN 0
                    WHEN 'distributor' THEN 1
                    WHEN 'aggregator' THEN 2
                    WHEN 'marketplace' THEN 3
                    ELSE 4
                END,
                score DESC""",
        params,
    ).fetchall()

    result = []
    for r in rows:
        result.append({
            'key': r[0],
            'name': r[1],
            'url_template': r[2],
            'site_domain': r[3],
            'category_tags': json.loads(r[4]) if r[4] else [],
            'item_types': json.loads(r[5]) if r[5] else [],
            'geo': r[6],
            'tier': r[7],
            'score': r[8],
            'is_active': bool(r[9]),
        })
    return result


def add_source(
    conn: sqlite3.Connection,
    key: str,
    name: str,
    *,
    url_template: str = '',
    site_domain: str = '',
    category_tags: list[str] | None = None,
    item_types: list[str] | None = None,
    geo: str = 'RU',
    tier: str = 'aggregator',
) -> None:
    """Добавляет источник в базу."""
    ensure_schema(conn)
    conn.execute(
        """INSERT OR REPLACE INTO search_sources
           (key, name, url_template, site_domain, category_tags,
            item_types, geo, tier, score, is_active)
           VALUES (?,?,?,?,?,?,?,?,1.0,1)""",
        (key, name, url_template, site_domain,
         json.dumps(category_tags or []),
         json.dumps(item_types or ['all']),
         geo.upper(), tier),
    )
    conn.commit()
    log.info("ml_source_matcher: added source %s (%s, %s)", key, tier, geo)