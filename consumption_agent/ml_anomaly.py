"""
ml_anomaly.py — Price Anomaly Detector for canonicalized product groups.

Stage 3/§6 of visual-product-search skill. Given a canonical group from
ml_canonical.canonicalize() plus Vision attributes, flag listings that
look like counterfeits ('suspicious_cheap') or significant overpricing
('overprice') and explain why.

Three independent checks (any one flag fires):
    1. Intra-group spread: the cheapest listing is far below group median
    2. Vision-estimate mismatch: median far from Vision's price guess
    3. Brand-history mismatch: median below average paid for this brand

Each check returns a severity score in [0, 1] derived from the ratio,
so the caller can decide whether to display a warning to the user.

This module is pure logic — DB access is via brand_history_provider so
tests can inject any baseline.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Thresholds (tunable)
# ---------------------------------------------------------------------------
# Intra-group: cheapest below MEDIAN × this → flag
INTRA_CHEAP_RATIO = 0.40
# Vision estimate guard rails
VISION_OVERPRICE_RATIO = 1.80   # median above est × this → overprice
VISION_CHEAP_RATIO = 0.30       # median below est × this → suspicious cheap
# Brand-history guard rails
BRAND_CHEAP_RATIO = 0.50        # median below brand-avg × this → suspicious

# Need at least N listings before intra-group spread is meaningful
MIN_LISTINGS_FOR_INTRA = 2


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Anomaly:
    kind: str           # 'suspicious_cheap' | 'overprice'
    severity: float     # 0.0..1.0 — higher = more suspicious
    baseline: int       # what we compared against (median / est / brand_avg)
    observed: int       # what we saw (min or median)
    source: str         # 'intra_group' | 'vision_estimate' | 'brand_history'
    reason: str         # user-facing explanation in Russian

    def to_dict(self) -> dict:
        return {
            'kind': self.kind,
            'severity': self.severity,
            'baseline': self.baseline,
            'observed': self.observed,
            'source': self.source,
            'reason': self.reason,
        }


# ---------------------------------------------------------------------------
# Brand history helper
# ---------------------------------------------------------------------------
def avg_paid_for_brand(conn: sqlite3.Connection, brand: Optional[str]) -> Optional[float]:
    """Average purchase_price from `items` for the given brand. None if
    brand empty or no priced rows."""
    if not brand:
        return None
    try:
        row = conn.execute(
            """
            SELECT AVG(purchase_price)
            FROM items
            WHERE LOWER(brand) = LOWER(?)
              AND purchase_price IS NOT NULL
              AND purchase_price > 0
              AND deleted_at IS NULL
            """,
            (brand,),
        ).fetchone()
    except sqlite3.OperationalError:
        # deleted_at column may not exist on every schema variant
        row = conn.execute(
            """
            SELECT AVG(purchase_price)
            FROM items
            WHERE LOWER(brand) = LOWER(?)
              AND purchase_price IS NOT NULL
              AND purchase_price > 0
            """,
            (brand,),
        ).fetchone()
    avg = row[0] if row else None
    if avg is None or avg <= 0:
        return None
    return float(avg)


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------
def _severity(observed: int, baseline: int, *, direction: str) -> float:
    """How far observed is from baseline, on a 0..1 scale.
    direction='below' → severity grows as observed drops below baseline.
    direction='above' → severity grows as observed climbs above baseline.
    """
    if baseline <= 0:
        return 0.0
    ratio = observed / baseline
    if direction == 'below':
        # ratio 1.0 → 0, ratio 0.0 → 1.0
        return max(0.0, min(1.0, 1.0 - ratio))
    # 'above'
    # ratio 1.0 → 0, ratio 3.0 → 1.0 (cap at 3x)
    return max(0.0, min(1.0, (ratio - 1.0) / 2.0))


def detect_anomaly(
    canonical_row: dict,
    attrs: Optional[dict] = None,
    *,
    brand_history_avg: Optional[float] = None,
) -> Optional[dict]:
    """Examine one canonical group for pricing anomalies.

    Returns the FIRST anomaly found (in priority order: intra-group → vision
    → brand-history), as a dict. Returns None when nothing looks suspicious.

    Priority rationale: intra-group is the strongest signal (we have the
    market itself disagreeing on the price). Vision estimate is a soft prior.
    Brand history is the weakest (averages across many items).
    """
    if not isinstance(canonical_row, dict):
        return None

    attrs = attrs or {}
    median = canonical_row.get('price_median')
    minp = canonical_row.get('price_min')
    n_listings = canonical_row.get('sources_count') or 0

    # ── 1. Intra-group spread ────────────────────────────────────────────
    if (median and minp and median > 0
            and n_listings >= MIN_LISTINGS_FOR_INTRA
            and minp < INTRA_CHEAP_RATIO * median):
        return Anomaly(
            kind='suspicious_cheap',
            severity=_severity(minp, median, direction='below'),
            baseline=int(median),
            observed=int(minp),
            source='intra_group',
            reason=(
                f"Самое дешёвое предложение ({minp} ₽) сильно ниже медианы "
                f"группы ({median} ₽). Возможна подделка или устаревший листинг."
            ),
        ).to_dict()

    # ── 2. Vision estimate mismatch ──────────────────────────────────────
    est = attrs.get('estimated_price_rub')
    if median and est and est > 0:
        if median > VISION_OVERPRICE_RATIO * est:
            return Anomaly(
                kind='overprice',
                severity=_severity(median, est, direction='above'),
                baseline=int(est),
                observed=int(median),
                source='vision_estimate',
                reason=(
                    f"Цена ({median} ₽) заметно выше визуальной оценки "
                    f"(~{est} ₽). Похоже на переплату."
                ),
            ).to_dict()
        if median < VISION_CHEAP_RATIO * est:
            return Anomaly(
                kind='suspicious_cheap',
                severity=_severity(median, est, direction='below'),
                baseline=int(est),
                observed=int(median),
                source='vision_estimate',
                reason=(
                    f"Цена ({median} ₽) сильно ниже визуальной оценки "
                    f"(~{est} ₽). Проверьте оригинальность."
                ),
            ).to_dict()

    # ── 3. Brand history mismatch ────────────────────────────────────────
    if median and brand_history_avg and brand_history_avg > 0:
        if median < BRAND_CHEAP_RATIO * brand_history_avg:
            return Anomaly(
                kind='suspicious_cheap',
                severity=_severity(median, int(brand_history_avg), direction='below'),
                baseline=int(brand_history_avg),
                observed=int(median),
                source='brand_history',
                reason=(
                    f"Цена ({median} ₽) ниже половины от средней по бренду "
                    f"({int(brand_history_avg)} ₽). Возможна подделка."
                ),
            ).to_dict()

    return None


def detect_all(
    canonical_rows: list[dict],
    attrs: Optional[dict] = None,
    *,
    brand_history_provider: Callable[[Optional[str]], Optional[float]] | None = None,
) -> list[dict]:
    """Run detect_anomaly over a list of canonical groups. Returns rows
    enriched with an 'anomaly' field (None for clean rows)."""
    attrs = attrs or {}
    brand = attrs.get('brand')
    brand_avg = brand_history_provider(brand) if brand_history_provider else None

    out = []
    for row in canonical_rows:
        flag = detect_anomaly(row, attrs, brand_history_avg=brand_avg)
        out.append({**row, 'anomaly': flag})
    return out
