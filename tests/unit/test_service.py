from datetime import date

import pytest

from geomcp_core import service
from geomcp_core.models import GeomcpError
from tests.conftest import GOLDEN_AREA_ID

VARS = ["synthetic_ndvi", "synthetic_t2m", "synthetic_station_t2m"]


def test_ten_year_quarterly_answer_is_compact_and_sourced() -> None:
    r = service.get_timeseries(GOLDEN_AREA_ID, VARS, date(2016, 1, 1), date(2026, 1, 1))
    assert r.period.cadence == "P3M" and r.period.n_periods == 40
    assert [s.variable for s in r.series] == VARS
    assert len(r.rows) <= 60 and r.rows_truncated is not None and r.rows_truncated.total == 120
    for s in r.series:
        assert s.units and s.provenance.licence and s.provenance.attribution
    # Sampled periods keep all their variables.
    per_period: dict[object, int] = {}
    for row in r.rows:
        per_period[row[0]] = per_period.get(row[0], 0) + 1
    assert set(per_period.values()) == {3}


def test_values_are_physically_plausible() -> None:
    r = service.get_timeseries(GOLDEN_AREA_ID, VARS, date(2016, 1, 1), date(2026, 1, 1), max_rows=500)
    for period, variable, mean, *_rest, status in r.rows:
        if status == "missing":
            assert mean is None
        elif variable == "synthetic_ndvi":
            assert -1 <= mean <= 1
        else:
            assert -30 < mean < 40, (period, variable, mean)


def test_every_gap_is_listed() -> None:
    r = service.get_timeseries(GOLDEN_AREA_ID, ["synthetic_ndvi"], date(2016, 1, 1), date(2026, 1, 1), max_rows=500)
    missing = {row[0] for row in r.rows if row[-1] == "missing"}
    assert missing == {g.period for g in r.gaps}
    assert r.series[0].summary.gaps == len(missing) > 0


def test_restricted_source_adds_notice_and_can_be_excluded() -> None:
    r = service.get_timeseries(GOLDEN_AREA_ID, ["synthetic_station_t2m"], date(2024, 1, 1), date(2025, 1, 1))
    assert any("commercial_safe=false" in n for n in r.notices)
    r = service.get_timeseries(
        GOLDEN_AREA_ID,
        ["synthetic_station_t2m"],
        date(2024, 1, 1),
        date(2025, 1, 1),
        commercial_safe_only=True,
    )
    assert r.series == [] and r.gaps[0].reason == "source_unavailable"


def test_future_periods_are_missing_and_recent_ones_preliminary() -> None:
    today = date.today()
    start = date(today.year - 1, 1, 1)
    end = date(today.year + 2, 1, 1)
    r = service.get_timeseries(GOLDEN_AREA_ID, ["synthetic_t2m"], start, end, "P1M", max_rows=500)
    statuses = {row[0]: row[-1] for row in r.rows}
    assert statuses[date(today.year + 1, 6, 1)] == "missing"  # not December of this year: that fails in December
    assert statuses[today.replace(day=1)] in ("preliminary", "missing")  # the current month is never final
    assert "preliminary" in statuses.values()


def test_unknown_variable_and_big_area_fail_clearly() -> None:
    with pytest.raises(GeomcpError) as e:
        service.get_timeseries(GOLDEN_AREA_ID, ["nope"], date(2024, 1, 1), date(2025, 1, 1))
    assert e.value.code == "unknown_variable"
    with pytest.raises(GeomcpError) as e:
        service.get_timeseries("a_pt_51.1100_17.0320_sq20000", ["synthetic_t2m"], date(2024, 1, 1), date(2025, 1, 1))
    assert e.value.code == "area_too_large"


def test_list_data_filters_by_topic() -> None:
    weather = service.list_data("weather")
    names = {v.name for s in weather for v in s.source.variables}
    assert {"synthetic_t2m", "synthetic_precipitation", "synthetic_station_t2m", "t2m", "precipitation"} <= names
    assert all(v.topic == "weather" for s in weather for v in s.source.variables)


def test_chunks_never_include_the_exclusive_end_year() -> None:
    from geomcp_core.adapters.sentinel_hub import Sentinel2Adapter
    from geomcp_core.periods import period_grid

    chunks = Sentinel2Adapter()._chunks(period_grid(date(2024, 1, 1), date(2025, 1, 1), "P1Y"), date(2026, 10, 8))  # pyright: ignore[reportPrivateUsage]
    assert chunks == [(date(2024, 1, 1), date(2025, 1, 1))]
    current = Sentinel2Adapter()._chunks(period_grid(date(2026, 8, 1), date(2026, 10, 1), "P1M"), date(2026, 10, 8))  # pyright: ignore[reportPrivateUsage]
    assert current == [(date(2026, 8, 1), date(2026, 9, 1)), (date(2026, 9, 1), date(2026, 10, 1))]
