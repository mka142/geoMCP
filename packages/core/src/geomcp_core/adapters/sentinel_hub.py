"""Sentinel-2, Sentinel-1, Sentinel-5P and Copernicus DEM through Sentinel Hub on CDSE.

Statistics are requested per calendar year with one value per acquisition (P1D intervals), cached per
year, and aggregated locally, so any cadence reuses the same cached acquisitions without new quota.
"""

import io
import math
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

import numpy as np
import tifffile
from numpy.typing import NDArray
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from geomcp_core import cache
from geomcp_core.adapters import cdse
from geomcp_core.aggregate import to_periods
from geomcp_core.areas import geometry_of, project, utm_epsg
from geomcp_core.models import (
    Area,
    Cost,
    Fact,
    GeomcpError,
    Health,
    Observation,
    Period,
    PeriodValue,
    Provenance,
    SeriesData,
    Snapshot,
    SourceInfo,
    VariableInfo,
)

TARGET_PIXELS = 15_000  # 10 km² -> 30 m at ~1/9 of the 10 m cost
# Sentinel Hub downsamples with NEAREST by default, which biased 30 m NDVI by -0.024 against 10 m in the live
# cross-check (one of nine pixels kept). BILINEAR brought it within 0.005 of the 10 m reference.
DOWNSAMPLING = {"downsampling": "BILINEAR", "upsampling": "BILINEAR"}
LADDER = (10, 20, 30, 40, 60, 100, 150, 200, 300, 500, 1000, 2000)
COPERNICUS_LICENCE = "Copernicus open data (CC-BY-4.0)"

_key_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_key_locks_guard = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks[key]


def stats_resolution(area_km2: float, native: float) -> int:
    wanted = max(native, math.sqrt(area_km2 * 1e6 / TARGET_PIXELS))
    return next((r for r in LADDER if r >= wanted), LADDER[-1])


def _utm(area: Area) -> tuple[BaseGeometry, int]:
    geom = geometry_of(area)
    c = geom.centroid
    epsg = utm_epsg(c.x, c.y)
    return project(geom, "EPSG:4326", f"EPSG:{epsg}"), epsg


