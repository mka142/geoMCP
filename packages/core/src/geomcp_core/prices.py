"""Real-estate transaction prices from the national Register of Prices (RCN, GUGiK), open since February 2026.

Aggregates only: medians per year for apartments (PLN per m² of floor), houses (PLN per transaction) and
land (PLN per m² of plot). Notary names and deed numbers are in the source but never leave this module.
Gotcha: with the URN CRS the WFS expects and returns EPSG:2180 coordinates northing first.
"""

import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from shapely.geometry import Point
from shapely.prepared import prep

from geomcp_core import cache, http
from geomcp_core.areas import area_from_id, geometry_of, project
from geomcp_core.models import GeomcpError

RCN = "https://mapy.geoportal.gov.pl/wss/service/rcn"
NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "ms": "http://mapserver.gis.umn.edu/mapserver",
}
PAGE = 1000
MAX_RECORDS = 20_000  # per layer; a 4 km suburban square had 17,000 plot records since 2021 (subdivisions)
MAX_KM2 = 25.0
TTL_S = 7 * 24 * 3600
# the only fields kept; the server ignores PROPERTYNAME, so personal data (DOK_TWORCA, the notary) and deed
# numbers arrive in every response and are dropped here, at parse time
KEEP = {
    "TRAN_LOKALNY_ID_IIP", "TRAN_RODZAJ_TRANS", "TRAN_RODZAJ_RYNKU", "TRAN_CENA_BRUTTO", "DOK_DATA",
    "NIER_RODZAJ", "NIER_UDZIAL", "NIER_POW_GRUNTU", "NIER_CENA_BRUTTO",
    "LOK_FUNKCJA", "LOK_POW_UZYT", "LOK_CENA_BRUTTO", "BUD_RODZAJ",
}  # fmt: skip


class YearStats(BaseModel):
    count: int
    median: float
    p25: float
    p75: float


class PriceSeries(BaseModel):
    kind: str
    unit: str
    description: str
    years: dict[str, YearStats]


class PricesResult(BaseModel):
    area: str
    area_km2: float
    series: list[PriceSeries]
    transactions_read: dict[str, int] = Field(description="Records read per register layer")
    notices: list[str]
    source: str = "Rejestr Cen Nieruchomości (GUGiK, powiat registers)"
    licence: str = "open data (PZGiK)"


def _filter(bbox_2180: tuple[float, float, float, float], since: int) -> str:
    minx, miny, maxx, maxy = bbox_2180
    return (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"><fes:And>'
        "<fes:BBOX><fes:ValueReference>GEOMETRIA</fes:ValueReference>"
        f'<gml:Envelope srsName="urn:ogc:def:crs:EPSG::2180"><gml:lowerCorner>{miny} {minx}</gml:lowerCorner>'
        f"<gml:upperCorner>{maxy} {maxx}</gml:upperCorner></gml:Envelope></fes:BBOX>"  # northing first
        "<fes:PropertyIsGreaterThanOrEqualTo><fes:ValueReference>DOK_DATA</fes:ValueReference>"
        f"<fes:Literal>{since}-01-01</fes:Literal></fes:PropertyIsGreaterThanOrEqualTo></fes:And></fes:Filter>"
    )


def _location(geometry: ET.Element) -> tuple[float, float] | None:
    """(easting, northing) of a point, or the mean vertex of a parcel or building outline."""
    text = " ".join(e.text or "" for e in geometry.iter() if e.tag.endswith(("}pos", "}posList")))
    values = [float(v) for v in text.split()]
    if len(values) < 2:
        return None
    north, east = values[0::2], values[1::2]
    return sum(east) / len(east), sum(north) / len(north)


def _fetch(layer: str, bbox_2180: tuple[float, float, float, float], since: int) -> tuple[list[dict[str, Any]], bool]:
    """Records of one layer in the bbox (easting/northing) dated `since` or later; True when capped."""
    out: list[dict[str, Any]] = []
    start = 0
    with http.client(180) as c:
        while True:
            params = {
                "SERVICE": "WFS",
                "VERSION": "2.0.0",
                "REQUEST": "GetFeature",
                "TYPENAMES": f"ms:{layer}",
                "FILTER": _filter(bbox_2180, since),
                "COUNT": PAGE,
                "STARTINDEX": start,
            }
            root = ET.fromstring(http.with_retries(lambda p=params: http.check(c.get(RCN, params=p)).content))
            members = root.findall("wfs:member", NS)
            for m in members:
                feature = m[0]
                row: dict[str, Any] = {}
                for child in feature:
                    tag = child.tag.split("}", 1)[1]
                    if tag == "GEOMETRIA":
                        if xy := _location(child):
                            row["xy"] = xy
                    elif tag in KEEP:
                        text = "".join(child.itertext()).strip()
                        if text:
                            row[tag] = text
                out.append(row)
            start += len(members)
            if len(members) < PAGE:
                return out, False
            if start >= MAX_RECORDS:
                return out, True


def _num(row: dict[str, Any], key: str) -> float | None:
    try:
        v = float(str(row.get(key, "")).replace(",", "."))
    except ValueError:
        return None
    return v if v > 0 else None


