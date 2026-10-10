import math
from datetime import date

from geomcp_core.models import PeriodValue
from geomcp_core.summarise import pick_indices, summarise


def _seasonal_series(trend: float, years: int = 10) -> list[PeriodValue]:
    out = []
    for y in range(years):
        for q, month in enumerate((1, 4, 7, 10)):
            value = 10 * math.sin(2 * math.pi * q / 4) + trend * y
            out.append(PeriodValue(period=date(2016 + y, month, 1), mean=value, valid_fraction=1.0, status="final"))
    return out


def test_seasonal_trend_recovers_slope_despite_annual_cycle() -> None:
    s = summarise(_seasonal_series(trend=0.5), "P3M")
    assert s.trend_per_year is not None and abs(s.trend_per_year - 0.5) < 0.01
    assert s.trend_p_value is not None and s.trend_p_value < 0.001


def test_no_trend_gives_flat_slope_and_high_p() -> None:
    s = summarise(_seasonal_series(trend=0.0), "P3M")
    assert s.trend_per_year == 0.0
    assert s.trend_p_value is None or s.trend_p_value > 0.5


def test_missing_values_count_as_gaps_and_are_ignored() -> None:
    values = _seasonal_series(trend=0.5)
    values[0] = PeriodValue(period=values[0].period, mean=None, valid_fraction=0.0, status="missing")
    s = summarise(values, "P3M")
    assert s.gaps == 1
    assert s.latest is not None and s.latest.period == date(2025, 10, 1)


def test_all_missing_gives_empty_summary() -> None:
    v = [PeriodValue(period=date(2020, 1, 1), mean=None, valid_fraction=0.0, status="missing")]
    s = summarise(v, "P1M")
    assert s.latest is None and s.trend_per_year is None and s.gaps == 1


def test_pick_indices_keeps_first_and_last() -> None:
    assert pick_indices(5, 10) == [0, 1, 2, 3, 4]
    picked = pick_indices(40, 20)
    assert picked[0] == 0 and picked[-1] == 39 and len(picked) == 20
    assert pick_indices(40, 1) == [39]
