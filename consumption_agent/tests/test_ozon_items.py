"""Tests for _parse_ozon_items in consumption_agent_full_030526.py.

Covers the two parsing strategies (table-based + flat-text fallback) and
the most common false-positive — shipping/total lines bleeding into the
items list.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from consumption_agent_full_030526 import _parse_ozon_items


# ---------------------------------------------------------------------------
# Fixtures — minimal HTML approximations of what Ozon actually sends.
# ---------------------------------------------------------------------------
TABLE_RECEIPT = """
<html><body>
<table>
  <tr><th>Товар</th><th>Цена</th></tr>
  <tr><td>Гречка ядрица 900 г</td><td>89,90 ₽</td></tr>
  <tr><td>Шампунь Head &amp; Shoulders 400 мл</td><td>1 234,00 ₽</td></tr>
  <tr><td>Доставка курьером</td><td>199,00 ₽</td></tr>
  <tr><td>Итого</td><td>1 522,90 ₽</td></tr>
</table>
</body></html>
"""

TABLE_WITH_QTY = """
<html><body>
<table>
  <tr><td>Молоко Простоквашино 1л</td><td>2 x 89,99</td><td>179,98 ₽</td></tr>
</table>
</body></html>
"""

FLAT_TEXT_RECEIPT = """
<html><body>
<div>Ваш чек Ozon</div>
<div>Кофе зерновой Lavazza</div>
<div>1 x 899,00</div>
<div>Чайник электрический Bosch</div>
<div>2 x 2 499,00</div>
<div>Доставка</div>
<div>0,00</div>
<div>Итого 5 897,00 ₽</div>
</body></html>
"""

EMPTY_HTML = "<html><body><p>Здравствуйте!</p></body></html>"

PROMO_HTML = """
<html><body>
<table>
  <tr><td>Скидка 10%</td><td>-100,00 ₽</td></tr>
  <tr><td>Итого со скидкой</td><td>900,00 ₽</td></tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------


def test_parses_table_with_two_real_items_and_skips_shipping_and_total():
    items = _parse_ozon_items(TABLE_RECEIPT)
    names = [i['name'] for i in items]
    assert names == ['Гречка ядрица 900 г', 'Шампунь Head & Shoulders 400 мл']
    assert items[0]['price'] == 89.90
    assert items[1]['price'] == 1234.00
    for it in items:
        assert it['qty'] == 1
        assert it['unit'] == 'шт'
        assert it['total'] == round(it['price'] * it['qty'], 2)


def test_parses_quantity_from_n_x_price_pattern():
    items = _parse_ozon_items(TABLE_WITH_QTY)
    assert len(items) == 1
    assert items[0]['qty'] == 2
    assert items[0]['price'] == 89.99
    assert items[0]['total'] == 179.98


def test_flat_text_emails_intentionally_return_empty():
    """Ozon ships HTML tables; flat-text emails are not parsed to avoid
    promoting greetings like «Спасибо за заказ» into items. We accept
    silent failure (= 0 items) over noisy false positives."""
    items = _parse_ozon_items(FLAT_TEXT_RECEIPT)
    assert items == []


def test_full_name_preferred_over_short_brand_cell():
    """When a row has both a short brand cell AND a long product-name cell
    before the price, the parser must pick the full name (the cell closer
    to the price). This guards against picking «Pampers» when «Подгузники
    Pampers Premium 50 шт» is available.
    """
    html = """
    <table>
      <tr>
        <td>Pampers</td>
        <td>Подгузники Pampers Premium Care 50 шт</td>
        <td>1 234,00 ₽</td>
      </tr>
    </table>
    """
    items = _parse_ozon_items(html)
    assert len(items) == 1
    assert items[0]['name'] == 'Подгузники Pampers Premium Care 50 шт'
    assert items[0]['price'] == 1234.00


def test_brand_only_row_known_limitation():
    """Brand-only miscapture (no full product name in the row, only brand
    + price): parser captures the brand. This is a documented limitation
    — cmd_match (matcher.py) is expected to fuzzy-link such items to
    known products on a second pass.

    Test exists to LOCK the current behaviour so any future tightening
    is intentional and surfaces here.
    """
    html = '<table><tr><td>Nestle</td><td>299,00 ₽</td></tr></table>'
    items = _parse_ozon_items(html)
    assert len(items) == 1
    assert items[0]['name'] == 'Nestle'  # known limitation, see docstring


def test_skip_pattern_covers_extended_keywords():
    """SKIP_NAME_RX must skip commission/bonus/refund/tip/promo rows."""
    cases = [
        '<table><tr><td>Комиссия за платёж</td><td>50,00 ₽</td></tr></table>',
        '<table><tr><td>Бонусы списано</td><td>100,00 ₽</td></tr></table>',
        '<table><tr><td>Возврат предоплаты</td><td>200,00 ₽</td></tr></table>',
        '<table><tr><td>Чаевые курьеру</td><td>50,00 ₽</td></tr></table>',
        '<table><tr><td>Промокод OZON10</td><td>-100,00 ₽</td></tr></table>',
    ]
    for html in cases:
        assert _parse_ozon_items(html) == [], f'Should skip: {html}'


def test_empty_or_unrelated_html_returns_empty_list():
    assert _parse_ozon_items(EMPTY_HTML) == []
    assert _parse_ozon_items('') == []
    assert _parse_ozon_items('<html></html>') == []


def test_promo_lines_are_excluded():
    items = _parse_ozon_items(PROMO_HTML)
    assert all('Скидка' not in i['name'] and 'Итого' not in i['name'] for i in items)


def test_no_duplicate_items_in_output():
    dup_html = """
    <table>
      <tr><td>Хлеб Бородинский</td><td>59,00 ₽</td></tr>
      <tr><td>Хлеб Бородинский</td><td>59,00 ₽</td></tr>
    </table>
    """
    items = _parse_ozon_items(dup_html)
    assert len(items) == 1
    assert items[0]['name'] == 'Хлеб Бородинский'
