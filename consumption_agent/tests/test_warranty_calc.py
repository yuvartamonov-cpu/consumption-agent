import pytest

from warranty_check import calc_warranty_until


@pytest.mark.parametrize(
    "purchase_date,months,expected",
    [
        ("2024-03-15", 24, "2026-03-15"),
        ("2024-01-31", 1, "2024-02-29"),
        ("2024-12-10", 13, "2026-01-10"),
    ],
)
def test_calc_warranty_until_month_boundaries(purchase_date, months, expected):
    result = calc_warranty_until(purchase_date, months)
    assert result.strftime("%Y-%m-%d") == expected
