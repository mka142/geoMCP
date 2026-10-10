"""Local zoning plans (MPZP) through the national integration service (KIMPZP, GUGiK).

Each gmina publishes its own schema, so answers are parsed generically: every HTML table row becomes a dict,
and well-known fields (plan name, resolution, designation symbol and description, link) are picked by name.
Areas are sampled at points: a parcel at its representative point, larger areas on a grid.
Coverage: gminas that do not feed the integration (for example Wrocław) answer "brak wyniku" everywhere.
"""

import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx
from pydantic import BaseModel, Field
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from geomcp_core import cache, http
from geomcp_core.areas import area_from_id, geometry_of, project
from geomcp_core.models import GeomcpError

KIEG = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
PARCEL_FIELDS = {  # KIEG label -> our key; the cadastre has more, these matter for what can be built
    "Pole pow. w ewidencji gruntów (ha)": "registry_area_ha",
    "Grupa rejestrowa": "register_group",
    "Oznaczenie użytku": "land_use",
    "Oznaczenie konturu": "land_use_contour",
}
KIMPZP = "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
LAYERS = "plany,wektor-str,wektor-pow,wektor-lzb,raster"
MAX_KM2 = 5.0
MAX_POINTS = 16
TTL_S = 30 * 24 * 3600
SKIP = {"objectid", "shape", "shape.area", "shape.len", "gdb_id", "id_pg", "globalid"}
# lower-case field-name fragments, most specific first
FIELDS = {
    "plan": ("nazwa mpzp", "nazwaplanu", "nazwa_planu", "tytul", "symbol_planu", "numerplanu"),  # not bare "nazwa":
    # in some gminas that is the precinct name
    "resolution": ("numeruchwały", "uchwalenie", "nr_uchwaly", "uchwala"),
    "adopted": ("datauchwalenia", "data uchwalenia", "data_uchwalenia"),
    "in_force": ("datawejściawżycie", "data obowiązywania", "data_wejscia"),
    "symbol": ("oznaczenie", "symbolterenu", "symbol_terenu", "symbol"),
    "designation": ("opis_oznac", "opis_terenu", "przeznaczenie", "nazwa_terenu", "opis"),
    "link": ("www", "link", "url", "dokument"),
}


class _Tables(HTMLParser):
    """Rows of every table as header -> value dicts."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self._headers: list[str] = []
        self._cells: list[str] = []
        self._cell: list[str] | None = None
        self._is_header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._headers = []
        elif tag == "tr":
            self._cells = []
        elif tag in ("th", "td"):
            self._cell, self._is_header = [], tag == "th"

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("th", "td") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            (self._headers if self._is_header else self._cells).append(text)
            self._cell = None
        elif tag == "tr" and self._cells and self._headers:
            self.rows.append(dict(zip(self._headers, self._cells, strict=False)))


def _pick(row: dict[str, str], fragments: tuple[str, ...]) -> str | None:
    lower = {k.lower(): v for k, v in row.items() if v and v.lower() != "null"}
    for f in fragments:
        for k, v in lower.items():
            if f in k:
                return v
    return None


class Designation(BaseModel):
    symbol: str | None
    description: str | None
    plan: str | None
    resolution: str | None = None
    adopted: str | None = None
    link: str | None = None
    share_of_points: float = Field(description="Share of sample points with this designation (0-1)")
    attributes: dict[str, str] = Field(default_factory=dict, description="All fields the gmina publishes")


class ZoningResult(BaseModel):
    area: str
    points_sampled: int
    points_in_a_plan: int
    designations: list[Designation]
    no_plan_messages: list[str] = Field(description="What the service said where it found no plan")
    parcel: dict[str, str] | None = Field(
        default=None,
        description="For a parcel: cadastre registry area (ha), register group and land-use class "
        "(e.g. B housing land, Bi other built-up, R arable, Ł meadow), as the powiat publishes them",
    )
    gaps: list[str]
    notices: list[str]
    retrieved_at: datetime


def _points(geom: BaseGeometry) -> list[Point]:
    """Up to MAX_POINTS points inside the polygon (EPSG:2180)."""
    if geom.area < 20_000:  # a parcel or a small site: its representative point
        return [geom.representative_point()]
    minx, miny, maxx, maxy = geom.bounds
    k = math.ceil(math.sqrt(MAX_POINTS))
    pts = [
        Point(minx + (maxx - minx) * (i + 0.5) / k, miny + (maxy - miny) * (j + 0.5) / k)
        for i in range(k)
        for j in range(k)
    ]
    inside = [p for p in pts if geom.contains(p)]
    return inside or [geom.representative_point()]


def _query(p: Point) -> tuple[list[dict[str, str]], str]:
    """Rows found at one point (EPSG:2180), or the service's message when none."""
    x, y = p.x, p.y
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": LAYERS,
        "QUERY_LAYERS": LAYERS,
        "STYLES": "",
        "CRS": "EPSG:2180",
        "BBOX": f"{y - 5},{x - 5},{y + 5},{x + 5}",  # northing first in WMS 1.3.0
        "WIDTH": 11,
        "HEIGHT": 11,
        "I": 5,
        "J": 5,
        "INFO_FORMAT": "text/html",
        "FEATURE_COUNT": 10,
    }
    with http.client(90) as c:
        text = http.with_retries(lambda: http.check(c.get(KIMPZP, params=params)).text, attempts=2)
    parser = _Tables()
    parser.feed(text)
    rows = [{k: v for k, v in r.items() if k.lower() not in SKIP} for r in parser.rows]
    return rows, "" if rows else " ".join(re.sub(r"<[^>]+>", " ", text).split())[:200]


