# pyright: basic
# rasterio ships without type information; this module is the typed boundary around it.
"""ESA WorldCover 2021 land cover (10 m) read directly from its cloud-optimised GeoTIFFs on AWS Open Data."""

import math
from datetime import UTC, date, datetime
from typing import ClassVar

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds

from geomcp_core import cache
from geomcp_core.areas import geometry_of
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

TILE_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)
CLASSES = {
    10: "tree_cover",
    20: "shrubland",
    30: "grassland",
    40: "cropland",
    50: "built_up",
    60: "bare_sparse_vegetation",
    70: "snow_ice",
    80: "permanent_water",
    90: "herbaceous_wetland",
    95: "mangroves",
    100: "moss_lichen",
}


def _tiles(bounds: tuple[float, float, float, float]) -> list[str]:
    """WorldCover tiles are 3 x 3 degrees, named after their south-west corner (e.g. N51E015)."""
    minx, miny, maxx, maxy = bounds
    out: list[str] = []
    for lat in range(math.floor(miny / 3) * 3, math.floor(maxy / 3) * 3 + 1, 3):
        for lon in range(math.floor(minx / 3) * 3, math.floor(maxx / 3) * 3 + 1, 3):
            ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
            out.append(f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}")
    return out


class WorldCoverAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="worldcover",
        name="ESA WorldCover 2021 v200",
        variables=[
            VariableInfo(
                name="landcover",
                units="fraction",
                topic="land cover",
                description="Share of the area in each land-cover class (tree cover, cropland, built-up, water, ...)",
                static=True,
            )
        ],
        access_pattern="pixels",
        resolution="10 m",
        coverage=(date(2021, 1, 1), date(2021, 12, 31)),
        latency="P0D",
        licence="CC-BY-4.0",
        attribution="© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by "
        "ESA WorldCover consortium",
        commercial_safe=True,
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError("static_variable", "Land cover is a 2021 snapshot.", "Use get_summary for it.")

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        return 0.0 if cache.get(self.info.id, f"worldcover|v200|{area.area_id}") is not None else 8.0

    def _shares(self, area: Area) -> tuple[dict[str, float], Cost]:
        key = f"worldcover|v200|{area.area_id}"
        if (hit := cache.get(self.info.id, key)) is not None:
            return hit, Cost(cache_hits=1)
        geom = geometry_of(area)
        counts: dict[int, int] = {}
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
            for tile in _tiles(geom.bounds):
                try:
                    src = rasterio.open(TILE_URL.format(tile=tile))
                except rasterio.RasterioIOError:
                    continue  # no tile over open ocean
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
                    values, n = np.unique(arr[inside], return_counts=True)
                    for v, c in zip(values.tolist(), n.tolist(), strict=True):
                        counts[int(v)] = counts.get(int(v), 0) + int(c)
        total = sum(c for v, c in counts.items() if v in CLASSES)
        if not total:
            raise GeomcpError("no_data", "WorldCover has no data for this area.")
        shares = {
            CLASSES[v]: round(c / total, 4) for v, c in sorted(counts.items(), key=lambda x: -x[1]) if v in CLASSES
        }
        cache.put(self.info.id, key, shares, None)
        return shares, Cost(cache_misses=1)

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        shares, cost = self._shares(area)
        prov = Provenance(
            source=self.info.name,
            backend="cloud-optimised GeoTIFF (AWS Open Data)",
            collection="esa-worldcover v200 (2021)",
            method="share of 10 m pixels inside the area polygon per class",
            licence=self.info.licence,
            attribution=self.info.attribution,
            commercial_safe=True,
            retrieved_at=datetime.now(UTC),
        )
        value: dict[str, float | None] = {k: v for k, v in shares.items()}
        return [Fact(variable="landcover", units="fraction", value=value, period="2021", provenance=prov)], cost
