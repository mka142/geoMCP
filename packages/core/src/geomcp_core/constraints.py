# pyright: basic
# rasterio ships without type information; this module is a typed boundary around it.
"""Hazards and constraints for an area in Poland: flood zones, protected nature, geological hazards, ground
motion and current weather and hydrological warnings.

Sources and access (all verified live, October 2026):
- ISOK flood hazard maps (Wody Polskie): WMS per scenario; the layers draw only at 5 m/pixel or finer, so the
  area is rendered in tiles and flooded pixels inside the polygon are counted. Only the water-depth and
  hazard-zone layers are drawn: the services also publish rivers, lakes, embankments, labels and a sheet index,
  which are not flood zones.
- GDOŚ nature protection: GeoServer WFS with GeoJSON output.
- PIG-PIB: ArcGIS REST for SOPO landslides and MIDAS mining terrains, and the EGMS 2016-2020 vertical
  ground-motion image service (statistics over the polygon).
- IMGW: current meteorological warnings (by powiat TERYT) and hydrological warnings (by voivodeship).
"""

import io
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

from geomcp_core import cache, http
from geomcp_core.areas import area_from_id, geometry_of, project
from geomcp_core.models import Area, GeomcpError

ISOK = "https://wody.isok.gov.pl/gpservices/KZGW/{service}/MapServer/WMSServer"
FLOOD_SCENARIOS = {
    "flood_10pct": ("MZP20_Glebokosc_WysokiePrawdopodPowodzi", "Flood zone, 10% a year (once in 10 years)"),
    "flood_1pct": ("MZP20_Glebokosc_SredniePrawdopodPowodzi", "Flood zone, 1% a year (once in 100 years)"),
    "flood_02pct": ("MZP20_Glebokosc_NiskiePrawdopodPowodzi", "Flood zone, 0.2% a year (once in 500 years)"),
    "flood_embankment_failure": (
        "MZP20_Glebokosc_ZniszczenieWaluPowodziowego",
        "Flood zone if a flood embankment fails",
    ),
}
FLOOD_M_PER_PX = 5.0  # the ISOK layers do not draw at coarser scales
# the flood extent itself: water depth (a raster) and the hazard-zone polygon, by the ids in the layers' abstracts;
# a 1% or 0.2% service holds a river and a sea variant of each
_FLOOD_LAYER = re.compile(
    r"<Name>(\d+)</Name>\s*<Title>(?:(?!</Title>).)*</Title>\s*"  # a title never spans into the next layer
    r"<Abstract><!\[CDATA\[\[id=mzp\.(?:glebokosc|obszar_zagrozenia)",
    re.S,
)
FLOOD_TILE_PX = 1500
FLOOD_MAX_KM2 = 250.0

GDOS = "https://sdi.gdos.gov.pl/wfs"
PROTECTED = {
    "natura2000_habitats": ("GDOS:SpecjalneObszaryOchrony", "Natura 2000 habitat site (SOO)"),
    "natura2000_birds": ("GDOS:ObszarySpecjalnejOchrony", "Natura 2000 bird site (OSO)"),
    "national_park": ("GDOS:ParkiNarodowe", "National park"),
    "landscape_park": ("GDOS:ParkiKrajobrazowe", "Landscape park"),
    "nature_reserve": ("GDOS:Rezerwaty", "Nature reserve"),
    "protected_landscape": ("GDOS:ObszaryChronionegoKrajobrazu", "Protected landscape area"),
    "ecological_use_area": ("GDOS:UzytkiEkologiczne", "Ecological use area (użytek ekologiczny)"),
    "nature_landscape_complex": ("GDOS:ZespolyPrzyrodniczoKrajobrazowe", "Nature and landscape complex"),
}

