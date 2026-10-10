"""Europe-wide sources and Polish air-quality history against recorded responses."""

import pytest

from geomcp_core.adapters import gios
from geomcp_core.adapters.europe import ClimateProjectionAdapter, CorineAdapter, PvgisAdapter
from geomcp_core.adapters.gios import GiosAdapter
from geomcp_core.areas import area_from_id
from tests.conftest import GOLDEN_AREA_ID

pytestmark = pytest.mark.vcr

SMOLEC = "a_pt_51.0730_16.8990_ci600"  # a suburb that grew on farmland since 1990


@pytest.fixture(autouse=True)
def no_rate_limit_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gios, "MIN_INTERVAL_S", 0.0)


def test_pvgis_yield_is_higher_at_the_optimal_tilt() -> None:
    (fact,), _ = PvgisAdapter().facts(area_from_id(GOLDEN_AREA_ID), ["solar_potential"])
    v = fact.value
    assert isinstance(v, dict)
    assert 800 < (v["yield_flat_kwh_per_kwp"] or 0) < (v["yield_optimal_kwh_per_kwp"] or 0) < 1300
    assert 30 <= (v["optimal_tilt_deg"] or 0) <= 45  # Wrocław


def test_climate_projection_warms_and_is_flagged_non_commercial() -> None:
    (fact,), _ = ClimateProjectionAdapter().facts(area_from_id(GOLDEN_AREA_ID), ["climate_projection"])
    v = fact.value
    assert isinstance(v, dict) and (v["tmax_change"] or 0) > 0.5 and v["models"] == 3
    assert fact.provenance.commercial_safe is False


def test_corine_shows_the_suburb_growing_on_farmland(monkeypatch: pytest.MonkeyPatch) -> None:
    from geomcp_core.adapters import europe

    years = {y: europe.CLC_YEARS[y] for y in ("1990", "2018")}  # two years keep the cassette small
    monkeypatch.setattr(europe, "CLC_YEARS", years)
    (fact,), _ = CorineAdapter().facts(area_from_id(SMOLEC), ["landcover_history"])
    v = fact.value
    assert isinstance(v, dict)
    assert (v.get("urban_fabric_2018") or 0) > (v.get("urban_fabric_1990") or 0)
    assert (v.get("agricultural_2018") or 0) < (v.get("agricultural_1990") or 0)


def test_air_quality_history_has_a_decade_of_verified_annual_means() -> None:
    facts, _ = GiosAdapter().facts(area_from_id(GOLDEN_AREA_ID), ["pm10_history"])
    (pm10,) = facts
    assert isinstance(pm10.value, dict) and len(pm10.value) >= 10
    assert (pm10.value["2024"] or 0) < (pm10.value["2014"] or 99)  # Wrocław's air improved
    assert "Wrocław" in pm10.provenance.method
