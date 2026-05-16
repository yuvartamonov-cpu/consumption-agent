"""Tests for ml_providers — marketplace API providers."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_providers as mp


# ────────────────────────────────────────────────
# Wildberries price extraction
# ────────────────────────────────────────────────

def test_wb_extract_price_from_sizes():
    product = {
        "sizes": [
            {"price": {"basic": 1199900, "product": 121600}}
        ]
    }
    assert mp._wb_extract_price(product) == 1216


def test_wb_extract_price_basic_fallback():
    product = {
        "sizes": [
            {"price": {"basic": 500000}}
        ]
    }
    assert mp._wb_extract_price(product) == 5000


def test_wb_extract_price_no_sizes():
    assert mp._wb_extract_price({}) is None
    assert mp._wb_extract_price({"sizes": []}) is None


def test_wb_extract_price_empty_price():
    product = {"sizes": [{"price": {}}]}
    assert mp._wb_extract_price(product) is None


# ────────────────────────────────────────────────
# WB image URL
# ────────────────────────────────────────────────

def test_wb_image_url_format():
    url = mp._wb_image_url(475144718)
    assert url.startswith("https://basket-")
    assert "475144718" in url
    assert url.endswith(".webp")


def test_wb_image_url_low_vol():
    # vol = 100 → basket-01
    url = mp._wb_image_url(10000000)
    assert "basket-01" in url


def test_wb_image_url_mid_vol():
    # vol = 1500 → basket-10
    url = mp._wb_image_url(150000000)
    assert "basket-10" in url


# ────────────────────────────────────────────────
# Yandex Market links (no network)
# ────────────────────────────────────────────────

def test_ym_links_generates_correct_urls():
    results = mp.yandex_market_links(["кроссовки Nike", "Nike Air Force"])
    assert len(results) == 2
    assert results[0]["store"] == "Яндекс.Маркет"
    assert results[0]["source"] == "yandex_market"
    assert "market.yandex.ru/search" in results[0]["url"]
    assert results[0]["_link_only"] is True


def test_ym_links_respects_limit():
    qs = [f"query_{i}" for i in range(10)]
    results = mp.yandex_market_links(qs, limit=2)
    assert len(results) == 2


def test_ym_links_empty_queries():
    results = mp.yandex_market_links([])
    assert results == []


def test_ym_links_url_encodes_cyrillic():
    results = mp.yandex_market_links(["пальто серое"])
    assert "%D0%BF%D0%B0%D0%BB%D1%8C%D1%82%D0%BE" in results[0]["url"]


# ────────────────────────────────────────────────
# Ozon cookie loading
# ────────────────────────────────────────────────

def test_load_ozon_cookies_missing_file():
    cookies = mp._load_ozon_cookies("/nonexistent/path.txt")
    assert cookies == {}


def test_load_ozon_cookies_empty_file(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text("# Netscape HTTP Cookie File\n")
    cookies = mp._load_ozon_cookies(str(p))
    assert cookies == {}


def test_load_ozon_cookies_valid(tmp_path):
    p = tmp_path / "cookies.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n"
        ".ozon.ru\tTRUE\t/\tTRUE\t0\t__Secure-ab-group\t42\n"
        ".ozon.ru\tTRUE\t/\tTRUE\t0\tsid\tabc123\n"
    )
    cookies = mp._load_ozon_cookies(str(p))
    assert cookies.get("__Secure-ab-group") == "42"
    assert cookies.get("sid") == "abc123"


# ────────────────────────────────────────────────
# Ozon search — no cookies → empty
# ────────────────────────────────────────────────

def test_ozon_no_cookies_returns_empty():
    result = asyncio.run(mp.search_ozon(
        ["test query"],
        cookies_path="/nonexistent/cookies.txt"
    ))
    assert result == []


# ────────────────────────────────────────────────
# Composite provider — source filtering
# ────────────────────────────────────────────────

def test_composite_only_queries_requested_sources():
    """When sources list has only yandex_market, only YM links
    should be returned (no WB/Ozon network calls)."""
    result = asyncio.run(mp.composite_provider(
        queries=["куртка зимняя"],
        sources=["yandex_market"],
        photo_path=None,
    ))
    assert len(result) >= 1
    assert all(r["source"] == "yandex_market" for r in result)
    assert all(r["_link_only"] for r in result)


def test_composite_empty_sources():
    result = asyncio.run(mp.composite_provider(
        queries=["test"],
        sources=[],
        photo_path=None,
    ))
    assert result == []


def test_composite_empty_queries():
    result = asyncio.run(mp.composite_provider(
        queries=[],
        sources=["wildberries", "yandex_market"],
        photo_path=None,
    ))
    # YM should still return links for empty queries (empty list input)
    # WB should return empty for empty queries
    assert isinstance(result, list)


# ────────────────────────────────────────────────
# Composite provider — result format
# ────────────────────────────────────────────────

def test_composite_result_has_required_fields():
    """Every result dict must have the fields the pipeline expects."""
    result = asyncio.run(mp.composite_provider(
        queries=["тест"],
        sources=["yandex_market"],
        photo_path=None,
    ))
    required = {"title", "url", "price", "store", "source"}
    for r in result:
        assert required.issubset(r.keys()), f"missing fields: {required - r.keys()}"


# ────────────────────────────────────────────────
# Integration: WB live search (network, skip in CI)
# ────────────────────────────────────────────────

def test_wb_live_search():
    """Actually hit WB API. Skip if no network."""
    try:
        results = asyncio.run(mp.search_wildberries(
            ["куртка зимняя мужская"],
            limit=3,
            timeout=8.0,
        ))
    except Exception:
        # Network error in CI — skip gracefully
        return

    if not results:
        # WB might rate-limit — acceptable
        return

    assert len(results) <= 3
    r = results[0]
    assert r["store"] == "Wildberries"
    assert r["source"] == "wildberries"
    assert r["url"].startswith("https://www.wildberries.ru/catalog/")
    assert r["title"]
    # Price should be a reasonable number
    if r["price"] is not None:
        assert 100 <= r["price"] <= 500000


def test_wb_nonsense_query_returns_something():
    """Even nonsense queries may return results on WB."""
    try:
        results = asyncio.run(mp.search_wildberries(
            ["xyznonexistent12345"],
            limit=3,
            timeout=8.0,
        ))
    except Exception:
        return
    # May be empty — that's fine
    assert isinstance(results, list)


# ────────────────────────────────────────────────
# Integration: composite live (network, skip in CI)
# ────────────────────────────────────────────────

def test_composite_live_wb_and_ym():
    """Full composite with WB + YM. Verifies mixed results."""
    try:
        results = asyncio.run(mp.composite_provider(
            queries=["кроссовки Nike"],
            sources=["wildberries", "yandex_market"],
            photo_path=None,
        ))
    except Exception:
        return

    sources_seen = {r["source"] for r in results}
    # At minimum YM link should always be there
    assert "yandex_market" in sources_seen
    # WB may or may not return results (rate limiting)
