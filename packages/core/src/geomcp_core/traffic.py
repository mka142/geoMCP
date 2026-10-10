"""Traffic on national roads near an area: GDDKiA General Traffic Survey (GPR 2025, published 30 September 2026),
with the 2020/21 count of the same section for comparison.

National roads crossing the area come from OpenStreetMap refs. Each survey section has a measurement point
(coordinates in the section list), so for every road the sections measured inside the area are reported, or
else the section measured nearest to it. Counts cover national roads outside city limits.
Section numbers were reassigned between the surveys (a third of the 2025 numbers named a different section in
2020/21), so the 2020/21 count comes through the section list's "Porównanie_z_2020" column, which names the
comparable 2020 section or is empty.
"""

import csv
import io
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field
from shapely.geometry import Point

from geomcp_core import cache, http
from geomcp_core.adapters.osm import OVERPASS, _polys, _union  # pyright: ignore[reportPrivateUsage]
from geomcp_core.areas import area_from_id, geometry_of, project
from geomcp_core.models import GeomcpError

SDRR_2025 = "https://api.dane.gov.pl/resources/2718919,sdrr-2025-na-sieci-drog-krajowych/file"
SECTIONS_2025 = (
    "https://api.dane.gov.pl/resources/2718917,wykaz-odcinkow-pomiarowych-sieci-drog-krajowych-w-2025-r/file"
)
SDRR_2020 = "https://api.dane.gov.pl/media/resources/20221223/Tab02_20_dk_KRAJ.csv"
ETOLL_MONTHLY = "https://api-transtat.stat.gov.pl/api/v1/C037DInd216p"
SURVEY = "GPR 2025"
MAX_KM2 = 250.0
MAX_DISTANCE_KM = 15.0  # a section measured further away than this says little about the area
TTL_S = 365 * 24 * 3600
OSM_TTL_S = 30 * 24 * 3600
POLAND = (14.07, 49.0, 24.15, 54.84)
NATIONAL = re.compile(r"^(?:A|S|DK)?\s*(\d{1,2})$")  # national roads are numbered 1-99; 3 digits are voivodeship roads


class Segment(BaseModel):
    road: str
    name: str = Field(description="Junction to junction, as GDDKiA names it")
    km_from: float
    km_to: float
    vehicles_per_day: int = Field(description="Average annual daily traffic 2025 (SDRR), both directions")
    vehicles_per_day_2020: int | None = Field(description="The same section in GPR 2020/21, when it existed")
    cars_per_day: int
    heavy_share: float = Field(description="Trucks, trucks with trailers and buses as a share of all vehicles (0-1)")
    measured_at: str | None = Field(description="Locality of the measurement point")
    distance_km: float = Field(description="Distance of the measurement point from the area (0 = inside)")


class TollTrend(BaseModel):
    road: str
    last_12_months: int = Field(description="e-TOLL vehicle passages on the whole road, last 12 months")
    change_vs_year_before: float | None = Field(description="Change against the 12 months before (fraction)")
    latest_month: str


class TrafficResult(BaseModel):
    area: str
    survey: str
    roads_in_area: list[str] = Field(description="National roads crossing the area (OpenStreetMap refs)")
    segments: list[Segment]
    toll_trends: list[TollTrend] = Field(
        default=[],  # pydantic copies mutable defaults
        description="Whole-road heavy-vehicle trend from e-TOLL (GUS TranStat, experimental), for tolled roads",
    )
    notices: list[str]
    source: str = "GDDKiA Generalny Pomiar Ruchu 2025 and 2020/21 (dane.gov.pl); road refs © OpenStreetMap"
    licence: str = "CC0 1.0 (GDDKiA); ODbL-1.0 (OSM)"
    retrieved_at: datetime


def _num(text: str) -> float:
    return float(text.replace(",", ".").strip() or 0)


def _csv(url: str, encoding: str) -> list[dict[str, str]]:
    with http.client(120) as c:
        raw = http.with_retries(lambda: http.check(c.get(url, follow_redirects=True)).content)
    return list(csv.DictReader(io.StringIO(raw.decode(encoding)), delimiter=";"))


