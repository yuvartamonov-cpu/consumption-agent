"""
Тесты для ml_watchlist.py — price-drop watchlist.
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ml_watchlist as mw


def _make_conn():
    conn = sqlite3.connect(':memory:')
    mw.ensure_watchlist_schema(conn)
    return conn


# ─── Schema ───

class TestSchema:

    def test_creates_tables(self):
        conn = _make_conn()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert 'ml_watchlist' in tables
        assert 'ml_price_history' in tables

    def test_idempotent(self):
        conn = _make_conn()
        # Повторный вызов не падает
        mw.ensure_watchlist_schema(conn)
        mw.ensure_watchlist_schema(conn)


# ─── CRUD ───

class TestAddRemove:

    def test_add_basic(self):
        conn = _make_conn()
        wid = mw.add_to_watchlist(
            conn,
            item_id=1,
            product_url='https://wb.ru/catalog/123/detail.aspx',
            product_title='Кроссовки Nike',
            store='Wildberries',
            initial_price=5000,
        )
        assert wid > 0
        rec = mw.get_watch(conn, wid)
        assert rec['item_id'] == 1
        assert rec['initial_price'] == 5000
        assert rec['status'] == 'active'

    def test_add_duplicate_returns_same_id(self):
        conn = _make_conn()
        url = 'https://wb.ru/catalog/123/detail.aspx'
        wid1 = mw.add_to_watchlist(conn, item_id=1, product_url=url, initial_price=5000)
        wid2 = mw.add_to_watchlist(conn, item_id=1, product_url=url, initial_price=5000)
        assert wid1 == wid2

    def test_add_reactivates_dismissed(self):
        conn = _make_conn()
        url = 'https://wb.ru/catalog/123/detail.aspx'
        wid = mw.add_to_watchlist(conn, item_id=1, product_url=url, initial_price=5000)
        mw.remove_from_watchlist(conn, wid)
        # Повторное добавление — реактивирует
        wid2 = mw.add_to_watchlist(conn, item_id=1, product_url=url, initial_price=5000)
        assert wid == wid2
        rec = mw.get_watch(conn, wid)
        assert rec['status'] == 'active'

    def test_remove(self):
        conn = _make_conn()
        wid = mw.add_to_watchlist(conn, item_id=1, product_url='u1', initial_price=100)
        assert mw.remove_from_watchlist(conn, wid)
        assert not mw.remove_from_watchlist(conn, wid)  # уже dismissed
        rec = mw.get_watch(conn, wid)
        assert rec['status'] == 'dismissed'

    def test_list_only_active(self):
        conn = _make_conn()
        w1 = mw.add_to_watchlist(conn, item_id=1, product_url='u1', initial_price=100)
        w2 = mw.add_to_watchlist(conn, item_id=2, product_url='u2', initial_price=200)
        mw.remove_from_watchlist(conn, w1)
        active = mw.list_watchlist(conn)
        assert len(active) == 1
        assert active[0]['id'] == w2


# ─── Price logic ───

class TestPriceLogic:

    def test_compute_drop_pct_basic(self):
        assert mw.compute_drop_pct(1000, 800) == 20.0
        assert mw.compute_drop_pct(5000, 4500) == 10.0

    def test_compute_drop_pct_no_change(self):
        assert mw.compute_drop_pct(1000, 1000) == 0.0

    def test_compute_drop_pct_increase(self):
        """Рост цены — drop = 0.0."""
        assert mw.compute_drop_pct(1000, 1200) == 0.0

    def test_compute_drop_pct_invalid(self):
        assert mw.compute_drop_pct(None, 100) is None
        assert mw.compute_drop_pct(100, None) is None
        assert mw.compute_drop_pct(0, 100) is None

    def test_record_price_check(self):
        conn = _make_conn()
        wid = mw.add_to_watchlist(conn, item_id=1, product_url='u', initial_price=1000)
        mw.record_price_check(conn, wid, 900, dropped_pct=10.0)
        rec = mw.get_watch(conn, wid)
        assert rec['last_price'] == 900
        assert rec['last_checked_at'] is not None

        hist = conn.execute(
            'SELECT price, dropped_pct FROM ml_price_history WHERE watch_id = ?',
            (wid,)
        ).fetchall()
        assert len(hist) == 1
        assert hist[0][0] == 900
        assert hist[0][1] == 10.0


# ─── check_price_drops ───

class TestCheckPriceDrops:

    def test_no_watches_no_drops(self):
        conn = _make_conn()

        async def fake_fetcher(url):
            return None

        drops = asyncio.run(mw.check_price_drops(conn, price_fetcher=fake_fetcher))
        assert drops == []

    def test_detects_drop(self):
        conn = _make_conn()
        wid = mw.add_to_watchlist(
            conn, item_id=1, product_url='u', initial_price=1000,
            threshold_pct=10.0,
        )

        async def fake_fetcher(url):
            return 800  # упало на 20%

        drops = asyncio.run(mw.check_price_drops(conn, price_fetcher=fake_fetcher))
        assert len(drops) == 1
        assert drops[0]['watch_id'] == wid
        assert drops[0]['new_price'] == 800
        assert drops[0]['old_price'] == 1000
        assert drops[0]['dropped_pct'] == 20.0

    def test_ignores_small_drops(self):
        conn = _make_conn()
        mw.add_to_watchlist(
            conn, item_id=1, product_url='u', initial_price=1000,
            threshold_pct=10.0,
        )

        async def fake_fetcher(url):
            return 950  # упало на 5% — ниже порога

        drops = asyncio.run(mw.check_price_drops(conn, price_fetcher=fake_fetcher))
        assert drops == []

    def test_fetcher_error_continues(self):
        conn = _make_conn()
        mw.add_to_watchlist(conn, item_id=1, product_url='u1', initial_price=1000)
        mw.add_to_watchlist(conn, item_id=2, product_url='u2', initial_price=2000)

        async def flaky_fetcher(url):
            if 'u1' in url:
                raise RuntimeError('boom')
            return 1500  # 25% drop для u2

        drops = asyncio.run(mw.check_price_drops(conn, price_fetcher=flaky_fetcher))
        assert len(drops) == 1
        assert drops[0]['new_price'] == 1500


# ─── Notification formatting ───

class TestNotificationFormat:

    def test_format_drop_message(self):
        drop = {
            'watch_id': 1,
            'item_id': 5,
            'title': 'Nike Air Force 1',
            'store': 'Wildberries',
            'url': 'https://wb.ru/catalog/123/detail.aspx',
            'old_price': 10000,
            'new_price': 7500,
            'dropped_pct': 25.0,
            'threshold_pct': 10.0,
        }
        text = mw.format_drop_notification(drop)
        assert '25.0%' in text
        assert 'Nike Air Force 1' in text
        assert '10 000' in text or '10000' in text.replace(' ', '')
        assert '7 500' in text or '7500' in text.replace(' ', '')
        assert 'Wildberries' in text

    def test_format_escapes_html(self):
        drop = {
            'title': '<script>alert(1)</script>',
            'store': '<b>Magazin</b>',
            'url': 'https://example.com',
            'old_price': 100, 'new_price': 80,
            'dropped_pct': 20.0, 'threshold_pct': 10.0,
        }
        text = mw.format_drop_notification(drop)
        assert '<script>' not in text
        assert '&lt;script&gt;' in text


# ─── mark_notified / reactivate ───

class TestStatusTransitions:

    def test_mark_notified_changes_status(self):
        conn = _make_conn()
        wid = mw.add_to_watchlist(conn, item_id=1, product_url='u', initial_price=100)
        mw.mark_notified(conn, wid)
        rec = mw.get_watch(conn, wid)
        assert rec['status'] == 'notified'
        assert rec['notified_at'] is not None

    def test_reactivate(self):
        conn = _make_conn()
        wid = mw.add_to_watchlist(conn, item_id=1, product_url='u', initial_price=100)
        mw.mark_notified(conn, wid)
        mw.reactivate_watch(conn, wid)
        rec = mw.get_watch(conn, wid)
        assert rec['status'] == 'active'
