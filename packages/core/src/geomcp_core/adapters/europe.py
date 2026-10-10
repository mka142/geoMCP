# pyright: basic
# rasterio ships without type information; this module is the typed boundary around it.
"""Europe-wide (and global) facts for get_summary, all keyless:

- population: JRC GHSL GHS-POP R2023A, 100 m, read by window from zipped tiles over HTTP (2000-2025);
- solar potential: JRC PVGIS 5.3 (yield per kWp, optimal and flat panels);
- land-cover history: CORINE Land Cover 1990-2018 from the EEA map services, shares per year;
- climate projections: Open-Meteo Climate API (CMIP6 HighResMIP, downscaled to 10 km), 2031-2050 against
  1991-2010; free for non-commercial use only, so flagged commercial_safe=False.
"""

import json
import math
from datetime import UTC, date, datetime
from statistics import fmean
from typing import Any, ClassVar

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from shapely.geometry import Polygon

from geomcp_core import cache, http
from geomcp_core.areas import geometry_of, project
from geomcp_core.models import (
    Area,
    Cost,
    Fact,
    GeomcpError,
    Health,
    Period,
    Provenance,
    SeriesData,
    SourceInfo,
    VariableInfo,
)

STATIC_TTL_S = 365 * 24 * 3600


class _Facts:
    """Shared plumbing for sources that only answer get_summary."""

    info: ClassVar[SourceInfo]
    estimate: ClassVar[float] = 5.0

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError("static_variable", f"{variable} is a fixed set of values.", "Use get_summary for it.")

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        return self.estimate

    def _provenance(self, collection: str, method: str) -> Provenance:
        return Provenance(
            source=self.info.name,
            backend=self.info.access_pattern,
            collection=collection,
            method=method,
            licence=self.info.licence,
            attribution=self.info.attribution,
            commercial_safe=self.info.commercial_safe,
            retrieved_at=datetime.now(UTC),
        )


# --- population ----------------------------------------------------------------------------------------------

GHSL = (
    "/vsizip//vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
    "GHS_POP_E{ep}_GLOBE_R2023A_54009_100/V1-0/tiles/GHS_POP_E{ep}_GLOBE_R2023A_54009_100_V1_0_R{r}_C{c}.zip/"
    "GHS_POP_E{ep}_GLOBE_R2023A_54009_100_V1_0_R{r}_C{c}.tif"
)
EPOCHS = (2000, 2010, 2020, 2025)
MOLLWEIDE = "ESRI:54009"


