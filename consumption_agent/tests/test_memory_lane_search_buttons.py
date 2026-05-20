"""Tests for Memory Lane /ml_search result buttons."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("telegram")

from bot.handlers.memory_lane import (
    _build_ml_search_keyboard,
    _format_top_link_button,
)


def test_format_top_link_button_uses_url_and_price():
    button = _format_top_link_button({
        'url': 'https://example.com/item',
        'store': 'Lamoda',
        'price_min': 12990,
    }, 1)
    assert button is not None
    assert button.url == 'https://example.com/item'
    assert 'Lamoda' in button.text
    assert '12 990' in button.text


def test_build_ml_search_keyboard_adds_top_links_page_and_watch():
    keyboard = _build_ml_search_keyboard(
        {
            'canonical_groups': [
                {'url': 'https://example.com/1', 'store': 'Lamoda', 'price_min': 1000},
                {'url': 'https://example.com/2', 'store': 'WB', 'price_min': 900},
                {'url': 'https://example.com/3', 'store': 'Ozon', 'price_min': 1100},
            ]
        },
        42,
        remaining_pages=2,
    )
    assert keyboard is not None
    rows = keyboard.inline_keyboard
    assert len(rows) == 5
    assert rows[0][0].url == 'https://example.com/1'
    assert rows[1][0].url == 'https://example.com/2'
    assert rows[2][0].url == 'https://example.com/3'
    assert rows[3][0].callback_data == 'ml_page:42:1'
    assert rows[4][0].callback_data == 'ml_watch:42'
