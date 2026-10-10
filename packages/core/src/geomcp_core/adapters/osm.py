"""OpenStreetMap counts and road length through the Overpass API (one query per area, cached 7 days)."""

import threading
from datetime import UTC, date, datetime
from typing import Any, ClassVar

from pyproj import Geod
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from geomcp_core import cache, http
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

OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
TTL_S = 7 * 24 * 3600
MAX_AREA_KM2 = 250.0  # Overpass answers for a gmina in ~10 s; larger areas risk its timeouts
_one_at_a_time = threading.Lock()  # Overpass etiquette: at most a couple of concurrent queries per client
_GEOD = Geod(ellps="WGS84")

COUNTS: dict[str, tuple[str, str]] = {
    "buildings": ('way["building"]', "Buildings mapped in OpenStreetMap"),
    "schools": ('nwr["amenity"~"^(school|kindergarten|university|college)$"]', "Schools, kindergartens, universities"),
    "healthcare": ('nwr["amenity"~"^(hospital|clinic|doctors|pharmacy)$"]', "Hospitals, clinics, doctors, pharmacies"),
    "shops": ('nwr["shop"]', "Shops of any kind"),
    "transit_stops": ('nwr["public_transport"="platform"]', "Public-transport stops (platforms)"),
}
ROADS = 'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service)$"]'


def _polys(geom: BaseGeometry) -> list[str]:
    parts = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    out: list[str] = []
    for part in parts:
        simple = part.simplify(0.0002, preserve_topology=True)
        if not isinstance(simple, Polygon):
            continue
        ring: list[tuple[float, float]] = [(float(x), float(y)) for x, y, *_ in simple.exterior.coords]
        out.append(" ".join(f"{lat:.6f} {lon:.6f}" for lon, lat in ring[:-1]))
    return out


def _union(selector: str, polys: list[str]) -> str:
    return "(" + "".join(f'{selector}(poly:"{p}");' for p in polys) + ")"


class OsmAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="osm",
        name="OpenStreetMap (Overpass API)",
        variables=[
            VariableInfo(name=n, units="count", topic="infrastructure", description=d, static=True)
            for n, (_, d) in COUNTS.items()
        ]
        + [
            VariableInfo(
                name="road_length",
                units="km",
                topic="infrastructure",
                description="Length of public roads (motorway to residential and service), clipped to the area",
                static=True,
            )
        ],
        access_pattern="server_stats",
        resolution="vector (as mapped by volunteers)",
        coverage=(date(2026, 1, 1), None),
        latency="PT1H",
        licence="ODbL-1.0",
        attribution="© OpenStreetMap contributors",
        commercial_safe=True,
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError("static_variable", f"{variable} is a current snapshot.", "Use get_summary for it.")

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        return 0.0 if cache.get(self.info.id, f"osm|v1|{area.area_id}") is not None else 25.0

    def _query(self, area: Area) -> tuple[dict[str, Any], Cost]:
        key = f"osm|v1|{area.area_id}"
        if (hit := cache.get(self.info.id, key)) is not None:
            return hit, Cost(cache_hits=1)
        if area.area_km2 > MAX_AREA_KM2:
            raise GeomcpError(
                "area_too_large",
                f"OpenStreetMap counts are limited to {MAX_AREA_KM2:.0f} km² (this area: {area.area_km2:,.0f} km²).",
            )
        geom = geometry_of(area)
        polys = _polys(geom)
        statements = [f"{_union(sel, polys)};out count;" for sel, _ in COUNTS.values()]
        query = "[out:json][timeout:120];" + "".join(statements) + f"{_union(ROADS, polys)};out geom;"
        payload: dict[str, Any] = {}
        with _one_at_a_time, http.client(180) as c:
            for i, url in enumerate(OVERPASS):  # public instances are often busy: fall back to a mirror
                try:
                    payload = http.with_retries(
                        lambda url=url: http.json_of(c.post(url, data={"data": query})), attempts=3, timeout_s=200
                    )
                    break
                except GeomcpError as e:
                    if e.code != "source_busy" or i == len(OVERPASS) - 1:
                        raise
        counts = [int(e["tags"]["total"]) for e in payload["elements"] if e["type"] == "count"]
        km = 0.0
        for way in (e for e in payload["elements"] if e["type"] == "way"):
            line = LineString([(p["lon"], p["lat"]) for p in way.get("geometry", [])])
            clipped = line.intersection(geom)
            for part in getattr(clipped, "geoms", [clipped]):
                if isinstance(part, LineString) and not part.is_empty:
                    lons, lats = part.xy
                    km += _GEOD.line_length(lons, lats) / 1000  # pyright: ignore[reportUnknownMemberType]
        result = {
            **dict(zip(COUNTS, counts, strict=True)),
            "road_length": round(km, 2),
            "as_of": payload.get("osm3s", {}).get("timestamp_osm_base", ""),
        }
        cache.put(self.info.id, key, result, TTL_S)
        return result, Cost(cache_misses=1)

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        result, cost = self._query(area)
        as_of = str(result["as_of"])[:10]
        units = {v.name: v.units for v in self.info.variables}
        prov = Provenance(
            source=self.info.name,
            backend="overpass",
            collection="planet",
            method=f"objects inside the area polygon as of {as_of}; completeness depends on volunteer mapping",
            licence=self.info.licence,
            attribution=self.info.attribution,
            commercial_safe=True,
            retrieved_at=datetime.now(UTC),
        )
        facts = [
            Fact(variable=v, units=units[v], value=float(result[v]), period=as_of, provenance=prov) for v in variables
        ]
        return facts, cost