def _ghsl_tiles(bounds: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """GHSL 100 m tiles are 1,000 km squares in Mollweide, numbered from the top-left corner."""
    minx, miny, maxx, maxy = bounds
    cols = range(math.floor((minx + 18_041_000) / 1e6) + 1, math.floor((maxx + 18_041_000) / 1e6) + 2)
    rows = range(math.floor((9e6 - maxy) / 1e6) + 1, math.floor((9e6 - miny) / 1e6) + 2)
    return [(r, c) for r in rows for c in cols]


class GhslPopulationAdapter(_Facts):
    estimate = 15.0  # 4 epochs; up to 25 s for a 30 km² city area
    info: ClassVar[SourceInfo] = SourceInfo(
        id="ghsl_population",
        name="JRC GHSL GHS-POP R2023A (100 m)",
        variables=[
            VariableInfo(
                name="population_grid",
                units="persons",
                topic="population",
                description="Residents inside the area itself (not the whole gmina), 2000, 2010, 2020 and 2025, "
                "from a 100 m population grid",
                static=True,
            )
        ],
        access_pattern="pixels",
        resolution="100 m",
        coverage=(date(2000, 1, 1), date(2025, 12, 31)),
        latency="P5Y",
        licence="CC-BY-4.0",
        attribution="European Commission, Joint Research Centre (JRC), GHS-POP R2023A",
        commercial_safe=True,
    )

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        key = f"ghsl|v1|{area.area_id}"
        cost = Cost(cache_hits=1)
        if (totals := cache.get(self.info.id, key)) is None:
            cost = Cost(cache_misses=1)
            geom = project(geometry_of(area), "EPSG:4326", MOLLWEIDE)
            totals = {}
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
                for epoch in EPOCHS:
                    total = 0.0
                    for r, c in _ghsl_tiles(geom.bounds):
                        try:
                            src = rasterio.open(GHSL.format(ep=epoch, r=r, c=c))
                        except rasterio.RasterioIOError:
                            continue  # no tile over open sea
                        with src:
                            window = from_bounds(*geom.bounds, transform=src.transform).intersection(
                                Window.from_slices((0, src.height), (0, src.width))
                            )
                            window = window.round_offsets().round_lengths()
                            if window.width < 1 or window.height < 1:
                                continue
                            arr = src.read(1, window=window)
                            inside = geometry_mask(
                                [geom.__geo_interface__],
                                out_shape=arr.shape,
                                transform=src.window_transform(window),
                                invert=True,
                            )
                            total += float(arr[inside & (arr > 0)].sum())  # nodata is -200
                    totals[str(epoch)] = round(total)
            cache.put(self.info.id, key, totals, STATIC_TTL_S)
        method = "sum of 100 m cells whose centre lies inside the area; 2025 is a model projection, 2020 the last "
        method += "epoch based on census inputs"
        return [
            Fact(
                variable="population_grid",
                units="persons",
                value={k: float(v) for k, v in totals.items()},
                period="2000/2025",
                provenance=self._provenance("GHS_POP_E{2000,2010,2020,2025}_GLOBE_R2023A_54009_100", method),
            )
        ], cost


# --- solar potential -----------------------------------------------------------------------------------------

PVGIS = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"


class PvgisAdapter(_Facts):
    estimate = 3.0
    info: ClassVar[SourceInfo] = SourceInfo(
        id="pvgis",
        name="JRC PVGIS 5.3",
        variables=[
            VariableInfo(
                name="solar_potential",
                units="kWh per kWp per year",
                topic="energy",
                description="Yearly PV yield of 1 kWp (crystalline silicon, 14% losses) at the optimal tilt and "
                "flat, the optimal tilt, and yearly irradiation on the optimally tilted plane (kWh/m²)",
                static=True,
            )
        ],
        access_pattern="server_stats",
        resolution="SARAH3 satellite radiation, about 5 km",
        coverage=(date(2005, 1, 1), date(2023, 12, 31)),
        latency="P1Y",
        licence="CC-BY-4.0",
        attribution="European Commission, Joint Research Centre, PVGIS",
        commercial_safe=True,
    )

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        c = geometry_of(area).centroid
        key = f"pvgis|v1|{c.y:.3f}|{c.x:.3f}"
        cost = Cost(cache_hits=1)
        if (value := cache.get(self.info.id, key)) is None:
            cost = Cost(cache_misses=1)
            base = {"lat": round(c.y, 4), "lon": round(c.x, 4), "peakpower": 1, "loss": 14, "outputformat": "json"}
            with http.client(60) as client:
                runs = {}
                for label, extra in (("optimal", {"optimalangles": 1}), ("flat", {"angle": 0})):
                    r = http.with_retries(lambda p=base | extra: http.raise_transient(client.get(PVGIS, params=p)))
                    if r.status_code == 400:
                        raise GeomcpError("out_of_coverage", f"PVGIS: {r.json().get('message', r.text[:120])}")
                    runs[label] = http.json_of(r)
            opt, flat = runs["optimal"], runs["flat"]
            value = {
                "yield_optimal_kwh_per_kwp": round(opt["outputs"]["totals"]["fixed"]["E_y"], 1),
                "yield_flat_kwh_per_kwp": round(flat["outputs"]["totals"]["fixed"]["E_y"], 1),
                "optimal_tilt_deg": float(opt["inputs"]["mounting_system"]["fixed"]["slope"]["value"]),
                "irradiation_optimal_kwh_m2": round(opt["outputs"]["totals"]["fixed"]["H(i)_y"], 1),
            }
            cache.put(self.info.id, key, value, STATIC_TTL_S)
        method = "PVcalc at the area centroid, 1 kWp free-standing crystalline silicon, 14% system losses, "
        method += "radiation 2005-2023 (PVGIS-SARAH3)"
        return [
            Fact(
                variable="solar_potential",
                units="kWh per kWp per year",
                value=value,
                period="2005/2023",
                provenance=self._provenance("PVGIS-SARAH3", method),
            )
        ], cost


# --- land-cover history --------------------------------------------------------------------------------------

CLC = "https://image.discomap.eea.europa.eu/arcgis/rest/services/Corine/{service}/MapServer/0/query"
CLC_YEARS = {  # only 2018 has a _LAEA name; the older services are EPSG:3035 too
    "1990": ("CLC1990", "code_90"),
    "2000": ("CLC2000", "code_00"),
    "2006": ("CLC2006", "Code_06"),
    "2012": ("CLC2012", "Code_12"),
    "2018": ("CLC2018_LAEA", "Code_18"),
}
CLC_GROUPS = {  # CORINE codes by first digits
    "urban_fabric": ("11",),
    "industry_transport": ("12",),
    "mines_construction": ("13",),
    "urban_green_sport": ("14",),
    "agricultural": ("2",),
    "forest_seminatural": ("3",),
    "wetlands": ("4",),
    "water": ("5",),
}
EUROPE = (-31.3, 27.6, 44.9, 71.2)


def _clc_group(code: str) -> str | None:
    return next((g for g, prefixes in CLC_GROUPS.items() if code.startswith(prefixes)), None)


class CorineAdapter(_Facts):
    estimate = 25.0  # five yearly services, about 4 s each
    info: ClassVar[SourceInfo] = SourceInfo(
        id="corine",
        name="CORINE Land Cover 1990-2018 (EEA)",
        variables=[
            VariableInfo(
                name="landcover_history",
                units="fraction",
                topic="land cover",
                description="Share of the area per land-cover group in 1990, 2000, 2006, 2012 and 2018 (urban "
                "fabric, industry and transport, mines and construction, urban green, agricultural, forest, "
                "wetlands, water); keys are group_year",
                static=True,
            )
        ],
        access_pattern="server_stats",
        resolution="25 ha minimum mapping unit (100 m)",
        coverage=(date(1990, 1, 1), date(2018, 12, 31)),
        latency="P6Y",
        licence="Copernicus Land Monitoring Service data policy (free, attribution)",
        attribution="© European Union, Copernicus Land Monitoring Service, European Environment Agency (EEA)",
        commercial_safe=True,
    )

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        from shapely.geometry import shape

        wgs = geometry_of(area)
        c = wgs.centroid
        if not (EUROPE[0] <= c.x <= EUROPE[2] and EUROPE[1] <= c.y <= EUROPE[3]):
            raise GeomcpError("out_of_coverage", "CORINE Land Cover covers Europe only.")
        key = f"corine|v3|{area.area_id}"
        cost = Cost(cache_hits=1)
        if (shares := cache.get(self.info.id, key)) is None:
            cost = Cost(cache_misses=1)
            geom = project(wgs, "EPSG:4326", "EPSG:3035")
            hull = geom.convex_hull.simplify(50)  # features are clipped to the real polygon below
            ring = [[round(x), round(y)] for x, y in hull.exterior.coords] if isinstance(hull, Polygon) else []
            shares = {}
            with http.client(120) as client:
                for year, (service, field) in CLC_YEARS.items():
                    params = {
                        "geometry": json.dumps({"rings": [ring], "spatialReference": {"wkid": 3035}}),
                        "geometryType": "esriGeometryPolygon",
                        "inSR": 3035,
                        "outSR": 3035,
                        "spatialRel": "esriSpatialRelIntersects",
                        "outFields": field,
                        "returnGeometry": "true",
                        "maxAllowableOffset": 25,  # 25 m generalisation: well below the 100 m mapping unit
                        "geometryPrecision": 0,
                        "f": "geojson",
                    }
                    url = CLC.format(service=service)
                    payload: dict[str, Any] = http.with_retries(
                        lambda u=url, p=params: http.json_of(client.post(u, data=p))
                    )
                    groups: dict[str, float] = {}
                    for f in payload.get("features", []):
                        group = _clc_group(str(f["properties"].get(field) or ""))
                        if group and f.get("geometry"):
                            part = shape(f["geometry"]).buffer(0).intersection(geom)
                            groups[group] = groups.get(group, 0.0) + part.area
                    for group, a in groups.items():
                        shares[f"{group}_{year}"] = round(a / geom.area, 4)
            if not shares:
                raise GeomcpError("no_data", "CORINE has no polygons for this area.")
            cache.put(self.info.id, key, shares, STATIC_TTL_S)
        method = "CORINE polygons intersected with the area (EPSG:3035), area share per group and year; changes "
        method += "under 5 ha are not mapped, so small shifts are noise"
        return [
            Fact(
                variable="landcover_history",
                units="fraction",
                value={k: float(v) for k, v in shares.items()},
                period="1990/2018",
                provenance=self._provenance("CLC 1990, 2000, 2006, 2012, 2018", method),
            )
        ], cost


# --- climate projections -------------------------------------------------------------------------------------

OPEN_METEO = "https://climate-api.open-meteo.com/v1/climate"
MODELS = ("EC_Earth3P_HR", "MPI_ESM1_2_XR", "MRI_AGCM3_2_S")
BASELINE, FUTURE = (1991, 2010), (2031, 2050)


class ClimateProjectionAdapter(_Facts):
    estimate = 4.0
    info: ClassVar[SourceInfo] = SourceInfo(
        id="climate_projection",
        name="Open-Meteo Climate API (CMIP6 HighResMIP, downscaled)",
        variables=[
            VariableInfo(
                name="climate_projection",
                units="mixed",
                topic="climate",
                description="Projected climate 2031-2050 against 1991-2010, mean of three CMIP6 models: mean "
                "daily maximum temperature (°C), hot days per year (max 30 °C or more), annual precipitation "
                "(mm), and their changes",
                static=True,
            )
        ],
        access_pattern="timeseries",
        resolution="10 km (downscaled)",
        coverage=(date(1950, 1, 1), date(2050, 12, 31)),
        latency="P0D",
        licence="CC-BY-4.0 data; Open-Meteo free API for non-commercial use only",
        attribution="Open-Meteo.com; CMIP6 HighResMIP models EC-Earth3P-HR, MPI-ESM1-2-XR, MRI-AGCM3-2-S",
        commercial_safe=False,
    )

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        c = geometry_of(area).centroid
        key = f"climate|v1|{c.y:.2f}|{c.x:.2f}"
        cost = Cost(cache_hits=1)
        if (value := cache.get(self.info.id, key)) is None:
            cost = Cost(cache_misses=1)
            stats: dict[str, dict[str, list[float]]] = {}
            with http.client(60) as client:
                for label, (y0, y1) in (("baseline", BASELINE), ("future", FUTURE)):
                    params = {
                        "latitude": round(c.y, 3),
                        "longitude": round(c.x, 3),
                        "start_date": f"{y0}-01-01",
                        "end_date": f"{y1}-12-31",
                        "models": ",".join(MODELS),
                        "daily": "temperature_2m_max,precipitation_sum",
                    }
                    daily = http.with_retries(lambda p=params: http.json_of(client.get(OPEN_METEO, params=p)))["daily"]
                    years = y1 - y0 + 1
                    per_model: dict[str, list[float]] = {"tmax": [], "hot": [], "precip": []}
                    for m in MODELS:
                        tmax = np.array([v for v in daily.get(f"temperature_2m_max_{m}", []) if v is not None])
                        rain = [v for v in daily.get(f"precipitation_sum_{m}", []) if v is not None]
                        if len(tmax) < 300 * years:
                            continue  # a model with gaps would bias the ensemble
                        per_model["tmax"].append(float(tmax.mean()))
                        per_model["hot"].append(float((tmax >= 30).sum()) / years)
                        per_model["precip"].append(sum(rain) / years)
                    stats[label] = per_model
            b, f = stats["baseline"], stats["future"]
            if not (b["tmax"] and f["tmax"]):
                raise GeomcpError("no_data", "The climate models returned no complete series here.")
            value = {
                "tmax_mean_1991_2010": round(fmean(b["tmax"]), 1),
                "tmax_mean_2031_2050": round(fmean(f["tmax"]), 1),
                "tmax_change": round(fmean(f["tmax"]) - fmean(b["tmax"]), 1),
                "hot_days_1991_2010": round(fmean(b["hot"]), 1),
                "hot_days_2031_2050": round(fmean(f["hot"]), 1),
                "precip_mm_1991_2010": round(fmean(b["precip"])),
                "precip_mm_2031_2050": round(fmean(f["precip"])),
                "precip_change_pct": round(100 * (fmean(f["precip"]) / fmean(b["precip"]) - 1), 1),
                "models": float(min(len(b["tmax"]), len(f["tmax"]))),
            }
            cache.put(self.info.id, key, value, STATIC_TTL_S)
        method = "mean of three CMIP6 HighResMIP models (no scenario choice; close to a high-emission pathway "
        method += "until 2050), downscaled to 10 km against ERA5-Land, at the area centroid"
        return [
            Fact(
                variable="climate_projection",
                units="mixed (°C, days per year, mm per year)",
                value=value,
                period="2031/2050",
                provenance=self._provenance(", ".join(MODELS), method),
            )
        ], cost
