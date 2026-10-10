# pyright: basic
# rasterio ships without type information; this module is a typed boundary around it.
"""GUGiK NMT: Poland's official 1 m digital terrain model (bare ground, heights in PL-KRON86-NH).

Requested through WCS 1.0.0 in EPSG:2180 with the bounding box in easting, northing order, as the
coverage description publishes it (guide §10 gotcha 7). The grid is capped near one million pixels, so
small areas get 1 m cells and a gmina gets about 13 m. Requests are tiled at 4 km, because the service
silently returns zeros for larger extents; it also drops connections at times, which retries cover.
"""

import io
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import ClassVar

import numpy as np
import tifffile
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds
from shapely.geometry import mapping

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

WCS = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF"
MAX_PIXELS = 1_000_000
TILE_M = 4000.0  # larger extents come back partly filled with zeros, so requests are tiled
MAX_AREA_KM2 = 250.0
POLAND_2180 = (160_000.0, 98_000.0, 877_000.0, 797_000.0)  # coverage envelope (easting, northing)


class GugikNmtAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="gugik_nmt",
        name="GUGiK NMT (digital terrain model, Poland)",
        variables=[
            VariableInfo(
                name="elevation_ground",
                units="m",
                topic="terrain",
                description="Bare-ground height above sea level (Kronstadt 86), 1 m model; Poland only",
                static=True,
            ),
            VariableInfo(
                name="slope_ground",
                units="degree",
                topic="terrain",
                description="Bare-ground slope; Poland only",
                static=True,
            ),
        ],
        access_pattern="pixels",
        resolution="1 m (coarser for large areas, see method)",
        coverage=(date(2019, 1, 1), None),
        latency="P1Y",
        licence="GUGiK open data (free for any use)",
        attribution="Główny Urząd Geodezji i Kartografii (GUGiK), NMT",
        commercial_safe=True,
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError("static_variable", f"{variable} does not change over time.", "Use get_summary for it.")

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        cached = any(cache.get(self.info.id, f"nmt-v2|{area.area_id}|{c}") is not None for c in range(1, 60))
        return 0.0 if cached else 15.0 + 6.0 * math.ceil(area.area_km2 / 16 / 4)  # 4 km tiles, 4 at a time

    def grid(self, area: Area) -> tuple[np.ndarray, float, Cost]:
        if area.area_km2 > MAX_AREA_KM2:
            raise GeomcpError("area_too_large", f"The 1 m terrain model is limited to {MAX_AREA_KM2:.0f} km².")
        geom = project(geometry_of(area), "EPSG:4326", "EPSG:2180")
        minx, miny, maxx, maxy = geom.bounds
        if not (
            POLAND_2180[0] <= minx and maxx <= POLAND_2180[2] and POLAND_2180[1] <= miny and maxy <= POLAND_2180[3]
        ):
            raise GeomcpError("outside_poland", "GUGiK NMT covers Poland only.", "Use elevation (Copernicus DEM).")
        cell = max(1.0, math.ceil(math.sqrt((maxx - minx) * (maxy - miny) / MAX_PIXELS)))
        width, height = max(1, math.ceil((maxx - minx) / cell)), max(1, math.ceil((maxy - miny) / cell))
        maxx, miny = minx + width * cell, maxy - height * cell  # snap the extent to whole cells
        key = f"nmt-v2|{area.area_id}|{cell:g}"
        if (hit := cache.get(self.info.id, key)) is not None:
            return np.array(hit, dtype=np.float32), cell, Cost(cache_hits=1)
        arr = np.full((height, width), np.nan, dtype=np.float32)
        tile_px = max(1, int(TILE_M // cell))
        tiles = [(r, c) for r in range(0, height, tile_px) for c in range(0, width, tile_px)]

        def fetch(rc: tuple[int, int]) -> tuple[int, int, np.ndarray]:
            r, c = rc
            h, w = min(tile_px, height - r), min(tile_px, width - c)
            x0, y1 = minx + c * cell, maxy - r * cell
            params = {
                "SERVICE": "WCS",
                "VERSION": "1.0.0",
                "REQUEST": "GetCoverage",
                "COVERAGE": "DTM_PL-KRON86-NH_TIFF",
                "CRS": "EPSG:2180",
                # easting, northing order (see module docstring)
                "BBOX": f"{x0:.2f},{y1 - h * cell:.2f},{x0 + w * cell:.2f},{y1:.2f}",
                "WIDTH": w,
                "HEIGHT": h,
                "FORMAT": "image/tiff",
            }
            with http.client(60) as client:
                raw = http.with_retries(
                    lambda: http.check(client.get(WCS, params=params)).content, attempts=5, timeout_s=300
                )
            try:
                tile = np.asarray(tifffile.imread(io.BytesIO(raw)), dtype=np.float32)
            except Exception as e:  # the service answers some failures with an XML document
                raise GeomcpError("source_error", f"GUGiK NMT returned no raster: {raw[:120]!r}") from e
            return r, c, tile

        with ThreadPoolExecutor(max_workers=4) as pool:
            for r, c, tile in pool.map(fetch, tiles):
                arr[r : r + tile.shape[0], c : c + tile.shape[1]] = tile[: height - r, : width - c]
        width, height = arr.shape[1], arr.shape[0]
        inside = geometry_mask(
            [mapping(geom)],
            out_shape=arr.shape,
            transform=from_bounds(minx, miny, maxx, maxy, width, height),
            invert=True,
        )
        # the service marks missing data with 0.0 (no nodata tag); real heights near 0 m are rare coastal depressions
        dem = np.where(inside & (arr != 0) & (arr > -100), arr, np.nan).astype(np.float32)
        cache.put(self.info.id, key, [[None if math.isnan(v) else v for v in row] for row in dem.tolist()], None)
        return dem, cell, Cost(cache_misses=1)

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        dem, cell, cost = self.grid(area)
        valid = dem[~np.isnan(dem)]
        if valid.size == 0:
            raise GeomcpError("no_data", "GUGiK NMT has no data for this area.")
        prov = Provenance(
            source=self.info.name,
            backend="gugik-wcs",
            collection="DTM_PL-KRON86-NH",
            method=f"bare-ground model at {cell:g} m cells inside the area; heights in PL-KRON86-NH (differ from "
            "EGM2008 by a few decimetres; do not mix with Copernicus DEM)",
            licence=self.info.licence,
            attribution=self.info.attribution,
            commercial_safe=True,
            retrieved_at=datetime.now(UTC),
        )
        facts: list[Fact] = []
        if "elevation_ground" in variables:
            pct = np.percentile(valid, [10, 90])
            value: dict[str, float | None] = {
                "min": round(float(valid.min()), 2),
                "p10": round(float(pct[0]), 2),
                "mean": round(float(valid.mean()), 2),
                "p90": round(float(pct[1]), 2),
                "max": round(float(valid.max()), 2),
            }
            facts.append(Fact(variable="elevation_ground", units="m", value=value, provenance=prov))
        if "slope_ground" in variables:
            filled = np.where(np.isnan(dem), np.nanmean(dem), dem)
            gy, gx = np.gradient(filled, cell)
            slope = np.degrees(np.arctan(np.hypot(gx, gy)))[~np.isnan(dem)]
            value = {
                "mean": round(float(slope.mean()), 2),
                "p90": round(float(np.percentile(slope, 90)), 2),
                "p99": round(float(np.percentile(slope, 99)), 2),  # max is dominated by embankments and walls
            }
            facts.append(Fact(variable="slope_ground", units="degree", value=value, provenance=prov))
        return facts, cost
