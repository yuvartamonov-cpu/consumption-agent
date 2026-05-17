"""
Тесты для ml_official_sites.py — resolver официальных сайтов брендов.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ml_official_sites as mos


# ─────────────────────────────────────────────────────────────
# lookup_brand
# ─────────────────────────────────────────────────────────────

class TestLookupBrand:

    def test_exact_match(self):
        """Точное совпадение по ключу."""
        info = mos.lookup_brand('Nike')
        assert info is not None
        assert 'official' in info

    def test_alias_match(self):
        """Поиск по алиасу."""
        info = mos.lookup_brand('NB')
        assert info is not None

    def test_case_insensitive(self):
        info = mos.lookup_brand('SAMSUNG')
        assert info is not None

    def test_unknown_brand_returns_none(self):
        assert mos.lookup_brand('НеизвестныйБренд123') is None

    def test_alias_levis(self):
        info = mos.lookup_brand("Levi's")
        assert info is not None


# ─────────────────────────────────────────────────────────────
# resolve_brand_links
# ─────────────────────────────────────────────────────────────

class TestResolveBrandLinks:

    def test_known_brand_returns_official(self):
        """Известный бренд → хотя бы одна official ссылка."""
        links = mos.resolve_brand_links('Nike', 'кроссовки белые')
        assert len(links) >= 1
        tiers = [l['tier'] for l in links]
        assert 'official' in tiers

    def test_known_brand_has_authorized(self):
        """Nike → есть авторизованные ритейлеры."""
        links = mos.resolve_brand_links('Nike', 'кроссовки')
        tiers = [l['tier'] for l in links]
        assert 'authorized' in tiers

    def test_apple_has_distributor(self):
        """Apple → re:Store как дистрибьютор."""
        links = mos.resolve_brand_links('Apple', 'iPhone 15')
        stores = [l['store'] for l in links]
        assert 're:Store' in stores

    def test_unknown_brand_fallback_to_search(self):
        """Неизвестный бренд → search_fallback ссылки."""
        links = mos.resolve_brand_links('SuperUnknownBrand', 'куртка')
        assert len(links) >= 1
        assert all(l['tier'] == 'search_fallback' for l in links)

    def test_query_encoded_in_url(self):
        """Поисковый запрос кодируется в URL."""
        links = mos.resolve_brand_links('Nike', 'кроссовки белые')
        official = [l for l in links if l['tier'] == 'official']
        assert official
        # URL должен содержать закодированный запрос
        assert '%' in official[0]['url']

    def test_all_links_have_required_fields(self):
        """Все ссылки содержат обязательные поля."""
        links = mos.resolve_brand_links('Adidas', 'кроссовки')
        for link in links:
            assert 'title' in link
            assert 'url' in link
            assert 'store' in link
            assert 'source' in link
            assert 'tier' in link
            assert link['_link_only'] is True

    def test_tier_ordering(self):
        """Порядок: official < distributor < authorized < brand_page."""
        links = mos.resolve_brand_links('Apple', 'MacBook')
        tiers = [l['tier'] for l in links]
        # official должен быть раньше authorized
        if 'official' in tiers and 'authorized' in tiers:
            assert tiers.index('official') < tiers.index('authorized')

    def test_empty_query_still_works(self):
        """Пустой запрос — всё равно возвращаем ссылки."""
        links = mos.resolve_brand_links('Samsung')
        assert len(links) >= 1

    def test_mac_cosmetics_resolved(self):
        """MAC (косметика) в справочнике."""
        links = mos.resolve_brand_links('MAC', 'тональный крем')
        assert len(links) >= 1
        stores = [l['store'] for l in links]
        assert 'Официальный сайт' in stores or 'Золотое Яблоко' in stores


# ─────────────────────────────────────────────────────────────
# sort_by_tier
# ─────────────────────────────────────────────────────────────

class TestSortByTier:

    def test_official_before_fallback(self):
        links = [
            {'tier': 'search_fallback', 'title': 'fallback'},
            {'tier': 'official', 'title': 'official'},
            {'tier': 'authorized', 'title': 'auth'},
        ]
        sorted_links = mos.sort_by_tier(links)
        assert sorted_links[0]['tier'] == 'official'
        assert sorted_links[1]['tier'] == 'authorized'
        assert sorted_links[2]['tier'] == 'search_fallback'


# ─────────────────────────────────────────────────────────────
# Интеграция с ml_providers.brand_site_links
# ─────────────────────────────────────────────────────────────

class TestBrandSiteLinksIntegration:

    def test_brand_source_produces_official_links(self):
        """brand:Nike в sources → official entry points."""
        import ml_providers
        links = ml_providers.brand_site_links(
            ['кроссовки белые'],
            ['brand:Nike', 'wildberries'],
        )
        assert len(links) >= 1
        # Должны быть official/authorized, а не только search_fallback
        tiers = {l.get('tier') for l in links}
        assert 'official' in tiers or 'authorized' in tiers

    def test_unknown_brand_produces_fallback(self):
        """Неизвестный бренд → fallback ссылки."""
        import ml_providers
        links = ml_providers.brand_site_links(
            ['куртка зимняя'],
            ['brand:UnknownBrandXYZ'],
        )
        assert len(links) >= 1

    def test_no_brand_sources_returns_empty(self):
        """Без brand: sources → пустой список."""
        import ml_providers
        links = ml_providers.brand_site_links(
            ['кроссовки'],
            ['wildberries', 'lamoda'],
        )
        assert links == []