def _years(values: list[tuple[str, float]]) -> dict[str, YearStats]:
    by_year: dict[str, list[float]] = {}
    for year, v in values:
        by_year.setdefault(year, []).append(v)
    out: dict[str, YearStats] = {}
    for year, vs in sorted(by_year.items()):
        if len(vs) < 3:  # too few sales to say anything
            continue
        q = statistics.quantiles(vs, n=4) if len(vs) >= 4 else [min(vs), statistics.median(vs), max(vs)]
        out[year] = YearStats(count=len(vs), median=round(statistics.median(vs)), p25=round(q[0]), p75=round(q[2]))
    return out


def get_prices(area_id: str, since_year: int | None = None) -> PricesResult:
    area = area_from_id(area_id)
    if area.area_km2 > MAX_KM2:
        raise GeomcpError(
            "area_too_large",
            f"Prices are read for areas up to {MAX_KM2:.0f} km² (this one is {area.area_km2:.0f} km²).",
            hint="Use a smaller area, such as a 2 km square around a point or one district.",
        )
    first = since_year or datetime.now(UTC).year - 5
    geom = project(geometry_of(area), "EPSG:4326", "EPSG:2180")
    inside = prep(geom)
    key = f"rcn|v2|{area.area_id}|{first}"
    raw: dict[str, dict[str, Any]] | None = cache.get("prices", key)
    if raw is None:
        raw = {}
        for layer in ("lokale", "budynki", "dzialki"):
            rows, capped = _fetch(layer, geom.bounds, first)
            raw[layer] = {"rows": [r for r in rows if "xy" in r and inside.contains(Point(r["xy"]))], "capped": capped}
        cache.put("prices", key, raw, TTL_S)
    notices: list[str] = []
    apartments: dict[str, list[tuple[str, float]]] = {"primary": [], "secondary": []}
    houses: list[tuple[str, float]] = []
    land: list[tuple[str, float]] = []
    # records per transaction: bundles (a developer's 59 parcels, apartments with land shares) carry
    # per-record prices that do not add up, so only single-property sales are priced
    records = Counter(f"{layer}|{r.get('TRAN_LOKALNY_ID_IIP')}" for layer, d in raw.items() for r in d["rows"])
    for layer, data in raw.items():
        if data["capped"]:
            notices.append(f"{layer}: over {MAX_RECORDS:,} records; the first {MAX_RECORDS:,} were used.")
        for r in data["rows"]:
            year = str(r.get("DOK_DATA", ""))[:4]
            if not year.isdigit() or r.get("TRAN_RODZAJ_TRANS") != "wolnyRynek":
                continue
            if layer == "lokale":
                price, floor = _num(r, "LOK_CENA_BRUTTO") or _num(r, "NIER_CENA_BRUTTO"), _num(r, "LOK_POW_UZYT")
                if r.get("LOK_FUNKCJA") == "mieszkalna" and r.get("NIER_UDZIAL") == "1/1" and price and floor:
                    market = "primary" if r.get("TRAN_RODZAJ_RYNKU") == "rynekPierwotny" else "secondary"
                    if 1_000 < price / floor < 100_000:  # drops garages and data-entry errors
                        apartments[market].append((year, price / floor))
                continue
            if records[f"{layer}|{r.get('TRAN_LOKALNY_ID_IIP')}"] != 1:
                continue
            price, plot = _num(r, "TRAN_CENA_BRUTTO"), _num(r, "NIER_POW_GRUNTU")
            if not (price and plot and plot >= 100):  # smaller "plots" are land shares of apartments or garages
                continue
            kind = r.get("NIER_RODZAJ")
            if layer == "budynki" and kind == "nieruchomoscGruntowaZabudowana" and r.get("BUD_RODZAJ") == "mieszkalny":
                houses.append((year, price))
            elif layer == "dzialki" and kind == "nieruchomoscGruntowaNiezabudowana":
                land.append((year, price / plot))
    series = [
        PriceSeries(
            kind=f"apartments_{m}",
            unit="PLN/m²",
            description=f"Apartments, {'new from developers' if m == 'primary' else 'resale'}: price per m² of floor",
            years=_years(v),
        )
        for m, v in apartments.items()
    ] + [
        PriceSeries(kind="houses", unit="PLN", description="Houses (a plot with one residential building): "
                    "price per transaction", years=_years(houses)),
        PriceSeries(kind="land", unit="PLN/m²", description="Undeveloped plots: price per m² of land",
                    years=_years(land)),
    ]  # fmt: skip
    if not any(s.years for s in series):
        notices.append(
            "No free-market sales found; the powiat may not publish its register yet (about 18 of 380 do not)."
        )
    notices.append("Gross prices from notarial deeds, free-market sales only; years with under 3 sales are omitted.")
    return PricesResult(
        area=area.name,
        area_km2=area.area_km2,
        series=series,
        transactions_read={k: len(v["rows"]) for k, v in raw.items()},
        notices=notices,
    )


def estimate_s(area_id: str) -> float:
    area = area_from_id(area_id)
    if cache.get("prices", f"rcn|v2|{area.area_id}|{datetime.now(UTC).year - 5}") is not None:
        return 1.0
    return 5.0 + 3.0 * area.area_km2  # measured: about 1.5 s per 1,000 records, 1,500 records per km²