def _survey() -> list[dict[str, Any]]:
    """GPR 2025 sections with counts, measurement point (lon, lat) and the 2020/21 count."""
    if (hit := cache.get("traffic", "gpr2025|v3")) is not None:
        return hit
    old = {r["NR2020"]: int(r["SDRR2020"]) for r in _csv(SDRR_2020, "cp1250") if r.get("SDRR2020", "").isdigit()}
    points: dict[str, tuple[float, float, str]] = {}
    compare: dict[str, str] = {}  # 2025 section -> the 2020/21 section it is comparable with
    for r in _csv(SECTIONS_2025, "utf-8-sig"):
        if r.get("Porównanie_z_2020", "").strip():
            compare[r["Numer_odcinka_pomiarowego"]] = r["Porównanie_z_2020"].strip()
        coords = [  # right and left measurement points; many sections list only one
            (_num(r[f"Dł._geo_punktu_{side}"]), _num(r[f"Szer._geo_punktu_{side}"]))
            for side in ("prawego", "lewego")
            if r.get(f"Szer._geo_punktu_{side}", "").strip() and r.get(f"Dł._geo_punktu_{side}", "").strip()
        ]
        if not coords:
            continue
        lon, lat = sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)
        if POLAND[0] < lon < POLAND[2] and POLAND[1] < lat < POLAND[3]:
            points[r["Numer_odcinka_pomiarowego"]] = (lon, lat, r.get("Miejscowość", "").strip().title())
    rows: list[dict[str, Any]] = []
    for r in _csv(SDRR_2025, "utf-8-sig"):
        section = r["nr_odcinka"]
        try:
            total = int(r["sdrr2025"])
            # columns p1-p8, ordered by the 2025 legend as the values show: motorcycles, cars, minibuses, vans,
            # trucks, trucks with trailers, buses, tractors
            p = [int(r[f"sdrr_p{i}"] or 0) for i in range(1, 9)]
        except (KeyError, ValueError):
            continue
        lon, lat, place = points.get(section, (None, None, None))
        rows.append(
            {
                "section": section,
                "road": r["numer_drogi_kraj"].strip(),
                "name": r["nazwa"].strip(),
                "km_from": _num(r["pikietaz_pocz"]),
                "km_to": _num(r["pikietaz_kon"]),
                "total": total,
                "total_2020": old.get(compare.get(section, "")),
                "cars": p[1],
                "heavy": p[4] + p[5] + p[6],
                "lon": lon,
                "lat": lat,
                "place": place,
            }
        )
    cache.put("traffic", "gpr2025|v3", rows, TTL_S)
    return rows


def _roads(area_id: str) -> set[str]:
    """National road numbers crossing the area, from OpenStreetMap."""
    key = f"traffic-osm|v3|{area_id}"
    if (hit := cache.get("traffic", key)) is not None:
        return set(hit)
    polys = _polys(geometry_of(area_from_id(area_id)))
    query = (
        "[out:json][timeout:90];"
        + _union('way["highway"~"^(motorway|trunk|primary|secondary)(_link)?$"]["ref"]', polys)
        + ";out tags;"
    )
    payload: dict[str, Any] = {}
    with http.client(120) as c:
        for i, url in enumerate(OVERPASS):
            try:
                payload = http.with_retries(lambda u=url: http.json_of(c.post(u, data={"data": query})), attempts=3)
                break
            except GeomcpError as e:
                if e.code != "source_busy" or i == len(OVERPASS) - 1:
                    raise
    roads: set[str] = set()
    for e in payload.get("elements", []):
        for token in re.split(r"[;,]", e.get("tags", {}).get("ref", "")):
            token = token.strip().upper()
            if m := NATIONAL.match(token):
                roads.add(token[0] + m.group(1) if token[0] in "AS" else m.group(1))
    cache.put("traffic", key, sorted(roads), OSM_TTL_S)
    return roads