def _parcel(p: Point) -> dict[str, str]:
    """Cadastre facts of the parcel at a point (EPSG:2180), from the national cadastre integration (KIEG)."""
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": "dzialki,uzytki",
        "QUERY_LAYERS": "dzialki,uzytki", "STYLES": "", "CRS": "EPSG:2180",
        "BBOX": f"{p.y - 5},{p.x - 5},{p.y + 5},{p.x + 5}", "WIDTH": 11, "HEIGHT": 11, "I": 5, "J": 5,
        "INFO_FORMAT": "text/html", "FEATURE_COUNT": 10,
    }  # fmt: skip
    with http.client(60) as c:
        text = http.with_retries(lambda: http.check(c.get(KIEG, params=params)).text, attempts=2)
    pairs = re.findall(r"<tr><td>([^<]*)</td><td>([^<]*)</td></tr>", text)
    return {PARCEL_FIELDS[k.strip()]: v.strip() for k, v in pairs if k.strip() in PARCEL_FIELDS and v.strip()}


def get_zoning(area_id: str) -> ZoningResult:
    area = area_from_id(area_id)
    if area.area_km2 > MAX_KM2:
        raise GeomcpError(
            "area_too_large",
            f"Zoning is sampled for areas up to {MAX_KM2:.0f} km² (this one is {area.area_km2:.0f} km²).",
            hint="Ask for a parcel, an address with a small buffer, or a district.",
        )
    geom = project(geometry_of(area), "EPSG:4326", "EPSG:2180")
    points = _points(geom)
    key = f"zoning|v2|{area.area_id}"
    results: list[Any] | None = cache.get("zoning", key)
    gaps: list[str] = []
    if results is None:
        results = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for p, fut in [(p, pool.submit(_query, p)) for p in points]:
                try:
                    results.append(list(fut.result()))
                except (GeomcpError, httpx.HTTPError) as e:  # one slow gmina server must not sink the call
                    gaps.append(f"point {p.x:.0f},{p.y:.0f}: {getattr(e, 'message', type(e).__name__)}")
        if not gaps:
            cache.put("zoning", key, results, TTL_S)
    found: dict[tuple[str | None, str | None, str | None], Designation] = {}
    hits = 0
    messages: list[str] = []
    for rows, message in results:
        if not rows:
            if message and message not in messages:
                messages.append(message)
            continue
        hits += 1
        seen_here: set[tuple[str | None, str | None, str | None]] = set()
        plan_rows = [r for r in rows if _pick(r, FIELDS["symbol"]) or _pick(r, FIELDS["designation"])] or rows
        for r in plan_rows:
            d = Designation(
                symbol=_pick(r, FIELDS["symbol"]),
                description=_pick(r, FIELDS["designation"]),
                plan=_pick(r, FIELDS["plan"]),
                resolution=_pick(r, FIELDS["resolution"]),
                adopted=_pick(r, FIELDS["adopted"]),
                link=_pick(r, FIELDS["link"]),
                share_of_points=0,
                attributes=r,
            )
            k = (d.symbol, d.description, d.plan)
            if k in seen_here:  # the same designation drawn at several scales
                continue
            seen_here.add(k)
            found.setdefault(k, d).share_of_points += 1
    n = len(results) or 1
    for d in found.values():
        d.share_of_points = round(d.share_of_points / n, 2)
    parcel: dict[str, str] | None = None
    if area.area_id.startswith("a_uldk_"):
        try:
            parcel = _parcel(geom.representative_point())
        except (GeomcpError, httpx.HTTPError) as e:
            gaps.append(f"cadastre: {getattr(e, 'message', type(e).__name__)}")
    notices = ["Designations are sampled at points; read the plan text (link) before any decision."]
    if hits == 0 and messages:
        notices.append(
            "No plan found. Either no local plan covers this area, or the gmina does not publish its plans to the "
            "national integration (Wrocław, for example); check the gmina's own geoportal."
        )
    return ZoningResult(
        area=area.name,
        points_sampled=len(points),
        points_in_a_plan=hits,
        designations=sorted(found.values(), key=lambda d: -d.share_of_points),
        no_plan_messages=messages,
        parcel=parcel,
        gaps=gaps,
        notices=notices,
        retrieved_at=datetime.now(UTC),
    )


def estimate_s(area_id: str) -> float:
    area = area_from_id(area_id)
    if cache.get("zoning", f"zoning|v2|{area.area_id}") is not None:
        return 1.0
    return 4.0 if area.area_km2 < 0.02 else 25.0  # some gmina servers take a minute per point
