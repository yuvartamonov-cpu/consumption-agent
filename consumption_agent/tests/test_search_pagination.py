"""
Тесты для пагинации результатов /ml_search.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ml_search_v2 as ms


def _make_result(n_groups: int) -> dict:
    """Создаёт фейковый результат с n_groups элементами."""
    groups = []
    for i in range(n_groups):
        groups.append({
            'title': f'Товар {i+1} с длинным названием для проверки',
            'url': f'https://example.com/product/{i+1}',
            'price_min': 1000 + i * 100,
            'price_max': 1500 + i * 100,
            'store': f'Магазин {i+1}',
            'sources_count': 1,
        })
    return {
        'item_id': 1,
        'attributes': {
            'subcategory': 'кроссовки',
            'brand': 'Nike',
            'primary_color': 'белый',
            'material': 'кожа',
        },
        'canonical_groups': groups,
        'summary': {'groups': n_groups, 'total_listings': n_groups * 2},
        'errors': [],
        'collision_warning': None,
    }


class TestFormatSearchPages:

    def test_empty_result_one_page(self):
        result = _make_result(0)
        pages = ms.format_search_pages(result)
        assert len(pages) == 1
        assert 'Ничего не нашёл' in pages[0]

    def test_small_result_one_page(self):
        result = _make_result(3)
        pages = ms.format_search_pages(result, groups_per_page=5)
        assert len(pages) == 1
        assert 'Товар 1' in pages[0]
        assert 'Товар 3' in pages[0]

    def test_exact_fit_one_page(self):
        result = _make_result(5)
        pages = ms.format_search_pages(result, groups_per_page=5)
        assert len(pages) == 1

    def test_overflow_creates_second_page(self):
        result = _make_result(8)
        pages = ms.format_search_pages(result, groups_per_page=5)
        assert len(pages) == 2
        assert 'Товар 1' in pages[0]
        assert 'Продолжение' in pages[1]

    def test_three_pages(self):
        result = _make_result(12)
        pages = ms.format_search_pages(result, groups_per_page=5)
        assert len(pages) == 3

    def test_pages_within_char_limit(self):
        result = _make_result(20)
        pages = ms.format_search_pages(result, groups_per_page=5,
                                       char_limit=ms.TG_MESSAGE_LIMIT)
        for page in pages:
            assert len(page) <= ms.TG_MESSAGE_LIMIT

    def test_numbering_continuous(self):
        """Нумерация товаров сквозная (1-10, не 1-5 + 1-5)."""
        result = _make_result(8)
        pages = ms.format_search_pages(result, groups_per_page=5)
        assert '<b>6.</b>' in pages[1]

    def test_remaining_count_in_footer(self):
        """Первая страница показывает сколько ещё вариантов."""
        result = _make_result(10)
        pages = ms.format_search_pages(result, groups_per_page=5)
        assert 'ещё' in pages[0]


class TestFormatBackwardCompat:

    def test_format_search_result_returns_string(self):
        """Старый API возвращает строку (первую страницу)."""
        result = _make_result(3)
        text = ms.format_search_result_telegram(result)
        assert isinstance(text, str)
        assert 'Товар 1' in text

    def test_format_header(self):
        result = _make_result(5)
        header = ms._format_header(result)
        assert 'кроссовки' in header
        assert 'Nike' in header

    def test_format_group(self):
        g = {
            'title': 'Тестовый товар',
            'url': 'https://example.com/1',
            'price_min': 5000,
            'price_max': 5000,
            'store': 'TestStore',
            'sources_count': 1,
        }
        text = ms._format_group(1, g)
        assert 'Тестовый товар' in text
        assert 'TestStore' in text
        assert '5 000 ₽' in text
