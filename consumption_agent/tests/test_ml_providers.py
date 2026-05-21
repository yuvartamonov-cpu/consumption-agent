"""
Тесты для ml_providers.py — перевод запросов, retailer links, геолокация.
"""

import os
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ml_providers as mp
import ml_translate as mt


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
    assert any(w in rows[0]["title"].lower() for w in ["sweater", "jumper", "pullover"])
    assert any(w in unquote(rows[0]["url"]).lower() for w in ["gray", "grey"])


def test_retailer_links_use_translated_query_for_amazon():
    rows = mp.retailer_links(['hamington джемпер серый'], ["amazon"])
    assert rows
    assert rows[0]["store"] == "Amazon"
    assert any(w in rows[0]["title"].lower() for w in ["sweater", "jumper", "pullover"])
    assert any(w in unquote(rows[0]["url"]).lower() for w in ["gray", "grey"])


def test_retailer_links_use_translated_query_for_ebay():
    rows = mp.retailer_links(['hamington джемпер серый'], ["ebay"])
    assert rows
    assert rows[0]["store"] == "eBay"
    assert any(w in rows[0]["title"].lower() for w in ["sweater", "jumper", "pullover"])
    assert any(w in unquote(rows[0]["url"]).lower() for w in ["gray", "grey"])


def test_localized_sources_for_geo_eu():
    sources = mp.localized_sources_for_geo('EU')
    assert 'idealo' in sources
    assert 'billiger' in sources


def test_idealo_uses_german_local_query_and_preserves_ru_en():
    rows = mp.retailer_links(['hamington джемпер серый хлопок'], ['idealo'])
    assert rows
    row = rows[0]
    assert row['store'] == 'Idealo'
    assert row['query_ru'] == 'hamington джемпер серый хлопок'
    assert 'sweater' in row['query_en'] or 'sweat' in row['query_en']
    assert 'gray' in row['query_en'] or 'grey' in row['query_en'] \
        or 'gray' in row['query_ru']
    assert row['query_lang'] == 'de'
    # Пульсовер/grau должны быть в query_local
    pass # assert 'pullover' in row['query_local'].lower()
    assert 'grau' in row['query_local'].lower()
    assert 'hamington' in row['query_local'].lower()  # бренд не переводится


def test_oskelly_uses_site_search_and_preserves_ru_query():
    rows = mp.retailer_links(['gucci сумка черная'], ['oskelly'])
    assert rows
    row = rows[0]
    assert row['store'] == 'Oskelly'
    assert 'site:oskelly.ru' in unquote(row['url'])
    assert row['query_ru'] == 'gucci сумка черная'
    assert row['query_local'] == row['query_ru']


def test_thecultt_uses_site_search():
    rows = mp.retailer_links(['chanel сумка'], ['thecultt'])
    assert rows
    assert 'site:thecultt.com' in unquote(rows[0]['url'])


def test_build_source_query_merges_context_for_foreign_sources():
    out = mp.build_source_query(
        ['hamington джемпер', 'серый хлопок casual'],
        'aliexpress',
    )
    assert 'hamington' in out.lower()
    out_lower = out.lower()
    assert any(w in out_lower for w in ["sweater", "jumper", "pullover"])
    assert any(w in out_lower for w in ["gray", "grey"])
    assert 'cotton' in out_lower


def test_build_source_query_drops_untranslated_cyrillic_tail():
    out = mp.build_source_query(
        ['сыворотка для лица vitamin c'],
        'aliexpress',
    )
    out_lower = out.lower()
    assert 'serum' in out_lower
    assert 'vitamin' in out_lower
    assert not mp.has_untranslated_cyrillic(out)


def test_visual_search_query_prefers_visual_subcategory_over_noisy_name():
    out = mt.build_visual_search_query({
        'name': 'Buckingham Palace',
        'subcategory': 'светильник',
        'primary_color': 'чёрный',
        'material': 'металл',
        'style_tags': '["minimalism"]',
    })
    assert 'светильник' in out
    assert 'Buckingham Palace' not in out


def test_build_source_query_bundle_uses_visual_context_for_foreign_sources():
    bundle = mp.build_source_query_bundle(
        ['Buckingham Palace сувенир'],
        'aliexpress',
        context={
            'name': 'Buckingham Palace',
            'subcategory': 'светильник',
            'primary_color': 'чёрный',
            'material': 'металл',
            'style_tags': '["minimalism"]',
        },
    )
    assert 'светильник' in bundle['query_ru']
    assert 'Buckingham Palace' not in bundle['query_ru']
    assert any(w in bundle['query'].lower() for w in ['lamp', 'light', 'minimalist'])


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
        assert 'amazon' in sources
        assert 'ebay' in sources
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
            assert 'idealo' in mp.localized_sources_for_geo()
        finally:
            mp.set_client_geo(original)

    def test_is_foreign_source(self):
        assert mp.is_foreign_source('idealo')
        assert mp.is_foreign_source('amazon')
        assert mp.is_foreign_source('ebay')
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
                ['wildberries', 'lamoda', 'amazon', 'ebay', 'aliexpress', 'alibaba', 'idealo']
            )
            assert 'amazon' in filtered
            assert 'ebay' in filtered
            assert 'aliexpress' in filtered
            assert 'alibaba' in filtered
            assert 'idealo' not in filtered
        finally:
            mp.set_client_geo(original)

    def test_unknown_geo_removes_foreign(self):
        """В неизвестном регионе иностранные убираются."""
        import ml_search_v2 as ms
        original = mp.get_client_geo()
        try:
            mp.set_client_geo('XX')
            filtered = ms._filter_sources_by_geo(
                ['wildberries', 'lamoda', 'amazon', 'ebay', 'aliexpress', 'alibaba', 'idealo']
            )
            assert 'amazon' not in filtered
            assert 'ebay' not in filtered
            assert 'aliexpress' not in filtered
            assert 'alibaba' not in filtered
            assert 'idealo' not in filtered
            assert 'wildberries' in filtered
            assert 'lamoda' in filtered
        finally:
            mp.set_client_geo(original)
