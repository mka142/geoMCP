"""M2 sources against recorded responses for the golden AOI (10 km², central Wrocław)."""

import pytest

from geomcp_core.adapters import gios
from geomcp_core.adapters.gios import GiosAdapter
from geomcp_core.adapters.gugik_nmt import GugikNmtAdapter
from geomcp_core.adapters.gus_bdl import GusBdlAdapter
from geomcp_core.adapters.osm import OsmAdapter
from geomcp_core.areas import area_from_id
from tests.conftest import GOLDEN_AREA_ID

pytestmark = pytest.mark.vcr


@pytest.fixture(autouse=True)
def no_rate_limit_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gios, "MIN_INTERVAL_S", 0.0)


def test_osm_counts_and_road_length_for_a_city_centre() -> None:
    old_town = area_from_id("a_pt_51.1100_17.0320_ci300")  # 300 m around the market square keeps the cassette small
    facts, _ = OsmAdapter().facts(old_town, ["buildings", "schools", "road_length"])
    values = {f.variable: f.value for f in facts}
    assert isinstance(values["buildings"], float) and 50 < values["buildings"] < 600
    assert isinstance(values["road_length"], float) and 1 < values["road_length"] < 25  # clipped to the circle
    assert all(f.provenance.licence == "ODbL-1.0" for f in facts)


def test_gus_statistics_come_from_the_containing_gmina() -> None:
    facts, _ = GusBdlAdapter().facts(area_from_id(GOLDEN_AREA_ID), ["population", "budget_income_per_capita"])
    population = next(f for f in facts if f.variable == "population")
    assert isinstance(population.value, dict) and 600_000 < (population.value["latest"] or 0) < 700_000  # Wrocław
    assert "TERYT 0264011" in population.provenance.method and "contains this area" in population.provenance.method


def test_gotcha_17_gios_station_times_are_converted_to_utc() -> None:
    facts, _ = GiosAdapter().facts(area_from_id(GOLDEN_AREA_ID), ["pm10_station", "no2_station"])
    assert {f.variable for f in facts} == {"pm10_station", "no2_station"}
    for f in facts:
        assert f.period is not None and f.period.endswith("Z") and "km from the area centroid" in f.provenance.method
        assert isinstance(f.value, dict) and 0 <= (f.value["last_24h_mean"] or -1) < 500


def test_gotcha_07_08_nmt_axis_order_and_height_datum() -> None:
    parcel = area_from_id("a_uldk_026401_1.0001.2395")  # a 618 m² parcel near Wrocław's market square: 1 m cells
    facts, _ = GugikNmtAdapter().facts(parcel, ["elevation_ground"])
    (elevation,) = facts
    assert isinstance(elevation.value, dict)
    assert 115 < (elevation.value["mean"] or 0) < 125  # a swapped axis order would land outside Poland or on zeros
    assert "1 m cells" in elevation.provenance.method
    assert "PL-KRON86-NH" in elevation.provenance.method  # datum stated, not mixed with EGM2008
