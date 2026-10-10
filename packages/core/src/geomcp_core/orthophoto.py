"""GUGiK aerial orthophoto for Polish areas (25 cm to 1 m), drawn north-up as a snapshot layer.

The high-resolution WMS does not cover all of Poland; blank renders fall back to the standard service.
"""

import io
import math
from datetime import UTC, datetime

import numpy as np
from PIL import Image

from geomcp_core import http
from geomcp_core.areas import geometry_of
from geomcp_core.models import Area, GeomcpError, Provenance, Snapshot

SERVICES = (
    "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/HighResolution",
    "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution",
)
NATIVE_M = 0.25
POLAND = (14.07, 49.0, 24.15, 54.84)


def _blank(png: bytes) -> bool:
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.int16)
    return bool(a.std() < 2)  # one flat colour: the service had no imagery here


def snapshot(area: Area, size_px: int) -> Snapshot:
    geom = geometry_of(area)
    minx, miny, maxx, maxy = geom.bounds
    c = geom.centroid
    if not (POLAND[0] <= c.x <= POLAND[2] and POLAND[1] <= c.y <= POLAND[3]):
        raise GeomcpError("out_of_coverage", "The orthophoto covers Poland only.", hint="Use true_color elsewhere.")
    # CRS:84 is plain lon/lat; scaling x by cos(latitude) keeps the picture undistorted and north-up
    width_m = (maxx - minx) * 111_320 * math.cos(math.radians(c.y))
    height_m = (maxy - miny) * 110_574
    longest = max(width_m, height_m)
    px = max(16, min(size_px, int(longest / NATIVE_M)))
    width, height = max(1, round(px * width_m / longest)), max(1, round(px * height_m / longest))
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": "Raster",
        "STYLES": "",
        "CRS": "CRS:84",
        "BBOX": f"{minx},{miny},{maxx},{maxy}",
        "WIDTH": width,
        "HEIGHT": height,
        "FORMAT": "image/png",
    }
    png = b""
    for url in SERVICES:
        with http.client(120) as client:
            png = http.with_retries(lambda u=url: http.check(client.get(u, params=params)).content)
        if not _blank(png):
            break
    else:
        raise GeomcpError("no_data", "GUGiK has no orthophoto for this area.")
    return Snapshot(
        layer="orthophoto",
        png=png,
        scene_date=None,
        requested_date=None,
        scene_cloud_cover=None,
        size_px=(width, height),
        pixel_size_m=round(longest / px, 2),
        legend={"type": "aerial photo", "note": "most recent national orthophoto; flight year varies by region"},
        provenance=Provenance(
            source="GUGiK orthophoto (PZGiK)",
            backend="gugik-wms",
            collection="ORTO",
            method=f"WMS GetMap from {url.rsplit('/', 1)[-1]}",
            licence="open data (PZGiK, free of charge since 2020)",
            attribution="Główny Urząd Geodezji i Kartografii",
            commercial_safe=True,
            retrieved_at=datetime.now(UTC),
        ),
    )
