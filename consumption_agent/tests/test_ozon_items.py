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


def test_flat_text_fallback_when_no_useful_tables():
    items = _parse_ozon_items(FLAT_TEXT_RECEIPT)
    names = [i['name'] for i in items]
    assert names == ['Кофе зерновой Lavazza', 'Чайник электрический Bosch']
    assert items[0]['qty'] == 1 and items[0]['price'] == 899.0
    assert items[1]['qty'] == 2 and items[1]['price'] == 2499.0
    assert not any('Доставка' in n or 'Итого' in n for n in names)


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