PIG = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services"
GEOLOGY = {
    "landslides": ("geozagrozenia/sopo_obszary/MapServer/14", "Landslides (SOPO)", ("NAZWA", "NR_OSUWISKA")),
    "landslide_prone": ("geozagrozenia/sopo_obszary/MapServer/12", "Areas threatened by mass movement (SOPO)", ()),
    "mining_terrain": ("midas/MapServer/2", "Mining terrain (teren górniczy)", ("NAZWA_TG", "KOPALINA")),
    "mining_area": ("midas/MapServer/1", "Mining area (obszar górniczy)", ("NAZWA_OG", "KOPALINA")),
    "mineral_deposit": ("midas/MapServer/0", "Documented mineral deposit (złoże)", ("NAZWA_ZLOZA", "KOPALINA")),
}
EGMS = f"{PIG}/geozagrozenia/egms_2016_2020_pion_interpol/ImageServer/computeStatisticsHistograms"

IMGW_METEO = "https://danepubliczne.imgw.pl/api/data/warningsmeteo"
IMGW_HYDRO = "https://danepubliczne.imgw.pl/api/data/warningshydro"

# TERYT voivodeship codes, as IMGW names them in hydrological warnings
VOIVODESHIPS = {
    "02": "dolnośląskie", "04": "kujawsko-pomorskie", "06": "lubelskie", "08": "lubuskie", "10": "łódzkie",
    "12": "małopolskie", "14": "mazowieckie", "16": "opolskie", "18": "podkarpackie", "20": "podlaskie",
    "22": "pomorskie", "24": "śląskie", "26": "świętokrzyskie", "28": "warmińsko-mazurskie",
    "30": "wielkopolskie", "32": "zachodniopomorskie",
}  # fmt: skip

STATIC_TTL_S = 30 * 24 * 3600
PARTIAL_TTL_S = 24 * 3600  # a result with gaps is kept a day, so a provider outage heals without a cache purge
WARNING_TTL_S = 1800
GDOS_COUNT = 200
Group = tuple[list["Constraint"], list[str]]  # what a source group found, and what it could not answer


class Constraint(BaseModel):
    key: str
    category: str
    label: str
    share: float | None = Field(description="Share of the area inside (0-1); None when not an area measure")
    count: int = 0
    names: list[str] = Field(default_factory=list)
    value: dict[str, Any] = Field(default_factory=dict)
    source: str
    licence: str


class ConstraintsResult(BaseModel):
    area: str
    area_km2: float
    items: list[Constraint]
    gaps: list[str]
    notices: list[str]
    retrieved_at: datetime


def _metric(geom: BaseGeometry) -> BaseGeometry:
    return project(geom, "EPSG:4326", "EPSG:2180")