def _bounds(geom: BaseGeometry, epsg: int) -> dict[str, Any]:
    return {"geometry": mapping(geom), "properties": {"crs": f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"}}


def _nan(x: Any) -> bool:
    return not isinstance(x, (int, float)) or math.isnan(x)


def _num(x: Any) -> float | None:
    return None if _nan(x) else float(x)


class _StatsAdapter:
    """Per-acquisition statistics through the Sentinel Hub Statistical API."""

    info: ClassVar[SourceInfo]
    data: ClassVar[dict[str, Any]]
    evalscript: ClassVar[str]
    script_version: ClassVar[str]
    outputs: ClassVar[dict[str, tuple[str, str]]]  # variable -> (output id, band)
    native_res: ClassVar[float]
    min_valid: ClassVar[float] = 0.0  # acquisitions with less valid area are not used
    chunk_months: ClassVar[int] = 12  # cache unit for settled years (12) or always monthly (1)
    chunk_seconds: ClassVar[float] = 20.0  # measured: one year of S2 statistics in 7-21 s for 10 km²
    scale: ClassVar[float] = 1.0  # applied to statistics (unit conversion)
    method: ClassVar[str]

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return ("ok", "") if cdse.configured() else ("unconfigured", cdse.unconfigured_hint())

    def _post(self, pv: PeriodValue) -> PeriodValue:
        return pv

    def _chunks(self, periods: list[Period], today: date) -> list[tuple[date, date]]:
        """Cache units: one per settled year; months for the current year (and always, for monthly sources)."""
        first = max(periods[0].start, self.info.coverage[0])
        last = min(periods[-1].end, today + timedelta(days=1))  # exclusive
        out: list[tuple[date, date]] = []
        for year in range(first.year, (last - timedelta(days=1)).year + 1):
            if self.chunk_months == 12 and year < today.year:
                out.append((date(year, 1, 1), date(year + 1, 1, 1)))
                continue
            for month in range(1, 13):
                c0 = date(year, month, 1)
                c1 = date(year + month // 12, month % 12 + 1, 1)
                if c1 > first and c0 < last:
                    out.append((c0, c1))
        return out

    def _key(self, area: Area, res: int, chunk: tuple[date, date]) -> str:
        return f"{self.info.id}|{self.script_version}|{area.area_id}|{res}m|{chunk[0]}|{chunk[1]}"

    def _fetch(
        self, area: Area, res: int, chunk: tuple[date, date], today: date
    ) -> tuple[dict[str, list[Observation]], float]:
        geom, epsg = _utm(area)
        end = min(chunk[1], today + timedelta(days=1))
        body = {
            "input": {"bounds": _bounds(geom, epsg), "data": [self.data]},
            "aggregation": {
                "timeRange": {"from": f"{chunk[0]}T00:00:00Z", "to": f"{end}T00:00:00Z"},
                "aggregationInterval": {"of": "P1D"},
                "evalscript": self.evalscript,
                "resx": res,
                "resy": res,
            },
            "calculations": {"default": {"statistics": {"default": {"percentiles": {"k": [10, 90]}}}}},
        }
        payload, units = cdse.statistics(body)
        out: dict[str, list[Observation]] = {v: [] for v in self.outputs}
        for interval in payload.get("data", []):
            if "outputs" not in interval:
                continue
            day = date.fromisoformat(interval["interval"]["from"][:10])
            for variable, (oid, band) in self.outputs.items():
                s = interval["outputs"][oid]["bands"][band]["stats"]
                n = s.get("sampleCount") or 0
                valid = 0.0 if not n or _nan(s.get("mean")) else 1 - (s.get("noDataCount") or 0) / n
                if valid <= 0:
                    out[variable].append(Observation(day=day, mean=0.0, valid_fraction=0.0))
                    continue
                pct: dict[str, Any] = s.get("percentiles") or {}
                p10, p90 = _num(pct.get("10.0")), _num(pct.get("90.0"))
                out[variable].append(
                    Observation(
                        day=day,
                        mean=s["mean"] * self.scale,
                        p10=None if p10 is None else p10 * self.scale,
                        p90=None if p90 is None else p90 * self.scale,
                        valid_fraction=round(valid, 4),
                    )
                )
        return out, units

    def _chunk(
        self, area: Area, res: int, chunk: tuple[date, date], today: date
    ) -> tuple[dict[str, list[Observation]], Cost]:
        key = self._key(area, res, chunk)
        with _lock(key):
            if (hit := cache.get(self.info.id, key)) is not None:
                return {v: [Observation.model_validate(o) for o in obs] for v, obs in hit.items()}, Cost(cache_hits=1)
            observations, units = self._fetch(area, res, chunk, today)
            dumped = {v: [o.model_dump(mode="json") for o in obs] for v, obs in observations.items()}
            cache.put(self.info.id, key, dumped, cache.ttl_for_chunk(chunk[1], today))
            return observations, Cost(cache_misses=1, sentinel_hub_units=units)

    def observations(self, area: Area, variable: str, periods: list[Period]) -> tuple[list[Observation], Cost, int]:
        """Per-acquisition observations inside the requested periods, the cost, and the statistics resolution."""
        today = datetime.now(UTC).date()
        res = stats_resolution(area.area_km2, self.native_res)
        cost = Cost()
        obs: list[Observation] = []
        with ThreadPoolExecutor(max_workers=cdse.MAX_PARALLEL) as pool:

            def one(chunk: tuple[date, date]) -> tuple[dict[str, list[Observation]], Cost]:
                return self._chunk(area, res, chunk, today)

            for chunk_obs, c in pool.map(one, self._chunks(periods, today)):
                obs.extend(o for o in chunk_obs[variable] if periods[0].start <= o.day < periods[-1].end)
                cost.add(c)
        return obs, cost, res

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        today = datetime.now(UTC).date()
        obs, cost, res = self.observations(area, variable, periods)
        values = to_periods(obs, periods, "mean", today, self.min_valid, preliminary_from=today)
        units = next(v.units for v in self.info.variables if v.name == variable)
        return SeriesData(
            variable=variable,
            units=units,
            values=[self._post(v) for v in values],
            cost=cost,
            provenance=Provenance(
                source=self.info.name,
                backend="sentinel-hub-statistical",
                collection=self.data["type"],
                method=self.method.format(res=res, min_valid=int(self.min_valid * 100)),
                licence=self.info.licence,
                attribution=self.info.attribution,
                commercial_safe=self.info.commercial_safe,
                retrieved_at=datetime.now(UTC),
            ),
        )

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        today = datetime.now(UTC).date()
        res = stats_resolution(area.area_km2, self.native_res)
        misses = sum(cache.get(self.info.id, self._key(area, res, c)) is None for c in self._chunks(periods, today))
        return math.ceil(misses / cdse.MAX_PARALLEL) * self.chunk_seconds


S2_EVALSCRIPT = """//VERSION=3
function setup() {
  return {input: [{bands: ["B02", "B03", "B04", "B08", "B11", "B12", "SCL", "dataMask"]}],
          output: [{id: "idx", bands: 6, sampleType: "FLOAT32"}, {id: "dataMask", bands: 1}]};
}
function nd(a, b) { return (a - b) / (a + b); }
function evaluatePixel(s) {
  const clear = [4, 5, 6].includes(s.SCL) ? 1 : 0;  // vegetation, bare soil, water
  return {idx: [nd(s.B08, s.B04), nd(s.B03, s.B08), nd(s.B03, s.B11), nd(s.B08, s.B11), nd(s.B08, s.B12),
                nd(s.B11, s.B08)],
          dataMask: [s.dataMask * clear]};
}"""


def _vars(topic: str, *items: tuple[str, str, str]) -> list[VariableInfo]:
    return [VariableInfo(name=n, units=u, topic=topic, description=d) for n, u, d in items]


class Sentinel2Adapter(_StatsAdapter):
    info = SourceInfo(
        id="sentinel2",
        name="Sentinel-2 L2A",
        variables=_vars(
            "vegetation",
            ("ndvi", "1", "Normalized Difference Vegetation Index (greenness)"),
            ("ndmi", "1", "Normalized Difference Moisture Index (vegetation water content)"),
            ("nbr", "1", "Normalized Burn Ratio (burn scars, disturbance)"),
        )
        + _vars(
            "water",
            ("ndwi", "1", "Normalized Difference Water Index (open water, McFeeters)"),
            ("mndwi", "1", "Modified NDWI (open water, robust in built-up areas)"),
        )
        + _vars("built-up", ("ndbi", "1", "Normalized Difference Built-up Index")),
        access_pattern="server_stats",
        resolution="10-20 m (statistics computed at an area-dependent resolution, see method)",
        coverage=(date(2016, 1, 1), None),
        latency="P1D",
        licence=COPERNICUS_LICENCE,
        attribution="Contains modified Copernicus Sentinel data",
        commercial_safe=True,
        auth_env=[cdse.ID_VAR, cdse.SECRET_VAR],
    )
    data: ClassVar[dict[str, Any]] = {"type": "sentinel-2-l2a", "processing": DOWNSAMPLING}
    evalscript = S2_EVALSCRIPT
    script_version = "s2idx_v2"  # v2: bilinear downsampling
    outputs: ClassVar[dict[str, tuple[str, str]]] = {
        v: ("idx", f"B{i}") for i, v in enumerate(["ndvi", "ndwi", "mndwi", "ndmi", "nbr", "ndbi"])
    }
    native_res = 10
    min_valid = 0.2
    method = (
        "per acquisition: mean over cloud-free pixels (SCL classes 4-6) at {res} m (bilinear); acquisitions with under "
        "{min_valid}% cloud-free area dropped; period value = mean of acquisition means"
    )


class Sentinel1Adapter(_StatsAdapter):
    info = SourceInfo(
        id="sentinel1",
        name="Sentinel-1 GRD (terrain-corrected gamma0)",
        variables=_vars(
            "radar",
            ("s1_vv", "dB", "Radar backscatter VV (surface roughness, moisture, structures)"),
            ("s1_vh", "dB", "Radar backscatter VH (vegetation volume)"),
        ),
        access_pattern="server_stats",
        resolution="10 m (statistics at an area-dependent resolution)",
        coverage=(date(2016, 1, 1), None),
        latency="P2D",
        licence=COPERNICUS_LICENCE,
        attribution="Contains modified Copernicus Sentinel data",
        commercial_safe=True,
        auth_env=[cdse.ID_VAR, cdse.SECRET_VAR],
    )
    data: ClassVar[dict[str, Any]] = {
        "type": "sentinel-1-grd",
        "dataFilter": {"acquisitionMode": "IW", "polarization": "DV", "orbitDirection": "ASCENDING"},
        "processing": {"backCoeff": "GAMMA0_TERRAIN", "orthorectify": True, "demInstance": "COPERNICUS"} | DOWNSAMPLING,
    }
    evalscript = """//VERSION=3
function setup() { return {input: [{bands: ["VV", "VH", "dataMask"]}],
  output: [{id: "sar", bands: 2, sampleType: "FLOAT32"}, {id: "dataMask", bands: 1}]}; }
function evaluatePixel(s) { return {sar: [s.VV, s.VH], dataMask: [s.dataMask]}; }"""
    script_version = "s1_v2"  # v2: bilinear downsampling
    outputs: ClassVar[dict[str, tuple[str, str]]] = {"s1_vv": ("sar", "B0"), "s1_vh": ("sar", "B1")}
    native_res = 10
    method = (
        "ascending orbits only; terrain-corrected gamma0 averaged in linear power at {res} m per acquisition "
        "and per period, then converted to dB"
    )

    def _post(self, pv: PeriodValue) -> PeriodValue:
        def db(x: float | None) -> float | None:
            return None if x is None or x <= 0 else round(10 * math.log10(x), 3)

        return pv.model_copy(update={"mean": db(pv.mean), "p10": db(pv.p10), "p90": db(pv.p90)})


class Sentinel5PAdapter(_StatsAdapter):
    info = SourceInfo(
        id="sentinel5p",
        name="Sentinel-5P TROPOMI L2 (offline)",
        variables=_vars(
            "air quality",
            ("no2_column", "umol m-2", "Tropospheric NO2 column (traffic, industry)"),
        ),
        access_pattern="server_stats",
        resolution="5.5 x 3.5 km",
        coverage=(date(2018, 5, 1), None),
        latency="P5D",
        licence=COPERNICUS_LICENCE,
        attribution="Contains modified Copernicus Sentinel data",
        commercial_safe=True,
        auth_env=[cdse.ID_VAR, cdse.SECRET_VAR],
    )
    data: ClassVar[dict[str, Any]] = {
        "type": "sentinel-5p-l2",
        "dataFilter": {"timeliness": "OFFL"},
        "processing": {"minQa": 75},
    }
    evalscript = """//VERSION=3
function setup() { return {input: [{bands: ["NO2", "dataMask"]}],
  output: [{id: "no2", bands: 1, sampleType: "FLOAT32"}, {id: "dataMask", bands: 1}]}; }
function evaluatePixel(s) { return {no2: [s.NO2], dataMask: [s.dataMask]}; }"""
    script_version = "s5p_no2_v1"
    outputs: ClassVar[dict[str, tuple[str, str]]] = {"no2_column": ("no2", "B0")}
    native_res = 1000
    min_valid = 0.5
    scale = 1e6  # mol m-2 -> umol m-2
    chunk_months = 1  # S5P is slow per day (measured ~22 s per month), so months run in parallel
    chunk_seconds = 25.0
    method = "daily OFFL overpasses with qa_value >= 0.75 at {res} m; days under {min_valid}% valid dropped"


DEM_EVALSCRIPT = """//VERSION=3
function setup() { return {input: ["DEM", "dataMask"], output: {bands: 2, sampleType: "FLOAT32"}}; }
function evaluatePixel(s) { return [s.DEM, s.dataMask]; }"""


class CopernicusDemAdapter:
    info = SourceInfo(
        id="copernicus_dem",
        name="Copernicus DEM GLO-90",
        variables=[
            VariableInfo(
                name="elevation",
                units="m",
                topic="terrain",
                description="Height above EGM2008 geoid",
                static=True,
            ),
            VariableInfo(name="slope", units="degree", topic="terrain", description="Terrain slope", static=True),
        ],
        access_pattern="server_stats",
        resolution="90 m (resampled to 30 m)",
        coverage=(date(2021, 1, 1), date(2021, 1, 1)),
        latency="P0D",
        licence="Copernicus DEM licence (free, attribution)",
        attribution="Copernicus DEM GLO-90, produced using Copernicus WorldDEM-90 © DLR e.V. 2010-2014 and "
        "© Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA",
        commercial_safe=True,
        auth_env=[cdse.ID_VAR, cdse.SECRET_VAR],
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return ("ok", "") if cdse.configured() else ("unconfigured", cdse.unconfigured_hint())

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError("static_variable", f"{variable} does not change over time.", "Use get_summary for it.")

    def grid(self, area: Area) -> tuple[NDArray[np.float32], int, Cost]:
        res = max(30, stats_resolution(area.area_km2, 30))
        key = f"dem|glo90|{area.area_id}|{res}"
        with _lock(key):
            if (hit := cache.get(self.info.id, key)) is not None:
                return np.array(hit, dtype=np.float32), res, Cost(cache_hits=1)
            geom, epsg = _utm(area)
            body = {
                "input": {"bounds": _bounds(geom, epsg), "data": [{"type": "dem"}]},
                "output": {
                    "resx": res,
                    "resy": res,
                    "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
                },
                "evalscript": DEM_EVALSCRIPT,
            }
            raw, units = cdse.process(body, "image/tiff")
            arr = np.asarray(tifffile.imread(io.BytesIO(raw)), dtype=np.float32)  # pyright: ignore[reportUnknownMemberType]
            dem = np.where(arr[..., 1] > 0, arr[..., 0], np.nan).astype(np.float32)
            rows: list[list[float]] = dem.tolist()
            cache.put(self.info.id, key, [[None if math.isnan(v) else v for v in row] for row in rows], None)
            return dem, res, Cost(cache_misses=1, sentinel_hub_units=units)

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        dem, res, cost = self.grid(area)
        prov = Provenance(
            source=self.info.name,
            backend="sentinel-hub-process",
            collection="dem (COPERNICUS_90)",
            method=f"GLO-90 resampled to {res} m; slope from central differences. Copernicus DEM is a surface "
            "model: buildings and forest canopy raise heights and steepen slopes in towns and woods",
            licence=self.info.licence,
            attribution=self.info.attribution,
            commercial_safe=True,
            retrieved_at=datetime.now(UTC),
        )
        valid = dem[~np.isnan(dem)]
        if valid.size == 0:
            raise GeomcpError("no_data", "The DEM has no data for this area.")
        facts: list[Fact] = []
        if "elevation" in variables:
            value = {
                "min": float(valid.min()),
                "p10": float(np.percentile(valid, 10)),
                "mean": float(valid.mean()),
                "p90": float(np.percentile(valid, 90)),
                "max": float(valid.max()),
            }
            facts.append(Fact(variable="elevation", units="m", value=_round(value), provenance=prov))
        if "slope" in variables:
            filled = np.where(np.isnan(dem), np.nanmean(dem), dem)
            gy, gx = np.gradient(filled, res)
            slope = np.degrees(np.arctan(np.hypot(gx, gy)))[~np.isnan(dem)]
            value = {
                "mean": float(slope.mean()),
                "p90": float(np.percentile(slope, 90)),
                "p99": float(np.percentile(slope, 99)),  # max is dominated by single noisy cells
            }
            facts.append(Fact(variable="slope", units="degree", value=_round(value), provenance=prov))
        return facts, cost


def _round(d: dict[str, float]) -> dict[str, float | None]:
    return {k: round(v, 2) for k, v in d.items()}


# --- Snapshots (Process API, PNG) ------------------------------------------------------------------

_RAMP_NDVI = (
    "[-0.2, 0, 0.2, 0.4, 0.6, 0.9]",
    "[[0.55,0.32,0.04],[0.85,0.7,0.4],[1,1,0.75],[0.65,0.85,0.4],[0.2,0.6,0.2],[0,0.35,0.1]]",
)
_RAMP_NDWI = (
    "[-0.6, -0.2, 0, 0.3, 0.6]",
    "[[0.6,0.45,0.3],[0.95,0.95,0.85],[0.7,0.85,1],[0.2,0.5,0.9],[0,0.2,0.6]]",
)
_DEM_COLOURS = "[[0.1,0.45,0.25],[0.45,0.7,0.35],[0.85,0.85,0.5],[0.8,0.6,0.35],[0.55,0.35,0.25],[0.95,0.95,0.95]]"


def _dem_ramp(lo: float, hi: float) -> tuple[str, str]:
    """Stretch the hypsometric colours over the area's own elevation range."""
    hi = max(hi, lo + 10)
    stops = [round(lo + (hi - lo) * f, 1) for f in (0, 0.2, 0.4, 0.6, 0.8, 1)]
    return str(stops), _DEM_COLOURS


def _ramp_script(bands: str, expr: str, ramp: tuple[str, str]) -> str:
    return f"""//VERSION=3
function setup() {{ return {{input: [{bands}], output: {{bands: 4}}}}; }}
function evaluatePixel(s) {{ const v = {expr};
  return [...colorBlend(v, {ramp[0]}, {ramp[1]}), s.dataMask]; }}"""


LAYERS: dict[str, dict[str, Any]] = {
    "true_color": {
        "data": {"type": "sentinel-2-l2a", "dataFilter": {"mosaickingOrder": "leastCC"}},
        "script": """//VERSION=3
function setup() { return {input: ["B02", "B03", "B04", "dataMask"], output: {bands: 4}}; }
function g(x) { return Math.pow(Math.max(0, 3 * x), 0.8); }
function evaluatePixel(s) { return [g(s.B04), g(s.B03), g(s.B02), s.dataMask]; }""",
        "legend": {"description": "natural colours (B4, B3, B2), gain 3, gamma 0.8"},
    },
    "false_color": {
        "data": {"type": "sentinel-2-l2a", "dataFilter": {"mosaickingOrder": "leastCC"}},
        "script": """//VERSION=3
function setup() { return {input: ["B03", "B04", "B08", "dataMask"], output: {bands: 4}}; }
function g(x) { return Math.pow(Math.max(0, 2.5 * x), 0.8); }
function evaluatePixel(s) { return [g(s.B08), g(s.B04), g(s.B03), s.dataMask]; }""",
        "legend": {"description": "near-infrared false colour (B8, B4, B3), gamma 0.8: vegetation appears red"},
    },
    "ndvi": {
        "data": {"type": "sentinel-2-l2a", "dataFilter": {"mosaickingOrder": "leastCC"}},
        "script": _ramp_script('"B04", "B08", "dataMask"', "(s.B08 - s.B04) / (s.B08 + s.B04)", _RAMP_NDVI),
        "legend": {"colormap": "brown-yellow-green", "range": [-0.2, 0.9], "units": "1"},
    },
    "ndwi": {
        "data": {"type": "sentinel-2-l2a", "dataFilter": {"mosaickingOrder": "leastCC"}},
        "script": _ramp_script('"B03", "B08", "dataMask"', "(s.B03 - s.B08) / (s.B03 + s.B08)", _RAMP_NDWI),
        "legend": {"colormap": "brown-white-blue", "range": [-0.6, 0.6], "units": "1"},
    },
    "sar_vv": {
        "data": {
            "type": "sentinel-1-grd",
            **{k: Sentinel1Adapter.data[k] for k in ("dataFilter", "processing")},
        },
        "script": """//VERSION=3
function setup() { return {input: ["VV", "dataMask"], output: {bands: 4}}; }
function evaluatePixel(s) { const v = Math.min(1, Math.max(0, (10 * Math.log10(s.VV) + 25) / 25));
  return [v, v, v, s.dataMask]; }""",
        "legend": {"colormap": "grey", "range": [-25, 0], "units": "dB"},
    },
    "elevation": {
        "data": {"type": "dem"},
        "script": "",  # built per area: the ramp is stretched to the area's elevation range
        "legend": {"colormap": "hypsometric", "units": "m"},
    },
}


def _pick_scene(collection: str, bbox: tuple[float, float, float, float], around: date) -> tuple[date, float | None]:
    start, end = around - timedelta(days=15), around + timedelta(days=16)
    feats = cdse.catalog_search(collection, bbox, start, end)
    if not feats:
        raise GeomcpError("no_data", f"No {collection} scene within 15 days of {around}.", "Try another date.")

    def score(f: dict[str, Any]) -> tuple[float, int]:
        d = date.fromisoformat(f["properties"]["datetime"][:10])
        return (f["properties"].get("eo:cloud_cover") or 0.0, abs((d - around).days))

    best = min(feats, key=score)
    return date.fromisoformat(best["properties"]["datetime"][:10]), best["properties"].get("eo:cloud_cover")


def snapshot(area: Area, layer: str, when: date | None, size_px: int) -> Snapshot:
    if layer not in LAYERS:
        raise GeomcpError(
            "unsupported_layer", f"Unknown layer {layer!r}.", hint=f"Use one of: {', '.join(sorted(LAYERS))}."
        )
    spec = dict(LAYERS[layer])
    if layer == "elevation":
        dem, _, _ = CopernicusDemAdapter().grid(area)  # cached after the first call
        lo, hi = float(np.nanpercentile(dem, 1)), float(np.nanpercentile(dem, 99))
        spec["script"] = _ramp_script('"DEM", "dataMask"', "s.DEM", _dem_ramp(lo, hi))
        spec["legend"] = spec["legend"] | {"range": [round(lo), round(hi)]}
    # Web Mercator renders north-up like a map; ground pixel size = mercator size x cos(latitude).
    wgs84 = geometry_of(area)
    geom, epsg = project(wgs84, "EPSG:4326", "EPSG:3857"), 3857
    ground = math.cos(math.radians(wgs84.centroid.y))
    minx, miny, maxx, maxy = geom.bounds
    longest = max(maxx - minx, maxy - miny)
    native = 30.0 if layer == "elevation" else 10.0
    px = max(16, min(size_px, int(longest * ground / native)))
    width = max(1, round(px * (maxx - minx) / longest))
    height = max(1, round(px * (maxy - miny) / longest))
    today = datetime.now(UTC).date()
    scene: date | None = None
    cloud: float | None = None
    data = dict(spec["data"])
    if data["type"] != "dem":
        scene, cloud = _pick_scene(data["type"], area.bbox, when or today - timedelta(days=16))
        data["dataFilter"] = dict(data.get("dataFilter", {})) | {
            "timeRange": {"from": f"{scene}T00:00:00Z", "to": f"{scene + timedelta(days=1)}T00:00:00Z"}
        }
    body = {
        "input": {"bounds": _bounds(geom, epsg), "data": [data]},
        "output": {
            "width": width,
            "height": height,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": spec["script"],
    }
    png, units = cdse.process(body, "image/png")
    return Snapshot(
        layer=layer,
        png=png,
        scene_date=scene,
        requested_date=when,
        scene_cloud_cover=None if cloud is None else round(float(cloud) / 100, 3),
        size_px=(width, height),
        pixel_size_m=round(longest * ground / px, 1),
        legend=spec["legend"],
        units_spent=units,
        provenance=Provenance(
            source="Copernicus DEM GLO-90" if layer == "elevation" else data["type"],
            backend="sentinel-hub-process",
            collection=data["type"],
            method="least-cloudy scene within 15 days of the requested date" if scene else "static elevation model",
            licence=COPERNICUS_LICENCE,
            attribution="Contains modified Copernicus Sentinel data"
            if scene
            else CopernicusDemAdapter.info.attribution,
            commercial_safe=True,
            retrieved_at=datetime.now(UTC),
        ),
    )