def _toll_trends(roads: set[str]) -> list[TollTrend]:
    """Monthly e-TOLL passages per road (vehicles over 3.5 t, whole road), last 12 months against the 12 before."""
    if (months := cache.get("traffic", "etoll|monthly")) is None:
        with http.client(60) as c:
            months = http.with_retries(lambda: http.json_of(c.get(ETOLL_MONTHLY)))
        cache.put("traffic", "etoll|monthly", months, 7 * 24 * 3600)
    rows = sorted(months, key=lambda m: (m["year"], m["dataX"]))  # dataX is "07 - July"
    out: list[TollTrend] = []
    for road in sorted(roads):
        column = road.lower() if road[0] in "AS" else f"dk{road}"
        series = [int(m.get(column) or 0) for m in rows]
        if len(series) < 12 or not any(series[-12:]):
            continue
        last, before = sum(series[-12:]), sum(series[-24:-12]) if len(series) >= 24 else 0
        out.append(
            TollTrend(
                road=road,
                last_12_months=last,
                change_vs_year_before=round(last / before - 1, 3) if before else None,
                latest_month=f"{rows[-1]['year']}-{rows[-1]['dataX'][:2]}",
            )
        )
    return out


def get_traffic(area_id: str) -> TrafficResult:
    area = area_from_id(area_id)
    if area.area_km2 > MAX_KM2:
        raise GeomcpError("area_too_large", f"Traffic is looked up for areas up to {MAX_KM2:.0f} km².")
    geom = geometry_of(area)
    c = geom.centroid
    if not (POLAND[0] <= c.x <= POLAND[2] and POLAND[1] <= c.y <= POLAND[3]):
        raise GeomcpError("out_of_coverage", "Traffic counts cover Polish national roads only.")
    metric = project(geom, "EPSG:4326", "EPSG:2180")
    roads = _roads(area.area_id)
    segments: list[Segment] = []
    for road in sorted(roads):
        candidates: list[tuple[float, dict[str, Any]]] = []
        for r in _survey():
            if r["road"] != road or r["lon"] is None:
                continue
            point = project(Point(r["lon"], r["lat"]), "EPSG:4326", "EPSG:2180")
            candidates.append((metric.distance(point) / 1000, r))
        inside = [x for x in candidates if x[0] == 0]
        chosen = inside or sorted(candidates, key=lambda x: x[0])[:1]
        for distance, r in chosen:
            if distance > MAX_DISTANCE_KM:
                continue
            segments.append(
                Segment(
                    road=road,
                    name=r["name"],
                    km_from=r["km_from"],
                    km_to=r["km_to"],
                    vehicles_per_day=r["total"],
                    vehicles_per_day_2020=r["total_2020"],
                    cars_per_day=r["cars"],
                    heavy_share=round(r["heavy"] / r["total"], 3) if r["total"] else 0.0,
                    measured_at=r["place"] or None,
                    distance_km=round(distance, 1),
                )
            )
    try:
        trends = _toll_trends(roads)
    except (GeomcpError, httpx.HTTPError):
        trends = []  # the trend is a bonus; the survey counts stand on their own
    notices = [
        f"{SURVEY} average daily traffic on national roads outside city limits; per road, the sections measured "
        f"inside the area, or else the nearest measured section (up to {MAX_DISTANCE_KM:.0f} km away)."
    ]
    if roads and not segments:
        notices.append("The roads here are not surveyed national roads (for example they run inside a city).")
    if not roads:
        notices.append("No national road crosses this area.")
    return TrafficResult(
        area=area.name,
        survey=SURVEY,
        roads_in_area=sorted(roads, key=lambda r: (not r[0].isalpha(), r)),
        segments=segments,
        toll_trends=trends,
        notices=notices,
        retrieved_at=datetime.now(UTC),
    )


def estimate_s(area_id: str) -> float:
    area = area_from_id(area_id)
    return 2.0 if cache.get("traffic", f"traffic-osm|v3|{area.area_id}") is not None else 15.0