def _json(url: str, params: dict[str, Any], post: bool = False) -> Any:
    """A JSON answer; ArcGIS reports failures as `{"error": ...}` with HTTP 200, which must not read as "none here"."""
    with http.client(120) as c:
        if post:  # polygons with many vertices exceed URL limits
            r = http.with_retries(lambda: http.check(c.post(url, data=params)))
        else:
            r = http.with_retries(lambda: http.check(c.get(url, params=params)))
    try:
        payload = r.json()
    except ValueError as e:
        raise GeomcpError("source_error", f"{r.url.host} answered with something other than JSON.") from e
    if isinstance(payload, dict) and "error" in payload:
        error: Any = payload["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        raise GeomcpError("source_error", f"{r.url.host}: {message}")
    return payload


def _features(payload: dict[str, Any], label: str, cap: int | None) -> list[dict[str, Any]]:
    """The features of an ArcGIS or GeoServer answer, refusing a truncated one (its shares would be too small).

    ArcGIS flags truncation itself; GeoServer does not, so a page filled to the requested `cap` counts as truncated.
    """
    features: list[dict[str, Any]] = payload.get("features", [])
    truncated = payload.get("exceededTransferLimit") or (payload.get("properties") or {}).get("exceededTransferLimit")
    if truncated or (cap is not None and len(features) >= cap):
        raise GeomcpError("source_error", f"{label}: more features than the service returns at once.")
    return features


def _overlap(
    area_geom: BaseGeometry, features: list[dict[str, Any]], name_fields: tuple[str, ...]
) -> tuple[float, int, list[str]]:
    """Share of the area covered by the features (EPSG:2180), the number touching it and their names."""
    area_m = _metric(area_geom)
    covered: BaseGeometry | None = None
    names: list[str] = []
    count = 0
    for f in features:
        if not f.get("geometry"):
            continue
        g = _metric(shape(f["geometry"])).buffer(0)
        if not g.intersects(area_m):
            continue
        count += 1
        part = g.intersection(area_m)
        covered = part if covered is None else covered.union(part)
        props: dict[str, Any] = f.get("properties") or {}
        label = " - ".join(str(props[k]).strip() for k in name_fields if props.get(k))
        if label and label not in names:
            names.append(label)
    share = 0.0 if covered is None else min(1.0, covered.area / area_m.area)
    return round(share, 4), count, names[:10]


def _gdos(area: Area, geom: BaseGeometry) -> Group:
    minx, miny, maxx, maxy = geom.bounds
    out: list[Constraint] = []
    gaps: list[str] = []
    for key, (layer, label) in PROTECTED.items():
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "bbox": f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::4326",  # lat,lon order for this CRS URN
            "count": GDOS_COUNT,
        }
        try:
            features = _features(_json(GDOS, params), label, GDOS_COUNT)
        except GeomcpError as e:
            gaps.append(f"{label}: {e.message}")
            continue
        share, count, names = _overlap(geom, features, ("nazwa", "kod"))
        out.append(
            Constraint(
                key=key,
                category="protected nature",
                label=label,
                share=share,
                count=count,
                names=names,
                source="GDOŚ (Generalna Dyrekcja Ochrony Środowiska)",
                licence="public data (GDOŚ)",
            )
        )
    return out, gaps


