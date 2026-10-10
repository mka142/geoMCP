"""GUGiK BDOT10k buildings: how many buildings of each function stand in an area (Poland).

There is no query service, only per-powiat downloads (zipped GML, about 30 MB; the buildings file unpacks to
over 100 MB). The first request for a powiat downloads and stream-parses it once into a compact cached list
(centroid, function, floors, footprint); later requests for any area in that powiat take milliseconds.
Gotcha: the GML coordinates are EPSG:2180 easting then northing (checked: 520 powiat buildings within 500 m of
Kąty Wrocławskie's centre that way round, none the other way).
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import numpy as np
import shapely

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

INDEX = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k"
OT = "{urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0}"
GML = "{http://www.opengis.net/gml/3.2}"
TTL_S = 180 * 24 * 3600  # powiats are updated every year or two
MAX_KM2 = 250.0
FUNCTIONS = {  # funkcjaOgolnaBudynku -> short key
    "budynki mieszkalne": "residential",
    "budynki biurowe": "offices",
    "budynki handlowo-usługowe": "retail_services",
    "budynki przemysłowe": "industrial",
    "zbiorniki, silosy i budynki magazynowe": "storage",
    "budynki transportu i łączności": "transport",
    "budynki szpitali i zakładów opieki medycznej": "healthcare",
    "budynki oświaty, nauki i kultury oraz budynki sportowe": "education_culture_sport",
    "budynki produkcyjne, usługowe i gospodarcze dla rolnictwa": "agricultural",
    "budynki kultu religijnego": "religious",
    "pozostałe budynki niemieszkalne": "other_non_residential",
    "budynki zakwaterowania turystycznego": "tourist_accommodation",
}


def _powiats(bounds_wgs84: tuple[float, float, float, float]) -> list[tuple[str, str]]:
    """(TERYT, GML download URL) of the powiats whose extent meets the bbox."""
    minx, miny, maxx, maxy = bounds_wgs84
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": "ms:BDOT10k_powiaty",
        "BBOX": f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326",  # lat,lon with this CRS URN
    }
    with http.client(120) as c:
        text = http.with_retries(lambda: http.check(c.get(INDEX, params=params)).text)
    terc = re.findall(r"<ms:TERYT>(\d{4})</ms:TERYT>", text)
    urls = re.findall(r"<ms:URL_GML>([^<]+)</ms:URL_GML>", text)
    return sorted(set(zip(terc, urls, strict=False)))


def _buildings(terc: str, url: str) -> dict[str, list[Any]]:
    """Columns x, y (centroid, EPSG:2180), function key, floors, footprint m² for every building in a powiat."""
    key = f"bdot|v1|{terc}"
    if (hit := cache.get("bdot10k", key)) is not None:
        return hit
    with http.client(300) as c:
        raw = http.with_retries(lambda: http.check(c.get(url)).content, timeout_s=900)
    cols: dict[str, list[Any]] = {"x": [], "y": [], "fn": [], "floors": [], "m2": []}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next((n for n in z.namelist() if n.endswith("OT_BUBD_A.xml")), None)
        if name is None:
            raise GeomcpError("source_error", f"The BDOT10k package for powiat {terc} has no buildings file.")
        with z.open(name) as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if el.tag != f"{OT}OT_BUBD_A":
                    continue
                if el.findtext(f"{OT}kategoriaIstnienia") == "eksploatowany":
                    pos = el.find(f".//{GML}posList")
                    if pos is not None and pos.text:
                        xy = np.array(pos.text.split(), dtype=float).reshape(-1, 2)  # easting, northing
                        x, y = xy[:, 0], xy[:, 1]
                        cols["x"].append(round(float(x[:-1].mean()), 1))
                        cols["y"].append(round(float(y[:-1].mean()), 1))
                        cols["m2"].append(round(abs(float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))) / 2))
                        general = el.findtext(f"{OT}funkcjaOgolnaBudynku") or ""
                        cols["fn"].append(FUNCTIONS.get(general.strip(), "other_non_residential"))
                        cols["floors"].append(int(el.findtext(f"{OT}liczbaKondygnacji") or 0))
                el.clear()  # keep memory flat while streaming 100+ MB
    cache.put("bdot10k", key, cols, TTL_S)
    return cols


class Bdot10kAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="bdot10k",
        name="GUGiK BDOT10k topographic database (buildings)",
        variables=[
            VariableInfo(
                name="building_functions",
                units="count",
                topic="infrastructure",
                description="Buildings in the area by function (residential, offices, retail and services, "
                "industrial, storage, agricultural, education and culture, healthcare, ...), with total "
                "footprint (m²) and mean number of floors",
                static=True,
            )
        ],
        access_pattern="pixels",
        resolution="building outlines (1:10,000)",
        coverage=(date(2021, 1, 1), None),
        latency="P2Y",
        licence="open data (PZGiK)",
        attribution="Główny Urząd Geodezji i Kartografii, BDOT10k",
        commercial_safe=True,
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError("static_variable", "Building functions are a current snapshot.", "Use get_summary.")

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        return 2.0 if cache.get("bdot10k", f"bdot-area|v1|{area.area_id}") is not None else 90.0

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        if area.area_km2 > MAX_KM2:
            raise GeomcpError("area_too_large", f"Building functions are counted for areas up to {MAX_KM2:.0f} km².")
        akey = f"bdot-area|v1|{area.area_id}"
        cost = Cost(cache_hits=1)
        value: dict[str, float | None] | None = cache.get("bdot10k", akey)
        if value is None:
            cost = Cost(cache_misses=1)
            wgs = geometry_of(area)
            c = wgs.centroid
            if not (14.07 <= c.x <= 24.15 and 49.0 <= c.y <= 54.84):
                raise GeomcpError("out_of_coverage", "BDOT10k covers Poland only.")
            geom = project(wgs, "EPSG:4326", "EPSG:2180")
            shapely.prepare(geom)
            counts: dict[str, int] = {}
            m2 = 0.0
            floors: list[int] = []
            for terc, url in _powiats(wgs.bounds):
                cols = _buildings(terc, url)
                x, y = np.array(cols["x"]), np.array(cols["y"])
                inside = shapely.contains_xy(geom, x, y)
                for i in np.flatnonzero(inside):
                    counts[cols["fn"][i]] = counts.get(cols["fn"][i], 0) + 1
                    m2 += cols["m2"][i]
                    if cols["floors"][i]:
                        floors.append(cols["floors"][i])
            value = {k: float(v) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}
            value["total"] = float(sum(counts.values()))
            value["footprint_m2"] = round(m2)
            value["mean_floors"] = round(sum(floors) / len(floors), 1) if floors else 0.0
            cache.put("bdot10k", akey, value, TTL_S)
        return [
            Fact(
                variable="building_functions",
                units="count (footprint in m², floors as a mean)",
                value=value,
                provenance=Provenance(
                    source=self.info.name,
                    backend="per-powiat GML download, parsed once and cached",
                    collection="OT_BUBD_A (existing buildings)",
                    method="buildings whose outline centroid lies inside the area, by general function "
                    "(funkcjaOgolnaBudynku)",
                    licence=self.info.licence,
                    attribution=self.info.attribution,
                    commercial_safe=True,
                    retrieved_at=datetime.now(UTC),
                ),
            )
        ], cost
