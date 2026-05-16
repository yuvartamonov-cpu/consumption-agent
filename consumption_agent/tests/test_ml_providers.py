import os
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ml_providers as mp


def test_translate_query_for_foreign_source():
    out = mp.translate_query_for_source('"hamington" джемпер серый', "aliexpress")
    assert 'sweater' in out
    assert 'gray' in out
    assert 'hamington' in out


def test_translate_query_keeps_local_source_untouched():
    query = '"hamington" джемпер серый'
    assert mp.translate_query_for_source(query, "lamoda") == query


def test_retailer_links_use_translated_query_for_aliexpress():
    rows = mp.retailer_links(['"hamington" джемпер серый'], ["aliexpress"])
    assert rows
    assert rows[0]["store"] == "AliExpress"
    assert "sweater" in rows[0]["title"]
    assert "gray" in unquote(rows[0]["url"])


def test_retailer_links_keep_russian_query_for_lamoda():
    rows = mp.retailer_links(['"hamington" джемпер серый'], ["lamoda"])
    assert rows
    assert rows[0]["store"] == "Lamoda"
    assert "джемпер" in rows[0]["title"]
