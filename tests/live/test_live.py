"""Live tests against the real services.

Nightly (cheap, a few processing units):  uv run pytest -m "live and not costly"
On demand (hundreds of units):            uv run --group crosscheck pytest -m costly
"""

import time
from datetime import UTC, date, datetime, timedelta

import pytest

from geomcp_core import service
from geomcp_core.adapters.sentinel_hub import Sentinel2Adapter
from geomcp_core.areas import area_from_id, resolve_area
from geomcp_core.periods import period_grid
from tests.conftest import GOLDEN_AREA_ID

pytestmark = pytest.mark.usefixtures("live_credentials")


@pytest.fixture(autouse=True)
def cold_cache(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOMCP_DATA_DIR", str(tmp_path))


@pytest.mark.live
def test_smoke_recent_month_from_every_source() -> None:
    today = datetime.now(UTC).date()
    start = (today - timedelta(days=75)).replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    r = service.get_timeseries(GOLDEN_AREA_ID, ["ndvi", "s1_vv", "t2m", "precipitation"], start, end, "P1M")
    assert not [g for g in r.gaps if g.period is None], r.notices  # every source answered
    s = service.get_summary(GOLDEN_AREA_ID, ["elevation"])
    elevation = s.facts[0].value
    assert isinstance(elevation, dict) and 100 <= (elevation["mean"] or 0) <= 160


@pytest.mark.live
def test_place_names_parcels_and_ambiguity() -> None:
    areas, selected = resolve_area("gmina Kąty Wrocławskie")
    assert selected and areas[0].admin.get("terc") == "0223043" and 170 < areas[0].area_km2 < 185
    _, selected = resolve_area("Lipno")
    assert selected is None
    (parcel,), selected = resolve_area("026401_1.0001.2395")
    assert selected == parcel.area_id and parcel.area_km2 < 0.01


@pytest.mark.live
@pytest.mark.costly
def test_performance_ten_years_cold_and_warm() -> None:
    """10 years of quarterly NDVI plus monthly weather: within 60 s cold and 2 s warm (about 280 PU)."""
    variables = ["ndvi", "t2m", "precipitation"]
    t0 = time.monotonic()
    service.get_timeseries(GOLDEN_AREA_ID, variables[:1], date(2016, 1, 1), date(2026, 1, 1), "P3M")
    service.get_timeseries(GOLDEN_AREA_ID, variables[1:], date(2016, 1, 1), date(2026, 1, 1), "P1M")
    cold = time.monotonic() - t0
    t0 = time.monotonic()
    service.get_timeseries(GOLDEN_AREA_ID, variables[:1], date(2016, 1, 1), date(2026, 1, 1), "P3M")
    service.get_timeseries(GOLDEN_AREA_ID, variables[1:], date(2016, 1, 1), date(2026, 1, 1), "P1M")
    warm = time.monotonic() - t0
    print(f"cold {cold:.1f} s, warm {warm:.2f} s")
    assert cold < 60 and warm < 2


@pytest.mark.live
@pytest.mark.costly
def test_gotcha_01_crosscheck_ndvi_against_earth_search_cogs() -> None:
    """Sentinel Hub NDVI means match an independent odc-stac computation (guide §7.1) within 0.03."""
    odc_stac = pytest.importorskip("odc.stac")
    pystac_client = pytest.importorskip("pystac_client")
    area = area_from_id(GOLDEN_AREA_ID)
    obs, _, res = Sentinel2Adapter().observations(area, "ndvi", period_grid(date(2024, 4, 1), date(2024, 10, 1), "P1M"))
    summer = [o for o in obs if date(2024, 4, 1) <= o.day < date(2024, 10, 1) and o.valid_fraction > 0.95]
    clear = sorted(summer, key=lambda o: -o.valid_fraction)[:5]
    assert len(clear) == 5, "not enough clear summer acquisitions"
    from odc.geo.geom import Geometry

    from geomcp_core.areas import geometry_of

    polygon = Geometry(geometry_of(area).__geo_interface__, crs="EPSG:4326")
    catalog = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")
    for o in clear:
        items = catalog.search(
            collections=["sentinel-2-c1-l2a"], bbox=area.bbox, datetime=f"{o.day}/{o.day + timedelta(days=1)}"
        ).item_collection()
        assert len(items), f"Earth Search has no scene on {o.day}"
        # reference at native 10 m over exactly the same polygon
        ds = odc_stac.load(
            items, bands=["red", "nir", "scl"], geopolygon=polygon, crs="EPSG:32633", resolution=10, groupby="solar_day"
        ).isel(time=0)
        scaled = {}
        for band in ("red", "nir"):  # gotcha 1: read scale and offset from metadata, never hard-code them
            rb = items[0].assets[band].extra_fields.get("raster:bands", [{}])[0]
            scaled[band] = ds[band] * rb.get("scale", 1.0) + rb.get("offset", 0.0)
        ndvi = ((scaled["nir"] - scaled["red"]) / (scaled["nir"] + scaled["red"])).where(ds["scl"].isin([4, 5, 6]))
        local = float(ndvi.odc.mask(polygon).mean())
        print(f"{o.day}: Sentinel Hub {o.mean:.3f} at {res} m, Earth Search {local:.3f} at 10 m")
        assert abs(local - o.mean) < 0.03, (o.day, o.mean, local)


@pytest.mark.live
def test_worldcover_and_osm_for_the_golden_area() -> None:
    s = service.get_summary(GOLDEN_AREA_ID, ["landcover", "buildings"])
    landcover = next(f for f in s.facts if f.variable == "landcover").value
    assert isinstance(landcover, dict) and (landcover.get("built_up") or 0) > 0.5  # central Wrocław


@pytest.mark.live
def test_constraints_for_a_gmina_with_nature_mining_and_floods() -> None:
    from geomcp_core import constraints

    gmina = resolve_area("gmina Kąty Wrocławskie", 1000, "square")[1]
    assert gmina
    r = constraints.get_constraints(gmina)
    items = {i.key: i for i in r.items}
    assert all("SOPO" in g for g in r.gaps)  # landslides: PIG-PIB refuses queries (October 2026)
    assert any("Bystrzyc" in n for n in items["natura2000_habitats"].names)
    assert 0 < (items["flood_1pct"].share or 0) < 0.5 and items["mining_terrain"].count > 0


@pytest.mark.live
def test_climate_extremes_count_days_per_year() -> None:
    r = service.get_timeseries(GOLDEN_AREA_ID, ["hot_days", "frost_days"], date(2024, 1, 1), date(2025, 1, 1), "P1Y")
    values = {row[1]: row[2] for row in r.rows}
    assert 0 < values["hot_days"] < 60 and 20 < values["frost_days"] < 150


@pytest.mark.live
def test_smup_public_services_for_a_gmina() -> None:
    from geomcp_core import statistics

    found = [i for i in statistics.find_statistics("podatek od nieruchomości") if i.source == "GUS SMUP"]
    assert found
    r = statistics.get_statistics(GOLDEN_AREA_ID, [found[0].id])
    assert r.indicators[0].values.get("gmina")


@pytest.mark.live
def test_prices_zoning_traffic_and_orthophoto_around_smolec() -> None:
    from geomcp_core import prices, traffic, zoning

    smolec = "a_pt_51.0730_16.8990_sq2000"
    houses = next(s for s in prices.get_prices(smolec).series if s.kind == "houses")
    assert houses.years and all(200_000 < y.median < 5_000_000 for y in houses.years.values())
    z = zoning.get_zoning("a_pt_51.0730_16.8990_sq600")
    assert z.points_in_a_plan > 0 and any(d.symbol for d in z.designations)
    t = traffic.get_traffic(smolec)
    assert "35" in t.roads_in_area or "A4" in t.roads_in_area or t.notices
    assert service.get_snapshot(smolec, "orthophoto").pixel_size_m < 10  # 4 km at 512 px


@pytest.mark.live
def test_lookup_business_by_krs_regon_and_nip_agree() -> None:
    from geomcp_core import business

    by_krs = business.lookup_business("0000023302")  # KGHM Polska Miedź S.A.
    assert "KGHM" in by_krs.name and by_krs.nip and by_krs.regon
    assert business.lookup_business(by_krs.nip).krs == by_krs.krs == business.lookup_business(by_krs.regon).krs


@pytest.mark.live
def test_population_grid_and_building_functions_for_a_suburb() -> None:
    from geomcp_core.adapters.bdot10k import Bdot10kAdapter
    from geomcp_core.adapters.europe import GhslPopulationAdapter

    smolec = area_from_id("a_pt_51.0730_16.8990_sq2000")
    (pop,), _ = GhslPopulationAdapter().facts(smolec, ["population_grid"])
    assert isinstance(pop.value, dict) and (pop.value["2020"] or 0) > (pop.value["2000"] or 0) > 0  # it grew
    (b,), _ = Bdot10kAdapter().facts(smolec, ["building_functions"])
    assert isinstance(b.value, dict) and (b.value["residential"] or 0) > (b.value["industrial"] or 0) > 0
