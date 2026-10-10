"""Area resolution.

Area ids are self-describing, so any process can rebuild an area from its id:
- `a_pt_<lat>_<lon>_<ci|sq><metres>`: a circle or square around a point (computed locally);
- `a_osm_<R|W|N><id>`: an OpenStreetMap object found through Nominatim (geometry cached on disk);
- `a_uldk_<parcel id>`: a Polish cadastral parcel from GUGiK ULDK.
Tool results carry a simplified geometry; `geometry_of` returns the full one for computations.
"""

import re
import threading
import time
from typing import Any, Literal

import numpy as np
import shapely
from numpy.typing import NDArray
from pyproj import CRS, Geod, Transformer
from shapely import wkt
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry

from geomcp_core import cache, http
from geomcp_core.models import Area, AreaKind, GeomcpError

Shape = Literal["circle", "square"]

_COORDS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_POINT_ID = re.compile(r"^a_pt_(-?\d+\.\d{4})_(-?\d+\.\d{4})_(ci|sq)(\d+)$")
_OSM_ID = re.compile(r"^a_osm_([RWN])(\d+)$")
_PARCEL = re.compile(r"^\d{6}_\d\.\d{4}\.\S+$")
_GEOD = Geod(ellps="WGS84")
_MAX_VERTICES = 200
_CLEAR_WINNER = 0.1  # Nominatim importance gap that makes the first hit unambiguous

NOMINATIM = "https://nominatim.openstreetmap.org"
ULDK = "https://uldk.gugik.gov.pl/"
OSM_LICENCE = "ODbL-1.0 (© OpenStreetMap contributors)"

_nominatim_lock = threading.Lock()
_nominatim_last = 0.0


def utm_epsg(lon: float, lat: float) -> int:
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1


def project(geom: BaseGeometry, crs_from: int | str, crs_to: int | str) -> BaseGeometry:
    transformer = Transformer.from_crs(crs_from, crs_to, always_xy=True)

    def fn(xy: NDArray[np.float64]) -> NDArray[np.float64]:
        xs, ys = transformer.transform(xy[:, 0], xy[:, 1])
        return np.column_stack((xs, ys))

    return shapely.transform(geom, fn)


def _km2(geom: BaseGeometry) -> float:
    area_m2, _ = _GEOD.geometry_area_perimeter(geom)  # pyright: ignore[reportUnknownMemberType]
    return abs(float(area_m2)) / 1e6


def _simplified(geom: BaseGeometry) -> dict[str, Any]:
    """GeoJSON with at most ~200 vertices, so results stay small in an LLM context."""
    tol, g = 0.0, geom
    while shapely.get_num_coordinates(g) > _MAX_VERTICES:  # pyright: ignore[reportUnknownMemberType]
        tol = tol * 2 if tol else 0.0002
        g = geom.simplify(tol, preserve_topology=True)
    return dict(mapping(g))


def _area(
    area_id: str,
    name: str,
    kind: AreaKind,
    geom: BaseGeometry,
    source: str,
    licence: str,
    admin: dict[str, str],
) -> Area:
    minx, miny, maxx, maxy = geom.bounds
    return Area(
        area_id=area_id,
        name=name,
        kind=kind,
        area_km2=round(_km2(geom), 6),  # m² precision, so parcels keep a real size
        bbox=(round(minx, 6), round(miny, 6), round(maxx, 6), round(maxy, 6)),
        geometry=_simplified(geom),
        admin=admin,
        source=source,
        licence=licence,
    )


def _remember(area: Area, geom: BaseGeometry) -> Area:
    cache.put("areas", area.area_id, area.model_dump(mode="json"), None)
    cache.put("areas-geometry", area.area_id, dict(mapping(geom)), None)
    return area


def _buffer_geometry(lat: float, lon: float, buffer_m: int, shape_: Shape) -> BaseGeometry:
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m")
    geom_local = Point(0, 0).buffer(buffer_m, cap_style="square" if shape_ == "square" else "round")
    return project(geom_local, local.to_wkt(), "EPSG:4326")


def _buffer_area(lat: float, lon: float, buffer_m: int, shape_: Shape, name: str | None = None) -> Area:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise GeomcpError("invalid_area", f"Coordinates {lat}, {lon} are out of range.", "Use 'lat,lon' in degrees.")
    if buffer_m <= 0:
        raise GeomcpError("invalid_area", "buffer_m must be positive.")
    kind_code = "sq" if shape_ == "square" else "ci"
    size = (
        f"{2 * buffer_m / 1000:.2f} km square" if shape_ == "square" else f"Circle of radius {buffer_m / 1000:.2f} km"
    )
    return _area(
        f"a_pt_{lat:.4f}_{lon:.4f}_{kind_code}{buffer_m}",
        f"{size} around {name or f'{lat:.4f}, {lon:.4f}'}",
        "buffer",
        _buffer_geometry(lat, lon, buffer_m, shape_),
        "user coordinates",
        "n/a",
        {},
    )