def _pig(area: Area, geom: BaseGeometry) -> Group:
    minx, miny, maxx, maxy = geom.bounds
    envelope = {"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy, "spatialReference": {"wkid": 4326}}
    out: list[Constraint] = []
    gaps: list[str] = []
    for key, (path, label, fields) in GEOLOGY.items():
        params = {
            "geometry": json.dumps(envelope),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
        }
        try:
            features = _features(_json(f"{PIG}/{path}/query", params), label, None)
        except GeomcpError as e:  # the SOPO landslide layers refuse queries (October 2026): a gap, not "none"
            gaps.append(f"{label}: {e.message}")
            continue
        share, count, names = _overlap(geom, features, fields)
        out.append(
            Constraint(
                key=key,
                category="geological hazards",
                label=label,
                share=share,
                count=count,
                names=names,
                source="PIG-PIB (Polish Geological Institute)",
                licence="public data (PIG-PIB)",
            )
        )
    hull = geom.convex_hull
    ring = [list(p) for p in hull.exterior.coords] if isinstance(hull, Polygon) else []
    try:
        stats = _json(
            EGMS,
            {
                "geometry": json.dumps({"rings": [ring], "spatialReference": {"wkid": 4326}}),
                "geometryType": "esriGeometryPolygon",
                "f": "json",
            },
            post=True,
        ).get("statistics", [{}])[0]
    except GeomcpError as e:
        gaps.append(f"ground motion (EGMS): {e.message}")
        stats = {}
    if stats.get("count"):
        out.append(
            Constraint(
                key="ground_motion_vertical",
                category="geological hazards",
                label="Vertical ground motion 2016-2020 (EGMS, interpolated), mm/year; negative is subsidence",
                share=None,
                value={k: round(float(stats[k]), 1) for k in ("mean", "median", "min", "max") if k in stats},
                source="Copernicus EGMS via PIG-PIB",
                licence="Copernicus open data",
            )
        )
    return out, gaps


def flood_layers(capabilities_xml: str) -> str:
    """The WMS layer ids of the flood extent (depth and hazard zone), as a LAYERS parameter."""
    names = _FLOOD_LAYER.findall(capabilities_xml)
    if not names:
        raise GeomcpError("source_error", "The ISOK flood service lists no depth or hazard-zone layer.")
    return ",".join(names)


def _flood_share(geom: BaseGeometry, service: str) -> float:
    """Share of the polygon inside the scenario's flood zone, from tiled WMS renders in CRS:84."""
    caps_key = f"isok-layers|v2|{service}"
    if (layers := cache.get("constraints", caps_key)) is None:
        with http.client(60) as c:
            xml = http.with_retries(
                lambda: (
                    http.check(
                        c.get(ISOK.format(service=service), params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"})
                    ).text
                )
            )
        layers = flood_layers(xml)
        cache.put("constraints", caps_key, layers, STATIC_TTL_S)
    minx, miny, maxx, maxy = geom.bounds
    lat = (miny + maxy) / 2
    width_m = (maxx - minx) * 111_320 * math.cos(math.radians(lat))
    height_m = (maxy - miny) * 110_574
    nx = max(1, math.ceil(width_m / FLOOD_M_PER_PX / FLOOD_TILE_PX))
    ny = max(1, math.ceil(height_m / FLOOD_M_PER_PX / FLOOD_TILE_PX))
    px_x = math.ceil(width_m / FLOOD_M_PER_PX / nx)
    px_y = math.ceil(height_m / FLOOD_M_PER_PX / ny)
    flooded = inside = 0

    def tile(ij: tuple[int, int]) -> tuple[int, int]:
        from rasterio.features import geometry_mask
        from rasterio.transform import from_bounds

        i, j = ij
        x0, x1 = minx + (maxx - minx) * i / nx, minx + (maxx - minx) * (i + 1) / nx
        y0, y1 = miny + (maxy - miny) * j / ny, miny + (maxy - miny) * (j + 1) / ny
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "LAYERS": layers,
            "STYLES": "",
            "CRS": "CRS:84",
            "BBOX": f"{x0},{y0},{x1},{y1}",
            "WIDTH": px_x,
            "HEIGHT": px_y,
            "FORMAT": "image/png32",
            "TRANSPARENT": "TRUE",
        }
        with http.client(120) as c:
            png = http.with_retries(lambda: http.check(c.get(ISOK.format(service=service), params=params)).content)
        rgba = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA")).astype(np.int32)
        # the service paints an opaque white background despite TRANSPARENT=TRUE: count coloured pixels
        alpha = (rgba[..., 3] > 0) & (rgba[..., :3].sum(axis=-1) < 740)
        mask = geometry_mask(
            [geom.__geo_interface__],
            out_shape=alpha.shape,
            transform=from_bounds(x0, y0, x1, y1, px_x, px_y),
            invert=True,
        )
        return int((alpha & mask).sum()), int(mask.sum())

    with ThreadPoolExecutor(max_workers=4) as pool:
        for f, n in pool.map(tile, [(i, j) for i in range(nx) for j in range(ny)]):
            flooded += f
            inside += n
    return round(flooded / inside, 4) if inside else 0.0


def _flood(area: Area, geom: BaseGeometry) -> Group:
    if area.area_km2 > FLOOD_MAX_KM2:
        raise GeomcpError("area_too_large", f"Flood zones are computed for areas up to {FLOOD_MAX_KM2:.0f} km².")
    out: list[Constraint] = []
    gaps: list[str] = []
    for key, (service, label) in FLOOD_SCENARIOS.items():
        try:
            share = _flood_share(geom, service)
        except (GeomcpError, httpx.HTTPStatusError) as e:
            gaps.append(f"{label}: {getattr(e, 'message', str(e))}")
            continue
        out.append(
            Constraint(
                key=key,
                category="flood",
                label=label,
                share=share,
                source="ISOK flood hazard maps (Wody Polskie)",
                licence="public data (Wody Polskie)",
            )
        )
    return out, gaps


NOISE_WMS = "https://iip.ekoportal.gov.pl/geoserver/wms"
NOISE = {  # source -> (label, layers: major infrastructure and agglomeration maps, round 3 of 2017)
    "road": (
        "Road noise",
        ("DF4_8_MRoad2017Lden:PL_a_Mroad_Lden", "DF4_8_AggRoad2017Lden:PL_a_AggRoad2017_Lden_polygon"),
    ),
    "rail": (
        "Rail noise",
        ("DF4_8_MRail2017Lden:PL_a_Mrail_2017_Lden", "DF4_8_AggRail2017Lden:PL_a_AggRail2017_Lden_Polygon"),
    ),
    "air": ("Aircraft noise", ("DF4_8_Mair2017:PL_a_Mair_Maps2017_Lden", "DF4_8_AggAir2017Lden:PL_a_Aggair2017_Lden")),
    "industry": ("Industrial noise", ("DF4_8_AggInd2017Lden:PL_a_AggInd2017_Lden_Polygon",)),
}  # fmt: skip
NOISE_M_PER_PX = 10.0
NOISE_MAX_PX = 2000


def _band_style(layer: str) -> str:
    """An SLD that paints each band's lower bound (dB) into the red channel, so pixels can be counted."""
    rules = "".join(
        "<Rule><ogc:Filter><ogc:PropertyIsEqualTo><ogc:PropertyName>db_low</ogc:PropertyName>"
        f"<ogc:Literal>{db}</ogc:Literal></ogc:PropertyIsEqualTo></ogc:Filter>"
        f'<PolygonSymbolizer><Fill><CssParameter name="fill">#{db:02x}0000</CssParameter></Fill></PolygonSymbolizer>'
        "</Rule>"
        for db in range(40, 100, 5)
    )
    return (
        '<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld" '
        f'xmlns:ogc="http://www.opengis.net/ogc"><NamedLayer><Name>{layer}</Name><UserStyle><FeatureTypeStyle>'
        f"{rules}</FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>"
    )


def _noise(area: Area, geom: BaseGeometry) -> Group:
    """Share of the area at Lden of 55 dB and more (annoyance) and 65 dB and more (health risk) per source."""
    from rasterio.features import geometry_mask
    from rasterio.transform import from_bounds

    minx, miny, maxx, maxy = geom.bounds
    lat = (miny + maxy) / 2
    width_m, height_m = (maxx - minx) * 111_320 * math.cos(math.radians(lat)), (maxy - miny) * 110_574
    scale = max(NOISE_M_PER_PX, max(width_m, height_m) / NOISE_MAX_PX)
    w, h = max(1, round(width_m / scale)), max(1, round(height_m / scale))
    inside = geometry_mask(
        [geom.__geo_interface__], out_shape=(h, w), transform=from_bounds(minx, miny, maxx, maxy, w, h), invert=True
    )

    def render(layer: str) -> np.ndarray:
        params = {
            "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": layer, "SRS": "EPSG:4326",
            "BBOX": f"{minx},{miny},{maxx},{maxy}", "WIDTH": w, "HEIGHT": h, "FORMAT": "image/png",
            "TRANSPARENT": "TRUE", "FORMAT_OPTIONS": "antialias:none", "SLD_BODY": _band_style(layer),
        }  # fmt: skip
        with http.client(120) as c:
            png = http.with_retries(lambda: http.check(c.post(NOISE_WMS, data=params)).content)
        rgba = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
        return np.where(rgba[..., 3] > 0, rgba[..., 0], 0).astype(np.int16)

    layers = [layer for _, ls in NOISE.values() for layer in ls]
    with ThreadPoolExecutor(max_workers=4) as pool:
        rendered = dict(zip(layers, pool.map(render, layers), strict=True))
    out: list[Constraint] = []
    n = int(inside.sum()) or 1
    for key, (label, ls) in NOISE.items():
        db = np.maximum.reduce([rendered[layer] for layer in ls])  # the louder of the two maps per pixel
        shares = {f"share_lden_{t}_db_plus": round(float(((db >= t) & inside).sum()) / n, 4) for t in (55, 65, 70)}
        out.append(
            Constraint(
                key=f"noise_{key}", category="noise", label=f"{label}, Lden 55 dB or more",
                share=shares["share_lden_55_db_plus"], value={k: v for k, v in shares.items() if v},
                source="GIOŚ strategic noise maps 2017 (round 3)", licence="CC-BY-4.0 (GIOŚ)",
            )
        )  # fmt: skip
    return out, []


IUNG = "https://susza.iung.pulawy.pl/wykazy/{year},{terc}/"
IUNG_SOIL = "https://sgeo.iung.pl/geoserver/Susza/wms"


def _soil_category(lon: float, lat: float) -> str | None:
    """IUNG soil category (I-IV) under a point, which decides which drought table applies."""
    params = {
        "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": "Susza:katgl_landuse2024",
        "query_layers": "Susza:katgl_landuse2024", "crs": "EPSG:4326",
        "bbox": f"{lat - 0.001},{lon - 0.001},{lat + 0.001},{lon + 0.001}", "width": 101, "height": 101,
        "i": 50, "j": 50, "info_format": "application/json",
    }  # fmt: skip
    with http.client(30) as c:
        features = http.with_retries(lambda: http.json_of(c.get(IUNG_SOIL, params=params))).get("features", [])
    value = int(features[0]["properties"].get("GRAY_INDEX", 0)) if features else 0
    return {1: "I", 2: "II", 3: "III", 4: "IV"}.get(value)


def _season() -> int:
    return datetime.now(UTC).year


def _drought(area: Area, terc: str) -> list[Constraint]:
    """IUNG agricultural drought monitoring for the gmina this season: crops at risk per soil category."""
    year = _season()
    key = f"iung|{year}|{terc}"
    if (tables := cache.get("constraints", key)) is None:
        with http.client(60) as c:
            page = http.with_retries(lambda: http.check(c.get(IUNG.format(year=year, terc=terc))).text)
        tables = {}
        for category, body in re.findall(r"Kategoria gleby (\w+)</h6>(.*?)</table>", page, re.S):
            periods = re.findall(r'<th title="([^"]+)">(\d\d)</th>', body)
            for crop, cells in re.findall(r'<td class="s-name[^"]*">([^<]+)</td>(.*?)</tr>', body, re.S):
                marks = re.findall(r"<td[^>]*>([^<]*)</td>", cells)
                risky = [periods[i][1] for i, m in enumerate(marks) if m.strip() == "+" and i < len(periods)]
                if risky:
                    tables.setdefault(category, []).append(f"{crop.strip()} (periods {', '.join(risky)})")
        cache.put("constraints", key, tables, 24 * 3600)  # updated every 10 days in season
    centre = geometry_of(area).centroid
    try:
        soil = _soil_category(centre.x, centre.y)
    except (GeomcpError, httpx.HTTPError):
        soil = None
    order = sorted(tables, key=lambda k: k != soil)  # the soil category under the area first
    names = [
        f"soil category {k}{' (under this area)' if k == soil else ''}: {crop}" for k in order for crop in tables[k]
    ]
    if soil and soil not in tables:
        names.insert(0, f"soil category {soil} (under this area): no crop at risk this season")
    return [
        Constraint(
            key="agricultural_drought", category="current warnings",
            label=f"IUNG agricultural drought risk for the gmina in {year} (crops and 10-day periods at risk)",
            share=None, count=len(names), names=names[:12],
            source="IUNG-PIB agricultural drought monitoring (SMSR)", licence="public information (IUNG-PIB)",
        )
    ]  # fmt: skip


def _warnings(area: Area, terc: str, voivodeship: str) -> list[Constraint]:
    with http.client(60) as c:
        meteo = http.with_retries(lambda: http.json_of(c.get(IMGW_METEO)))
        hydro = http.with_retries(lambda: http.json_of(c.get(IMGW_HYDRO)))
    # with nothing active IMGW answers {"message": "Brak ostrzeżeń ..."} instead of an empty list
    meteo = meteo if isinstance(meteo, list) else []
    hydro = hydro if isinstance(hydro, list) else []
    powiat = terc[:4]
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    met = [w for w in meteo if powiat in (w.get("teryt") or []) and w.get("obowiazuje_do", "9") >= now]
    hyd = [
        w
        for w in hydro
        if any((o.get("wojewodztwo") or "").lower() == voivodeship.lower() for o in w.get("obszary", []))
    ]
    return [
        Constraint(
            key="weather_warnings",
            category="current warnings",
            label="IMGW weather warnings for the powiat",
            share=None,
            count=len(met),
            names=[f"{w['nazwa_zdarzenia']} (level {w['stopien']}, until {w['obowiazuje_do']})" for w in met],
            source="IMGW-PIB",
            licence="IMGW-PIB terms (warnings are public)",
        ),
        Constraint(
            key="hydro_warnings",
            category="current warnings",
            label=f"IMGW hydrological warnings in {voivodeship.lower()} (floods, drought)",
            share=None,
            count=len(hyd),
            names=sorted(  # one entry per catchment, e.g. "dolnośląskie, Bystrzyca, susza hydrologiczna (level -1)"
                {
                    f"{o.get('opis') or w['zdarzenie']} (level {w['stopień']})"
                    for w in hyd
                    for o in w.get("obszary", [])
                    if (o.get("wojewodztwo") or "").lower() == voivodeship.lower()
                }
            ),
            source="IMGW-PIB",
            licence="IMGW-PIB terms (warnings are public)",
        ),
    ]


def estimate_s(area_id: str) -> float:
    """Expected seconds: flood tiles dominate (measured: 15 s for 10 km², 67 s for 177 km²)."""
    area = area_from_id(area_id)
    return (
        3.0 if cache.get("constraints", f"constraints|v4|{area.area_id}") is not None else 12.0 + 0.32 * area.area_km2
    )


def get_constraints(area_id: str) -> ConstraintsResult:
    from geomcp_core.adapters.gus_bdl import _commune_at  # pyright: ignore[reportPrivateUsage]

    area = area_from_id(area_id)
    geom = geometry_of(area)
    terc, _ = _commune_at(area)  # also fails clearly outside Poland
    key = f"constraints|v4|{area.area_id}"
    items: list[Constraint] = []
    gaps: list[str] = []
    notices: list[str] = []
    static: dict[str, list[Any]] | None = cache.get("constraints", key)
    if static is None:
        static = {"items": [], "gaps": []}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                name_: pool.submit(fn, area, geom)
                for name_, fn in (("flood", _flood), ("protected nature", _gdos), ("geology", _pig), ("noise", _noise))
            }
            for group, fut in futures.items():
                try:
                    found, missing = fut.result()
                    static["items"].extend(c.model_dump(mode="json") for c in found)
                    static["gaps"].extend(f"{group}: {m}" for m in missing)
                except GeomcpError as e:
                    static["gaps"].append(f"{group}: {e.message}")
                except httpx.HTTPStatusError as e:  # one provider changing its API must not sink the others
                    static["gaps"].append(f"{group}: {e.request.url.host} answered HTTP {e.response.status_code}")
        # a partial answer is cached as partial (its gaps travel with it) and expires soon
        cache.put("constraints", key, static, PARTIAL_TTL_S if static["gaps"] else STATIC_TTL_S)
    items.extend(Constraint.model_validate(c) for c in static["items"])
    gaps.extend(static["gaps"])
    try:
        voivodeship = VOIVODESHIPS[terc[:2]]
        items.extend(_warnings(area, terc, voivodeship))
        items.extend(_drought(area, terc))
    except GeomcpError as e:
        gaps.append(f"warnings: {e.message}")
    except httpx.HTTPStatusError as e:
        gaps.append(f"warnings: {e.request.url.host} answered HTTP {e.response.status_code}")
    if any(i.key == "ground_motion_vertical" for i in items):
        notices.append("Ground motion is interpolated between radar points (2016-2020); treat it as an indication.")
    return ConstraintsResult(
        area=area.name, area_km2=area.area_km2, items=items, gaps=gaps, notices=notices, retrieved_at=datetime.now(UTC)
    )
