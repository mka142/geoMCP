"""Real adapters against recorded responses for the golden AOI (10 km², central Wrocław)."""

import math
from datetime import date

import pytest

from geomcp_core.adapters.cds import Era5LandAdapter
from geomcp_core.adapters.sentinel_hub import CopernicusDemAdapter, Sentinel1Adapter, Sentinel2Adapter
from geomcp_core.areas import area_from_id
from geomcp_core.periods import period_grid
from tests.conftest import GOLDEN_AREA_ID

pytestmark = pytest.mark.vcr
SUMMER_2024 = period_grid(date(2024, 6, 1), date(2024, 9, 1), "P1M")


def test_sentinel2_indices_are_plausible() -> None:
    area = area_from_id(GOLDEN_AREA_ID)
    s = Sentinel2Adapter().stats(area, "ndvi", SUMMER_2024)
    means = [v.mean for v in s.values if v.mean is not None]
    assert len(means) == 3 and all(0.1 < m < 0.6 for m in means)  # urban core in summer
    assert s.cost.sentinel_hub_units > 0 and "30 m" in s.provenance.method
    # the same cached year serves every other index without new requests
    ndwi = Sentinel2Adapter().stats(area, "ndwi", SUMMER_2024)
    assert ndwi.cost.cache_hits == 1 and ndwi.cost.cache_misses == 0
    assert all(v.mean is not None and v.mean < 0 for v in ndwi.values)  # land, not water


def test_gotcha_02_one_observation_per_day_despite_overlapping_tiles() -> None:
    obs, _, _ = Sentinel2Adapter().observations(area_from_id(GOLDEN_AREA_ID), "ndvi", SUMMER_2024)
    days = [o.day for o in obs]
    assert len(days) == len(set(days))  # P1D intervals mosaic same-day tiles, like groupby="solar_day"


def test_gotcha_04_sar_is_averaged_in_linear_power_then_converted_to_db() -> None:
    area = area_from_id(GOLDEN_AREA_ID)
    adapter = Sentinel1Adapter()
    obs, _, _ = adapter.observations(area, "s1_vv", SUMMER_2024)
    june = [o.mean for o in obs if o.day.month == 6 and o.valid_fraction > 0]
    s = adapter.stats(area, "s1_vv", SUMMER_2024)
    expected = 10 * math.log10(sum(june) / len(june))
    assert s.units == "dB" and s.values[0].mean == pytest.approx(expected, abs=1e-3)


def test_elevation_and_slope_for_the_golden_area() -> None:
    facts, _ = CopernicusDemAdapter().facts(area_from_id(GOLDEN_AREA_ID), ["elevation", "slope"])
    elevation = next(f for f in facts if f.variable == "elevation").value
    slope = next(f for f in facts if f.variable == "slope").value
    assert isinstance(elevation, dict) and isinstance(slope, dict)
    assert 100 <= (elevation["mean"] or 0) <= 160 and (slope["mean"] or -1) >= 0


def test_gotcha_05_era5_land_is_hourly_not_accumulated_and_in_celsius() -> None:
    area = area_from_id(GOLDEN_AREA_ID)
    year = Era5LandAdapter().stats(area, "precipitation", period_grid(date(2024, 1, 1), date(2025, 1, 1), "P1Y"))
    total = year.values[0].mean
    assert total is not None and 450 < total < 1100  # summed hourly amounts; de-accumulating would give ~30 mm
    summer = Era5LandAdapter().stats(area, "t2m", SUMMER_2024)  # same cached year, no new request
    assert summer.units == "degC" and summer.cost.cache_hits == 1
    assert all(v.mean is not None and 15 < v.mean < 26 for v in summer.values)