def _nominatim(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Nominatim allows at most one request per second per application (usage policy)."""
    global _nominatim_last
    with _nominatim_lock:
        wait = 1.05 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            time.sleep(wait)
        try:
            with http.client(30) as c:
                hits: list[dict[str, Any]] = http.with_retries(
                    lambda: http.json_of(c.get(f"{NOMINATIM}/{path}", params=params))
                )
        finally:
            _nominatim_last = time.monotonic()
    return hits


def _osm_area(hit: dict[str, Any], buffer_m: int, shape_: Shape) -> Area:
    tags: dict[str, str] = hit.get("extratags") or {}
    name: str = hit.get("name") or hit["display_name"].split(",")[0]
    geojson: dict[str, Any] = hit.get("geojson") or {"type": "Point"}
    if geojson["type"] not in ("Polygon", "MultiPolygon"):
        lat, lon = round(float(hit["lat"]), 4), round(float(hit["lon"]), 4)
        return _buffer_area(lat, lon, buffer_m, shape_, name=hit["display_name"])
    admin = {"display_name": hit["display_name"]}
    kind: AreaKind = "place"
    if terc := tags.get("teryt:terc"):
        admin["terc"] = terc
        kind = {7: "gmina", 4: "powiat", 2: "wojewodztwo"}.get(len(terc), "place")  # type: ignore[assignment]
    geom = shape(geojson)
    area_id = f"a_osm_{hit['osm_type'][0].upper()}{hit['osm_id']}"
    return _remember(_area(area_id, name, kind, geom, "OpenStreetMap via Nominatim", OSM_LICENCE, admin), geom)


def _parcel(parcel_id: str) -> Area:
    params = {"request": "GetParcelById", "id": parcel_id, "result": "geom_wkt", "srid": "4326"}
    with http.client(60) as c:
        text = http.with_retries(lambda: http.check(c.get(ULDK, params=params)).text)
    lines = text.strip().splitlines()
    if len(lines) < 2 or lines[0].strip() != "0":
        raise GeomcpError("unknown_area", f"GUGiK ULDK does not know parcel {parcel_id!r}.")
    geom = wkt.loads(lines[1].split(";", 1)[-1])
    area = _area(f"a_uldk_{parcel_id}", f"Parcel {parcel_id}", "parcel", geom, "GUGiK ULDK", "GUGiK open data", {})
    return _remember(area, geom)


def resolve_area(query: str, buffer_m: int = 1000, shape_: Shape = "circle") -> tuple[list[Area], str | None]:
    """Candidate areas for a query, and the selected area_id (None when the query is ambiguous).

    Accepts 'lat,lon', a Polish parcel id (e.g. 026401_1.0001.2395) or any place name or address.
    Points and addresses become a buffer of `buffer_m` metres.
    """
    if m := _COORDS.match(query):
        area = _buffer_area(round(float(m.group(1)), 4), round(float(m.group(2)), 4), buffer_m, shape_)
        return [area], area.area_id
    if _PARCEL.match(query.strip()):
        area = _parcel(query.strip())
        return [area], area.area_id
    params = {
        "q": query,
        "format": "jsonv2",
        "polygon_geojson": 1,
        "polygon_threshold": 0.0001,
        "extratags": 1,
    }
    hits = _nominatim("search", params | {"limit": 5})
    if not hits:
        raise GeomcpError(
            "unknown_area", f"No place matches {query!r}.", hint="Try a fuller name, an address or 'lat,lon'."
        )
    areas = list({a.area_id: a for a in (_osm_area(h, buffer_m, shape_) for h in hits)}.values())
    importance = [float(h.get("importance") or 0) for h in hits]
    clear = len(areas) == 1 or len(importance) < 2 or importance[0] - importance[1] >= _CLEAR_WINNER
    return areas, areas[0].area_id if clear else None


def area_from_id(area_id: str) -> Area:
    if m := _POINT_ID.match(area_id):
        lat, lon, kind_code, buffer_m = float(m.group(1)), float(m.group(2)), m.group(3), int(m.group(4))
        return _buffer_area(lat, lon, buffer_m, "square" if kind_code == "sq" else "circle")
    if cached := cache.get("areas", area_id):
        return Area.model_validate(cached)
    if m := _OSM_ID.match(area_id):
        params = {
            "osm_ids": f"{m.group(1)}{m.group(2)}",
            "format": "jsonv2",
            "polygon_geojson": 1,
            "extratags": 1,
        }
        hits = _nominatim("lookup", params)
        if not hits:
            raise GeomcpError("unknown_area", f"OpenStreetMap object {area_id!r} no longer exists.")
        return _osm_area(hits[0], 1000, "circle")
    if area_id.startswith("a_uldk_"):
        return _parcel(area_id.removeprefix("a_uldk_"))
    raise GeomcpError("unknown_area", f"Unknown area_id {area_id!r}.", hint="Call resolve_area first.")


def geometry_of(area: Area) -> BaseGeometry:
    """Full-resolution geometry in EPSG:4326."""
    if m := _POINT_ID.match(area.area_id):
        lat, lon, kind_code, buffer_m = float(m.group(1)), float(m.group(2)), m.group(3), int(m.group(4))
        return _buffer_geometry(lat, lon, buffer_m, "square" if kind_code == "sq" else "circle")
    if cached := cache.get("areas-geometry", area.area_id):
        return shape(cached)
    if m := _OSM_ID.match(area.area_id):  # cache was cleared: fetch the full geometry again
        params = {
            "osm_ids": f"{m.group(1)}{m.group(2)}",
            "format": "jsonv2",
            "polygon_geojson": 1,
            "extratags": 1,
        }
        _osm_area(_nominatim("lookup", params)[0], 1000, "circle")
    elif area.area_id.startswith("a_uldk_"):
        _parcel(area.area_id.removeprefix("a_uldk_"))
    return shape(cache.get("areas-geometry", area.area_id) or area.geometry)
