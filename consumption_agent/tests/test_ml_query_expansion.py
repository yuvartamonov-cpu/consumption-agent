"""Tests for ml_query_expansion — query expansion tree."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ml_query_expansion import (
    expand_queries,
    top_query,
    queries_for_source,
    SPECIFICITY_ORDER,
)


# ────────────────────────────────────────────────
# expand_queries
# ────────────────────────────────────────────────

def test_empty_attrs_returns_empty():
    assert expand_queries({}) == []
    assert expand_queries(None) == []


def test_article_alone_produces_one_query():
    out = expand_queries({'article': 'A-12345'})
    assert ('A-12345', 'article') in out
    assert len(out) == 1


def test_brand_and_article_produces_two():
    out = expand_queries({'article': '315122-111', 'brand': 'Nike'})
    tags = [t for _, t in out]
    assert 'article' in tags
    assert 'brand_article' in tags
    # article must come first (more specific)
    assert tags.index('article') < tags.index('brand_article')


def test_brand_and_model_produces_brand_model():
    out = expand_queries({'brand': 'Nike', 'model': 'Air Force 1'})
    queries = dict((tag, q) for q, tag in out)
    assert queries.get('brand_model') == 'Nike Air Force 1'


def test_subcategory_and_color_descriptive():
    out = expand_queries({
        'subcategory': 'пальто',
        'primary_color': 'серый',
        'material': 'шерсть',
    })
    queries = dict((tag, q) for q, tag in out)
    assert 'descriptive' in queries
    desc = queries['descriptive']
    assert 'пальто' in desc and 'серый' in desc and 'шерсть' in desc


def test_full_ladder_specificity_order():
    attrs = {
        'article': 'A1',
        'brand': 'Nike',
        'model': 'AF1',
        'subcategory': 'кроссовки',
        'primary_color': 'белый',
        'material': 'кожа',
        'fit': 'regular',
        'style': ['minimalism', 'streetwear'],
    }
    out = expand_queries(attrs)
    tags = [t for _, t in out]
    # All present
    for expected in ('article', 'brand_article', 'brand_model',
                     'brand_subcat', 'descriptive', 'style_broad'):
        assert expected in tags, f"missing tag: {expected}"
    # Order matches SPECIFICITY_ORDER (no out-of-order entries)
    indices = [SPECIFICITY_ORDER.index(t) for t in tags]
    assert indices == sorted(indices), f"out of order: {tags}"


def test_style_broad_picks_two_styles():
    out = expand_queries({
        'subcategory': 'пальто',
        'style': ['minimalism', 'casual', 'preppy', 'extra'],
    })
    queries = dict((tag, q) for q, tag in out)
    sb = queries['style_broad']
    assert 'minimalism' in sb and 'casual' in sb
    assert 'preppy' not in sb  # capped at 2


def test_no_brand_no_subcat_means_no_brand_subcat():
    out = expand_queries({'primary_color': 'красный'})
    tags = [t for _, t in out]
    assert 'brand_subcat' not in tags


def test_falls_back_to_category_if_no_subcategory():
    out = expand_queries({'brand': 'IKEA', 'category': 'мебель',
                          'primary_color': 'белый'})
    queries = dict((tag, q) for q, tag in out)
    assert 'brand_subcat' in queries
    assert 'мебель' in queries['brand_subcat']


def test_null_string_treated_as_missing():
    out = expand_queries({'article': 'null', 'brand': 'Nike', 'subcategory': '—'})
    tags = [t for _, t in out]
    assert 'article' not in tags
    assert 'brand_article' not in tags
    # subcategory был '—' → не должен использоваться
    queries = dict((tag, q) for q, tag in out)
    assert 'descriptive' not in queries


def test_purchase_intent_appends_купить():
    out = expand_queries({'article': 'X1'}, include_purchase_intent=True)
    assert out[0][0] == 'X1 купить'


def test_deduplicates_queries():
    # When attrs collapse to same string, we shouldn't get dupes
    out = expand_queries({
        'subcategory': 'X', 'category': 'X',  # same noun via fallback
        'brand': '', 'primary_color': '',
    })
    queries = [q for q, _ in out]
    assert len(queries) == len(set(queries))


def test_whitespace_collapsed():
    out = expand_queries({'brand': '  Nike  ', 'model': '  Air   Force  '})
    queries = dict((tag, q) for q, tag in out)
    assert queries['brand_model'] == 'Nike Air Force'


# ────────────────────────────────────────────────
# top_query
# ────────────────────────────────────────────────

def test_top_query_picks_most_specific():
    assert top_query({'article': 'A1', 'brand': 'B'}) == 'A1'


def test_top_query_empty():
    assert top_query({}) is None


# ────────────────────────────────────────────────
# queries_for_source
# ────────────────────────────────────────────────

def test_brand_source_only_precise_queries():
    attrs = {
        'article': 'A1', 'brand': 'Nike', 'model': 'AF1',
        'subcategory': 'кроссовки', 'primary_color': 'белый',
        'style': ['minimalism'],
    }
    qs = queries_for_source(attrs, 'brand:nike')
    assert 'A1' in qs
    assert 'Nike A1' in qs
    assert 'Nike AF1' in qs
    # No descriptive/style queries for brand sites
    assert all('минимализм' not in q.lower() for q in qs)
    assert all('кроссовки' not in q.lower() or 'AF1' in q for q in qs)


def test_marketplace_source_uses_full_ladder():
    attrs = {
        'article': 'A1', 'brand': 'Nike', 'subcategory': 'кроссовки',
        'primary_color': 'белый', 'style': ['minimalism'],
    }
    qs = queries_for_source(attrs, 'ozon', max_n=10)
    # Full ladder so descriptive style queries must appear
    assert any('кроссовки' in q for q in qs)


def test_queries_for_source_caps_results():
    attrs = {
        'article': 'A1', 'brand': 'Nike', 'model': 'AF1',
        'subcategory': 'X', 'primary_color': 'Y',
        'style': ['s1', 's2'],
    }
    assert len(queries_for_source(attrs, 'ozon', max_n=2)) == 2
