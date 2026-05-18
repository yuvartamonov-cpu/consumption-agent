"""Tests for ml_anomaly — price anomaly detection."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ml_anomaly as ma
from ml_anomaly import detect_anomaly, detect_all, avg_paid_for_brand


# ────────────────────────────────────────────────
# detect_anomaly — intra-group
# ────────────────────────────────────────────────

def test_clean_group_returns_none():
    row = {'price_min': 11500, 'price_max': 12500, 'price_median': 12000,
           'sources_count': 3}
    assert detect_anomaly(row) is None


def test_intra_group_cheap_listing_flagged():
    # Median 12 000, min 3 000 (25%) — well below 40% threshold
    row = {'price_min': 3000, 'price_max': 13000, 'price_median': 12000,
           'sources_count': 3}
    flag = detect_anomaly(row)
    assert flag is not None
    assert flag['kind'] == 'suspicious_cheap'
    assert flag['source'] == 'intra_group'
    assert flag['baseline'] == 12000
    assert flag['observed'] == 3000
    assert 0.0 < flag['severity'] <= 1.0


def test_intra_group_needs_multiple_listings():
    # Single listing — can't have intra-group anomaly
    row = {'price_min': 1000, 'price_max': 1000, 'price_median': 1000,
           'sources_count': 1}
    assert detect_anomaly(row) is None


def test_intra_group_just_above_threshold():
    # min/median = 0.45 → above 0.4 threshold → no flag
    row = {'price_min': 5400, 'price_max': 13000, 'price_median': 12000,
           'sources_count': 3}
    assert detect_anomaly(row) is None


# ────────────────────────────────────────────────
# detect_anomaly — Vision estimate
# ────────────────────────────────────────────────

def test_overprice_via_vision():
    # est = 5000, median = 15000 → 3x → overprice
    row = {'price_min': 14000, 'price_max': 16000, 'price_median': 15000,
           'sources_count': 3}
    attrs = {'estimated_price_rub': 5000}
    flag = detect_anomaly(row, attrs)
    assert flag['kind'] == 'overprice'
    assert flag['source'] == 'vision_estimate'
    assert flag['baseline'] == 5000
    assert flag['observed'] == 15000


def test_suspicious_cheap_via_vision():
    # est = 12000, median = 3000 → 0.25x → cheap
    row = {'price_min': 2900, 'price_max': 3100, 'price_median': 3000,
           'sources_count': 3}
    attrs = {'estimated_price_rub': 12000}
    flag = detect_anomaly(row, attrs)
    assert flag['kind'] == 'suspicious_cheap'
    assert flag['source'] == 'vision_estimate'


def test_vision_estimate_in_range_no_flag():
    row = {'price_min': 4500, 'price_max': 5500, 'price_median': 5000,
           'sources_count': 3}
    attrs = {'estimated_price_rub': 5000}
    assert detect_anomaly(row, attrs) is None


def test_vision_overprice_just_below_threshold():
    # ratio = 1.79 → below 1.8 → no flag
    row = {'price_min': 8950, 'price_max': 8950, 'price_median': 8950,
           'sources_count': 2}
    attrs = {'estimated_price_rub': 5000}
    assert detect_anomaly(row, attrs) is None


# ────────────────────────────────────────────────
# detect_anomaly — brand history
# ────────────────────────────────────────────────

def test_brand_history_flag():
    # brand_avg = 20000, median = 5000 → 0.25 → flag
    row = {'price_min': 5000, 'price_max': 5000, 'price_median': 5000,
           'sources_count': 1}
    flag = detect_anomaly(row, {'brand': 'Nike'},
                          brand_history_avg=20000)
    assert flag['kind'] == 'suspicious_cheap'
    assert flag['source'] == 'brand_history'
    assert flag['baseline'] == 20000


def test_brand_history_not_triggered_when_in_range():
    row = {'price_min': 18000, 'price_max': 18000, 'price_median': 18000,
           'sources_count': 1}
    assert detect_anomaly(row, {'brand': 'Nike'},
                          brand_history_avg=20000) is None


def test_brand_history_ignored_when_no_brand_avg():
    row = {'price_min': 100, 'price_max': 100, 'price_median': 100,
           'sources_count': 1}
    assert detect_anomaly(row, {'brand': 'Unknown'},
                          brand_history_avg=None) is None


# ────────────────────────────────────────────────
# Priority order: intra > vision > brand
# ────────────────────────────────────────────────

def test_priority_intra_group_wins_over_vision():
    # Both checks would fire — intra-group must win
    row = {'price_min': 1000, 'price_max': 15000, 'price_median': 10000,
           'sources_count': 5}
    attrs = {'estimated_price_rub': 3000}  # would say 'overprice'
    flag = detect_anomaly(row, attrs)
    assert flag['source'] == 'intra_group'


def test_priority_vision_wins_over_brand_history():
    row = {'price_min': 1000, 'price_max': 1000, 'price_median': 1000,
           'sources_count': 1}
    attrs = {'estimated_price_rub': 5000}     # would say cheap (1/5 = 0.2)
    flag = detect_anomaly(row, attrs, brand_history_avg=10000)  # also cheap
    assert flag['source'] == 'vision_estimate'


# ────────────────────────────────────────────────
# severity scaling
# ────────────────────────────────────────────────

def test_severity_increases_with_distance():
    # Two rows with same shape but different min — severity should be higher
    # for the one farther from the median
    mild = detect_anomaly(
        {'price_min': 3000, 'price_max': 10000, 'price_median': 10000,
         'sources_count': 3}
    )
    severe = detect_anomaly(
        {'price_min': 500, 'price_max': 10000, 'price_median': 10000,
         'sources_count': 3}
    )
    assert mild is not None and severe is not None
    assert severe['severity'] > mild['severity']


# ────────────────────────────────────────────────
# defensive checks
# ────────────────────────────────────────────────

def test_non_dict_input_returns_none():
    assert detect_anomaly(None) is None
    assert detect_anomaly("group") is None


def test_empty_group_returns_none():
    assert detect_anomaly({}) is None


def test_missing_median_returns_none():
    assert detect_anomaly({'price_min': 100, 'sources_count': 2}) is None


# ────────────────────────────────────────────────
# detect_all
# ────────────────────────────────────────────────

def test_detect_all_enriches_rows():
    rows = [
        {'price_min': 1000, 'price_max': 12000, 'price_median': 12000,
         'sources_count': 3},  # cheap
        {'price_min': 5000, 'price_max': 5500, 'price_median': 5200,
         'sources_count': 2},  # clean
    ]
    out = detect_all(rows)
    assert out[0]['anomaly'] is not None
    assert out[1]['anomaly'] is None
    # Original keys preserved
    assert out[0]['price_median'] == 12000


def test_detect_all_uses_brand_provider():
    captured = []
    def provider(brand):
        captured.append(brand)
        return 20000.0
    rows = [{'price_min': 5000, 'price_max': 5000, 'price_median': 5000,
             'sources_count': 1}]
    out = detect_all(rows, {'brand': 'Nike'}, brand_history_provider=provider)
    assert captured == ['Nike']
    assert out[0]['anomaly']['source'] == 'brand_history'


# ────────────────────────────────────────────────
# avg_paid_for_brand — DB-backed helper
# ────────────────────────────────────────────────

def _setup_items(deleted_at: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    extra = ', deleted_at TEXT' if deleted_at else ''
    conn.execute(
        f"CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, brand TEXT, "
        f"purchase_price REAL{extra})"
    )
    return conn


def test_avg_paid_simple_case():
    conn = _setup_items()
    conn.executemany(
        "INSERT INTO items (name, brand, purchase_price) VALUES (?,?,?)",
        [('Boots', 'Nike', 10000.0),
         ('Cap', 'Nike', 2000.0),
         ('Shirt', 'Adidas', 5000.0)]
    )
    assert avg_paid_for_brand(conn, 'Nike') == 6000.0
    assert avg_paid_for_brand(conn, 'Adidas') == 5000.0
    conn.close()


def test_avg_paid_case_insensitive():
    conn = _setup_items()
    conn.execute("INSERT INTO items (name, brand, purchase_price) VALUES ('x', 'NIKE', 5000.0)")
    assert avg_paid_for_brand(conn, 'nike') == 5000.0
    conn.close()


def test_avg_paid_skips_null_and_zero_prices():
    conn = _setup_items()
    conn.executemany(
        "INSERT INTO items (name, brand, purchase_price) VALUES (?,?,?)",
        [('A', 'Nike', None),
         ('B', 'Nike', 0.0),
         ('C', 'Nike', 4000.0)]
    )
    assert avg_paid_for_brand(conn, 'Nike') == 4000.0
    conn.close()


def test_avg_paid_skips_deleted():
    conn = _setup_items()
    conn.executemany(
        "INSERT INTO items (name, brand, purchase_price, deleted_at) VALUES (?,?,?,?)",
        [('A', 'Nike', 5000.0, None),
         ('B', 'Nike', 100.0, '2025-01-01')]
    )
    assert avg_paid_for_brand(conn, 'Nike') == 5000.0
    conn.close()


def test_avg_paid_empty_brand_returns_none():
    conn = _setup_items()
    assert avg_paid_for_brand(conn, '') is None
    assert avg_paid_for_brand(conn, None) is None
    conn.close()


def test_avg_paid_no_rows_returns_none():
    conn = _setup_items()
    assert avg_paid_for_brand(conn, 'Nike') is None
    conn.close()


def test_avg_paid_works_without_deleted_at_column():
    """Older schemas may lack deleted_at — graceful fallback."""
    conn = _setup_items(deleted_at=False)
    conn.execute("INSERT INTO items (name, brand, purchase_price) VALUES ('x', 'Nike', 7000)")
    assert avg_paid_for_brand(conn, 'Nike') == 7000.0
    conn.close()
