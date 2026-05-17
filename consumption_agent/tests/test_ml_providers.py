"""
Тесты для ml_providers.py — перевод запросов, retailer links, геолокация.
"""

import os
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ml_providers as mp


# ─── Базовый перевод ───

def test_translate_query_for_foreign_source():
    out = mp.translate_query_for_source('hamington джемпер серый', "aliexpress")
    assert 'sweater' in out
    assert 'gray' in out
    assert 'hamington' in out


def test_translate_query_keeps_local_source_untouched():
    query = 'hamington джемпер серый'
    assert mp.translate_query_for_source(query, "lamoda") == query


def test_retailer_links_use_translated_query_for_aliexpress():
    rows = mp.retailer_links(['hamington джемпер серый'], ["aliexpress"])
    assert rows
    assert rows[0]["store"] == "AliExpress"
    assert "sweater" in rows[0]["title"]
    assert "gray" in unquote(rows[0]["url"])


def test_retailer_links_keep_russian_query_for_lamoda():
    rows = mp.retailer_links(['hamington джемпер серый'], ["lamoda"])
    assert rows
    assert rows[0]["store"] == "Lamoda"
    assert "джемпер" in rows[0]["title"]


# ─── Расширенный словарь: одежда / обувь / аксессуары ───

class TestTranslationFashion:

    def test_outerwear(self):
        out = mp.translate_query_for_source('пуховик зимний чёрный', 'aliexpress')
        assert 'down jacket' in out
        assert 'winter' in out
        assert 'black' in out

    def test_shoes(self):
        out = mp.translate_query_for_source('лоферы замшевые коричневые', 'alibaba')
        assert 'loafers' in out
        assert 'suede' in out
        assert 'brown' in out

    def test_accessories(self):
        out = mp.translate_query_for_source('кошелёк кожаный мужской', 'aliexpress')
        assert 'wallet' in out
        assert 'leather' in out
        assert 'men' in out

    def test_bottom_wear(self):
        out = mp.translate_query_for_source('джоггеры хлопок серые', 'aliexpress')
        assert 'joggers' in out
        assert 'cotton' in out
        assert 'gray' in out


# ─── Техника / дом / косметика ───

class TestTranslationTechHome:

    def test_tech(self):
        out = mp.translate_query_for_source('клавиатура беспроводная белая', 'alibaba')
        assert 'keyboard' in out
        assert 'white' in out

    def test_furniture(self):
        out = mp.translate_query_for_source('светильник керамический', 'aliexpress')
        assert 'lamp' in out
        assert 'ceramic' in out

    def test_cosmetics(self):
        out = mp.translate_query_for_source('сыворотка для лица', 'aliexpress')
        assert 'serum' in out


# ─── Fit / сезон / пол ───

class TestTranslationAttributes:

    def test_fit(self):
        out = mp.translate_query_for_source('куртка оверсайз', 'aliexpress')
        assert 'jacket' in out
        assert 'oversize' in out

    def test_season(self):
        out = mp.translate_query_for_source('пальто демисезонное', 'aliexpress')
        assert 'coat' in out
        assert 'all-season' in out

    def test_gender(self):
        out = mp.translate_query_for_source('кроссовки женские', 'aliexpress')
        assert 'sneakers' in out
        assert 'women' in out


# ─── Нормализация: служебные слова удаляются ───

class TestTranslationCleanup:

    def test_removes_service_words(self):
        out = mp.translate_query_for_source('кроссовки купить недорого', 'aliexpress')
        assert 'sneakers' in out
        assert 'купить' not in out
        assert 'недорого' not in out

    def test_brand_preserved_in_latin(self):
        """Латинские бренды не трогаем."""
        out = mp.translate_query_for_source('Nike кроссовки белые', 'aliexpress')
        assert 'Nike' in out
        assert 'sneakers' in out
        assert 'white' in out

    def test_cyrillic_detection(self):
        assert mp.has_untranslated_cyrillic('sneakers белые')
        assert not mp.has_untranslated_cyrillic('sneakers white')

    def test_colors_with_yo(self):
        """Цвета с ё переводятся."""
        out = mp.translate_query_for_source('жёлтый зелёный', 'aliexpress')
        assert 'yellow' in out
        assert 'green' in out


# ─── Геолокация ───

class TestGeoLocation:

    def test_default_geo_is_ru(self):
        assert mp.get_client_geo() in ('RU', 'KZ', 'BY', 'EU', 'US')

    def test_foreign_sources_for_ru(self):
        sources = mp.foreign_sources_for_geo('RU')
        assert 'aliexpress' in sources
        assert 'alibaba' in sources

    def test_foreign_sources_for_unknown_region(self):
        sources = mp.foreign_sources_for_geo('XX')
        assert sources == []

    def test_set_and_get_geo(self):
        original = mp.get_client_geo()
        try:
            mp.set_client_geo('EU')
            assert mp.get_client_geo() == 'EU'
            sources = mp.foreign_sources_for_geo()
            assert 'aliexpress' in sources
        finally:
            mp.set_client_geo(original)

    def test_is_foreign_source(self):
        assert mp.is_foreign_source('aliexpress')
        assert mp.is_foreign_source('alibaba')
        assert not mp.is_foreign_source('wildberries')
        assert not mp.is_foreign_source('lamoda')


# ─── Гео-фильтрация в route_sources ───

class TestGeoFilterInRouting:

    def test_ru_includes_aliexpress(self):
        """В РФ aliexpress доступен."""
        import ml_search_v2 as ms
        original = mp.get_client_geo()
        try:
            mp.set_client_geo('RU')
            filtered = ms._filter_sources_by_geo(
                ['wildberries', 'lamoda', 'aliexpress', 'alibaba']
            )
            assert 'aliexpress' in filtered
            assert 'alibaba' in filtered
        finally:
            mp.set_client_geo(original)

    def test_unknown_geo_removes_foreign(self):
        """В неизвестном регионе иностранные убираются."""
        import ml_search_v2 as ms
        original = mp.get_client_geo()
        try:
            mp.set_client_geo('XX')
            filtered = ms._filter_sources_by_geo(
                ['wildberries', 'lamoda', 'aliexpress', 'alibaba']
            )
            assert 'aliexpress' not in filtered
            assert 'alibaba' not in filtered
            assert 'wildberries' in filtered
            assert 'lamoda' in filtered
        finally:
            mp.set_client_geo(original)
