from datetime import date
from itertools import pairwise

import pytest

from geomcp_core.models import GeomcpError
from geomcp_core.periods import default_cadence, period_grid


def test_quarterly_grid_covers_range_without_overlap() -> None:
    periods = period_grid(date(2016, 1, 1), date(2026, 1, 1), "P3M")
    assert len(periods) == 40
    assert periods[0].start == date(2016, 1, 1) and periods[-1].end == date(2026, 1, 1)
    assert all(a.end == b.start for a, b in pairwise(periods))


def test_partial_last_period_is_clipped_to_end() -> None:
    periods = period_grid(date(2024, 1, 1), date(2024, 2, 15), "P1M")
    assert [(p.start, p.end) for p in periods] == [
        (date(2024, 1, 1), date(2024, 2, 1)),
        (date(2024, 2, 1), date(2024, 2, 15)),
    ]


def test_weekly_grid_requires_iso_monday() -> None:
    assert len(period_grid(date(2024, 6, 3), date(2024, 6, 17), "P1W")) == 2
    with pytest.raises(GeomcpError) as e:
        period_grid(date(2024, 6, 4), date(2024, 6, 17), "P1W")
    assert e.value.code == "invalid_range" and "2024-06-03" in e.value.hint


def test_monthly_grid_requires_first_of_month_and_positive_range() -> None:
    with pytest.raises(GeomcpError):
        period_grid(date(2024, 1, 2), date(2024, 3, 1), "P1M")
    with pytest.raises(GeomcpError):
        period_grid(date(2024, 3, 1), date(2024, 3, 1), "P1M")


def test_default_cadence_is_quarterly_above_two_years() -> None:
    assert default_cadence(date(2016, 1, 1), date(2026, 1, 1)) == "P3M"
    assert default_cadence(date(2025, 1, 1), date(2026, 1, 1)) == "P1M"
