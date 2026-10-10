"""M4 Polish sources against recorded responses: statistics (BDL, SMUP) and hazards and constraints."""

import pytest

from geomcp_core import constraints, statistics
from tests.conftest import GOLDEN_AREA_ID

pytestmark = pytest.mark.vcr

KOZANOW = "a_pt_51.1450_16.9550_ci500"  # Wrocław district on the Odra floodplain, flooded in 1997
LUBIN = "a_pt_51.4050_16.2000_ci500"  # inside the Lubin copper mine's mining terrain


@pytest.fixture(autouse=True)
def no_rate_limit_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(statistics, "_smup_last", 0.0)
    monkeypatch.setattr(constraints, "_season", lambda: 2026)  # the drought season the cassettes hold


def test_find_statistics_ranks_gmina_indicators_first() -> None:
    found = statistics.find_statistics("ludność", limit=10)
    bdl = [i for i in found if i.source == "GUS BDL"]
    assert bdl and bdl[0].level == "gmina" and all(i.id.startswith("bdl:") for i in bdl)


def test_get_statistics_compares_the_gmina_with_higher_units() -> None:
    r = statistics.get_statistics(GOLDEN_AREA_ID, ["bdl:72305"], [2023])  # population, total
    (population,) = r.indicators
    assert set(population.values) == {"gmina", "powiat", "voivodeship", "Poland"}
    assert 600_000 < population.values["gmina"]["2023"] < 700_000  # Wrocław
    assert 37_000_000 < population.values["Poland"]["2023"] < 39_000_000


def test_flood_zones_on_the_odra_floodplain() -> None:
    r = constraints.get_constraints(KOZANOW)
    shares = {i.key: i.share for i in r.items if i.category == "flood"}
    assert not r.gaps
    assert 0 < (shares["flood_10pct"] or 0) <= (shares["flood_1pct"] or 0) <= (shares["flood_02pct"] or 0) <= 1


def test_mining_terrain_and_subsidence_near_lubin() -> None:
    r = constraints.get_constraints(LUBIN)
    items = {i.key: i for i in r.items}
    assert items["mining_terrain"].share == 1.0 and "Lubin" in items["mining_terrain"].names[0]
    motion = items["ground_motion_vertical"].value
    assert motion["mean"] < 0  # the ground subsides over the copper mine


def test_orthophoto_of_a_parcel_is_a_quarter_metre_picture() -> None:
    from geomcp_core import service

    snap = service.get_snapshot("a_uldk_026401_1.0001.2395", "orthophoto")
    assert snap.png.startswith(b"\x89PNG") and snap.pixel_size_m == 0.25 and snap.provenance.commercial_safe


def test_prices_report_yearly_medians_without_personal_data() -> None:
    from geomcp_core import prices

    r = prices.get_prices("a_pt_51.1080_17.0400_ci200", since_year=2025)  # Wrocław old town, east side
    apartments = next(s for s in r.series if s.kind == "apartments_secondary")
    assert apartments.years and all(3_000 < y.median < 30_000 for y in apartments.years.values())
    assert "notariusz" not in r.model_dump_json().lower()  # the deed author never leaves the module


def test_zoning_reads_designations_from_a_gmina_schema() -> None:
    from geomcp_core import zoning

    r = zoning.get_zoning("a_pt_50.0510_19.9450_ci50")  # Kraków, Kazimierz: one sample point
    assert r.points_in_a_plan == 1 and r.designations
    d = r.designations[0]
    assert d.symbol and d.plan == "KAZIMIERZ" and (d.link or "").startswith("http")


def test_a_parcel_gets_its_cadastre_land_use() -> None:
    from geomcp_core import zoning

    r = zoning.get_zoning("a_uldk_026401_1.0001.2395")  # Wrocław old town: not in the plan integration
    assert r.points_in_a_plan == 0 and r.no_plan_messages and any("Wrocław" in n for n in r.notices)
    assert r.parcel and r.parcel["registry_area_ha"] == "0.0618" and r.parcel.get("land_use_contour") == "Bi"


def test_traffic_joins_osm_roads_to_survey_segments() -> None:
    from geomcp_core import traffic

    r = traffic.get_traffic("a_pt_51.0230_16.7700_ci1500")  # the A4 south of Kąty Wrocławskie
    assert "A4" in r.roads_in_area
    a4 = [s for s in r.segments if s.road == "A4"]
    assert a4 and all(30_000 < s.vehicles_per_day < 120_000 and s.distance_km == 0 for s in a4)
    assert all(s.vehicles_per_day_2020 for s in a4)  # the 2020/21 count of the same section


def test_a_company_by_nip_joins_the_vat_and_court_registers(monkeypatch: pytest.MonkeyPatch) -> None:
    from geomcp_core import business

    monkeypatch.setattr(business, "_today", lambda: "2026-10-09")  # the date the cassette was recorded
    b = business.lookup_business("5250000251")  # PKP S.A.
    assert b.krs == "0000019193" and b.vat_status == "Czynny" and "SKARB PAŃSTWA" in b.owners
    assert b.main_activity and b.main_activity.startswith("70.10.Z") and b.share_capital_pln
    assert "*" not in " ".join(b.board_roles)  # roles only; masked names are not passed on
    assert "accountNumbers" not in b.model_dump_json()


def test_regon_adds_activities_from_the_gus_register(monkeypatch: pytest.MonkeyPatch) -> None:
    from geomcp_core import regon

    monkeypatch.setenv("GEOMCP_REGON_KEY", "abcde12345abcde12345")  # GUS's public test key
    monkeypatch.setenv("GEOMCP_REGON_URL", "https://wyszukiwarkaregontest.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc")
    monkeypatch.setattr(regon, "_sid", None)
    entry = regon.search("nip", "5261040828")  # GUS itself, in the frozen 2018 test data
    assert entry and entry["Regon"] == "000331501" and entry["Typ"] == "P"
    assert ("84.11.Z", "Kierowanie podstawowymi rodzajami działalności publicznej", True) in regon.activities(entry)
    assert regon.search("nip", "1111111111") is None


def test_ceidg_counts_sole_traders_by_gmina_and_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    from geomcp_core import ceidg

    monkeypatch.setattr(ceidg, "MIN_INTERVAL_S", 0.0)
    r = ceidg.search(town="Smolec", pkd="43.22.Z")  # plumbing, heating and air conditioning
    assert 10 < r.count < 500 and 0 < len(r.listed) <= ceidg.PAGE
    assert all(t.gmina == "Kąty Wrocławskie" for t in r.listed)  # names and addresses are scrubbed on recording
    assert r.filters["pkd"] == "4322Z" and r.filters["status"] == "AKTYWNY"
