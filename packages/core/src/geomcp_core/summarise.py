"""Turn long series into compact answers: trend, extremes, latest value, gaps.

Trends use the seasonal Theil-Sen slope and the seasonal Mann-Kendall test (Hirsch et al., 1982):
values are compared only with values from the same season (same month or ISO week), so the annual
cycle does not leak into the trend.
"""

import math
from collections import defaultdict
from datetime import date
from statistics import median

from geomcp_core.models import Cadence, Extreme, PeriodValue, Summary


def season_of(d: date, cadence: Cadence) -> int:
    if cadence == "P1W":
        return min(d.isocalendar().week, 52)  # week 53 joins week 52
    if cadence == "P1Y":
        return 0
    return d.month


def _years(d: date) -> float:
    return d.toordinal() / 365.2425


def seasonal_theil_sen(points: list[tuple[int, float, float]]) -> float | None:
    """Median of pairwise slopes within each season. `points` are (season, x in years, y)."""
    by_season: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for season, x, y in points:
        by_season[season].append((x, y))
    # ponytail: O(n^2) pairs per season; fine for decades of weekly data, sample pairs beyond that
    slopes = [
        (pts[j][1] - pts[i][1]) / (pts[j][0] - pts[i][0])
        for pts in by_season.values()
        for i in range(len(pts))
        for j in range(i + 1, len(pts))
        if pts[j][0] != pts[i][0]
    ]
    return median(slopes) if slopes else None


def seasonal_mann_kendall_p(points: list[tuple[int, float, float]]) -> float | None:
    """Two-sided p-value: per-season S and variance (with tie correction) summed, normal approximation."""
    by_season: dict[int, list[float]] = defaultdict(list)
    for season, _, y in points:
        by_season[season].append(y)
    s_total = 0
    var_total = 0.0
    for ys in by_season.values():
        n = len(ys)
        if n < 2:
            continue
        s_total += sum((ys[j] > ys[i]) - (ys[j] < ys[i]) for i in range(n) for j in range(i + 1, n))
        ties: dict[float, int] = defaultdict(int)
        for y in ys:
            ties[y] += 1
        var_total += (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in ties.values())) / 18
    if var_total <= 0:
        return None
    z = (s_total - math.copysign(1, s_total)) / math.sqrt(var_total) if s_total != 0 else 0.0
    return math.erfc(abs(z) / math.sqrt(2))


def summarise(values: list[PeriodValue], cadence: Cadence) -> Summary:
    valid = [v for v in values if v.status != "missing" and v.mean is not None]
    gaps = len(values) - len(valid)
    if not valid:
        return Summary(trend_per_year=None, trend_p_value=None, min=None, max=None, latest=None, gaps=gaps)
    points = [(season_of(v.period, cadence), _years(v.period), v.mean or 0.0) for v in valid]
    slope = seasonal_theil_sen(points)
    p = seasonal_mann_kendall_p(points)
    lo = min(valid, key=lambda v: v.mean or 0.0)
    hi = max(valid, key=lambda v: v.mean or 0.0)
    last = valid[-1]
    return Summary(
        trend_per_year=None if slope is None else round(slope, 6),
        trend_p_value=None if p is None else round(p, 4),
        min=Extreme(value=lo.mean or 0.0, period=lo.period),
        max=Extreme(value=hi.mean or 0.0, period=hi.period),
        latest=Extreme(value=last.mean or 0.0, period=last.period),
        gaps=gaps,
    )


def pick_indices(n: int, k: int) -> list[int]:
    """All of range(n), or an evenly spaced sample of k indices that keeps the first and last."""
    if n <= k:
        return list(range(n))
    if k == 1:
        return [n - 1]
    step = (n - 1) / (k - 1)
    return sorted({round(i * step) for i in range(k)})
