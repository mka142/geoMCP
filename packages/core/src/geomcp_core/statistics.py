"""Official Polish statistics for any area: GUS Local Data Bank (BDL) and public-services monitoring (SMUP).

Indicator ids are prefixed by source: `bdl:72305`, `smup:10`.
Values come for the area's gmina and, for comparison, its powiat, voivodeship and Poland.
BDL searches by Polish words only; SMUP has Polish and English names and is searched locally.
"""

import threading
import time
from typing import Any

from pydantic import BaseModel, Field

from geomcp_core import cache, http
from geomcp_core.adapters.gus_bdl import BDL, _commune_at, _get, _unit  # pyright: ignore[reportPrivateUsage]
from geomcp_core.areas import area_from_id
from geomcp_core.config import env
from geomcp_core.models import GeomcpError

SMUP = "https://api.smup.gov.pl/api/1.0.0"
LEVEL_NAMES = {
    0: "Poland",
    1: "macroregion",
    2: "voivodeship",
    3: "region",
    4: "subregion",
    5: "powiat",
    6: "gmina",
    7: "gmina part",
}
COMPARE_LEVELS = ("gmina", "powiat", "voivodeship", "Poland")
LIST_TTL_S = 30 * 24 * 3600
DATA_TTL_S = 7 * 24 * 3600
_smup_lock = threading.Lock()
_smup_last = 0.0


class Indicator(BaseModel):
    id: str = Field(description="Pass to get_statistics, e.g. bdl:72305 or smup:10")
    source: str
    name: str
    unit: str | None
    level: str | None = Field(description="Lowest territorial level with data (gmina, powiat, voivodeship...)")
    topic: str | None = None


class IndicatorValues(BaseModel):
    indicator: Indicator
    values: dict[str, dict[str, float]] = Field(
        description="level -> year -> value, for gmina, powiat, voivodeship, Poland"
    )


class StatisticsResult(BaseModel):
    area: str
    gmina: str
    units: dict[str, str] = Field(description="level -> unit name, e.g. powiat -> Powiat wrocławski")
    indicators: list[IndicatorValues]
    notices: list[str]


def _smup_get(path: str, params: dict[str, Any]) -> Any:
    """SMUP GET with a client-side rate limit (anonymous: 5 per second, 100 per 15 minutes)."""
    global _smup_last
    headers = {"Accept": "application/json"}
    if key := env("GEOMCP_GUS_SMUP_KEY"):
        headers["X-ClientId"] = key
    with _smup_lock:
        wait = 0.3 - (time.monotonic() - _smup_last)
        if wait > 0:
            time.sleep(wait)
        try:
            with http.client(60) as c:
                url = f"{SMUP}/{path}"
                return http.with_retries(lambda: http.json_of(c.get(url, params=params, headers=headers)))
        finally:
            _smup_last = time.monotonic()


def _smup_indicators() -> list[dict[str, Any]]:
    """Every SMUP indicator with Polish and English names, cached for 30 days."""
    if (hit := cache.get("statistics", "smup|indicators|v1")) is not None:
        return hit
    rows: dict[int, dict[str, Any]] = {}
    for lang in ("pl", "en"):
        page = 1
        while True:
            payload = _smup_get("indicator-list", {"lang": lang, "page-size": 100, "page": page})
            for r in payload.get("data", []):
                row = rows.setdefault(r["id"], {"id": r["id"], "position": r.get("nazwa-pozycji")})
                row[f"name_{lang}"] = r["nazwa-wskaznika"]
            if page >= payload.get("page-count", 1):
                break
            page += 1
    out = list(rows.values())
    cache.put("statistics", "smup|indicators|v1", out, LIST_TTL_S)
    return out


def _subject(subject_id: str | None) -> str:
    """BDL subject name (e.g. 'Ludność wg grup wieku i płci'), cached; it gives variable names their context."""
    if not subject_id:
        return ""
    key = f"subject|{subject_id}"
    if (hit := cache.get("statistics", key)) is None:
        hit = str(_get(f"subjects/{subject_id}", {}).get("name", ""))
        cache.put("statistics", key, hit, LIST_TTL_S)
    return hit


def _bdl_name(v: dict[str, Any]) -> str:
    parts = [p for p in (v.get("n1"), v.get("n2"), v.get("n3"), v.get("n4"), v.get("n5")) if p]
    subject = _subject(v.get("subjectId"))
    return f"{subject}: {' / '.join(parts)}" if subject else " / ".join(parts)


def find_statistics(query: str, limit: int = 25) -> list[Indicator]:
    words = [w for w in query.lower().split() if w]
    if not words:
        raise GeomcpError("invalid_request", "Pass words to search for.")
    key = f"search|v1|{' '.join(words)}|{limit}"
    if (hit := cache.get("statistics", key)) is not None:  # searches are slow (up to minutes) and spend quota
        return [Indicator.model_validate(i) for i in hit]
    found: list[Indicator] = []
    candidates: dict[int, dict[str, Any]] = {}
    try:  # variable search is the slowest BDL call (66 s, or HTTP 500 after 2 min on bad days): one attempt
        for v in _get("variables/search", {"name": query, "page-size": 100}, attempts=1).get("results", []):
            candidates[v["id"]] = v
    except GeomcpError:
        variable_search_failed = True
    else:
        variable_search_failed = False
    # subjects match concepts better ("ludność", "wynagrodzenia"): add the variables of the best few
    for subject in _get("subjects/search", {"name": query, "page-size": 5}).get("results", [])[:3]:
        cache.put("statistics", f"subject|{subject['id']}", subject.get("name", ""), LIST_TTL_S)  # saves a call later
        if subject.get("hasVariables"):
            for v in _get("variables", {"subject-id": subject["id"], "page-size": 30}).get("results", []):
                candidates.setdefault(v["id"], v)
    rank = {6: 0, 5: 1, 2: 2, 0: 3, 7: 4}  # gmina and powiat indicators first; gmina parts last
    for v in sorted(candidates.values(), key=lambda v: rank.get(v.get("level", -1), 5))[:limit]:
        found.append(
            Indicator(
                id=f"bdl:{v['id']}",
                source="GUS BDL",
                name=_bdl_name(v),
                unit=v.get("measureUnitName"),
                level=LEVEL_NAMES.get(v.get("level", -1)),
                topic=v.get("subjectId"),
            )
        )
    for row in _smup_indicators():
        text = f"{row.get('name_pl', '')} {row.get('name_en', '')}".lower()
        if all(w[: max(4, len(w) - 3)] in text for w in words if len(w) > 2):  # stems: podatek ~ podatku
            position = row.get("position")
            name = row.get("name_pl") or row.get("name_en") or str(row["id"])
            if position and position != "Brak wymiaru":
                name += f" ({position})"
            found.append(Indicator(id=f"smup:{row['id']}", source="GUS SMUP", name=name, unit=None, level="gmina"))
    found = found[: limit * 2]
    if variable_search_failed and not found:
        raise GeomcpError("source_busy", "GUS BDL search is not responding.", hint="Try again later.")
    if not variable_search_failed:  # a partial answer is returned but not cached
        cache.put("statistics", key, [i.model_dump() for i in found], DATA_TTL_S)
    return found


def _hierarchy(bdl_gmina: str) -> list[tuple[str, str, str]]:
    """(level name, BDL unit id, unit name) for gmina, powiat, voivodeship and Poland."""
    key = f"hierarchy|{bdl_gmina}"
    if (hit := cache.get("statistics", key)) is not None:
        return [tuple(h) for h in hit]  # type: ignore[misc]
    out: list[tuple[str, str, str]] = []
    unit_id: str | None = bdl_gmina
    while unit_id:
        unit = _get(f"units/{unit_id}", {})
        level = {6: "gmina", 5: "powiat", 2: "voivodeship"}.get(unit.get("level", -1))
        if level:
            out.append((level, unit["id"], unit["name"]))
        unit_id = unit.get("parentId")
    out.append(("Poland", "000000000000", "Polska"))
    cache.put("statistics", key, [list(h) for h in out], LIST_TTL_S)
    return out


def _bdl_values(unit_id: str, variable_ids: list[str], years: list[int] | None) -> dict[str, dict[str, float]]:
    key = f"bdl|{unit_id}|{','.join(sorted(variable_ids))}|{years}"
    if (hit := cache.get("statistics", key)) is not None:
        return hit
    params: dict[str, Any] = {"var-id": variable_ids, "page-size": 100}
    if years:
        params["year"] = years
    out: dict[str, dict[str, float]] = {}
    for r in _get(f"data/by-unit/{unit_id}", params).get("results", []):
        out[str(r["id"])] = {str(v["year"]): float(v["val"]) for v in r.get("values", []) if v.get("val") is not None}
    cache.put("statistics", key, out, DATA_TTL_S)
    return out


def _smup_values(bdl_unit: str, indicator: str) -> dict[str, float]:
    key = f"smup|{bdl_unit}|{indicator}"
    if (hit := cache.get("statistics", key)) is not None:
        return hit
    teryt_key = f"smup-teryt|{bdl_unit}"
    if (teryt := cache.get("statistics", teryt_key)) is None:
        rows = _smup_get("teryt-dictionary", {"filters": f"kts-symbol==10{bdl_unit}", "page-size": 5}).get("data", [])
        teryt = rows[0]["id-teryt"] if rows else -1
        cache.put("statistics", teryt_key, teryt, LIST_TTL_S)
    if teryt == -1:
        return {}
    rows = _smup_get("indicator-date-data", {"id": indicator, "filters": f"id-teryt=={teryt}", "page-size": 100})
    out = {str(r["id-daty"])[:4]: float(r["wartosc"]) for r in rows.get("data", []) if r.get("wartosc") is not None}
    cache.put("statistics", key, out, DATA_TTL_S)
    return out


def get_statistics(area_id: str, indicator_ids: list[str], years: list[int] | None = None) -> StatisticsResult:
    if not indicator_ids:
        raise GeomcpError("invalid_request", "Pass at least one indicator id from find_statistics.")
    area = area_from_id(area_id)
    own_terc = area.admin.get("terc", "")
    terc, name = _commune_at(area)
    if len(own_terc) == 7 and own_terc != terc:
        terc, name = own_terc, area.name.removeprefix("gmina ").removeprefix("Gmina ")
    gmina_unit, gmina_name = _unit(terc, name)
    levels = _hierarchy(gmina_unit)
    notices: list[str] = []
    if len(own_terc) != 7:
        notices.append(f"Values are for gmina {gmina_name}, which contains this area, and its higher units.")
    bdl_ids = [i.split(":", 1)[1] for i in indicator_ids if i.startswith("bdl:")]
    bdl_by_level = {lvl: _bdl_values(unit, bdl_ids, years) for lvl, unit, _ in levels} if bdl_ids else {}
    meta: dict[str, Indicator] = {}
    for vid in bdl_ids:
        v = _get(f"variables/{vid}", {})
        if not v or "id" not in v:
            raise GeomcpError("unknown_indicator", f"GUS BDL has no indicator {vid}.", "Use find_statistics.")
        name = _bdl_name(v)
        meta[f"bdl:{vid}"] = Indicator(
            id=f"bdl:{vid}",
            source="GUS BDL",
            name=name,
            unit=v.get("measureUnitName"),
            level=LEVEL_NAMES.get(v.get("level", -1)),
            topic=v.get("subjectId"),
        )
    smup_names = (
        {f"smup:{r['id']}": r for r in _smup_indicators()} if any(i.startswith("smup:") for i in indicator_ids) else {}
    )
    results: list[IndicatorValues] = []
    for indicator_id in dict.fromkeys(indicator_ids):
        source, _, raw = indicator_id.partition(":")
        values: dict[str, dict[str, float]] = {}
        if source == "bdl":
            for lvl, _, _ in levels:
                if series := bdl_by_level[lvl].get(raw):
                    values[lvl] = series
            indicator = meta[indicator_id]
        elif source == "smup":
            if indicator_id not in smup_names:
                raise GeomcpError("unknown_indicator", f"SMUP has no indicator {raw}.", "Use find_statistics.")
            row = smup_names[indicator_id]
            for lvl, unit, _ in levels:
                if series := _smup_values(unit, raw):
                    values[lvl] = {y: v for y, v in series.items() if not years or int(y) in years}
            indicator = Indicator(
                id=indicator_id, source="GUS SMUP", name=row.get("name_pl") or raw, unit=None, level="gmina"
            )
        else:
            raise GeomcpError(
                "unknown_indicator", f"Unknown indicator id {indicator_id!r}.", "Ids look like bdl:72305."
            )
        if not years:  # keep the last six years so answers stay compact
            last = sorted({y for s in values.values() for y in s})[-6:]
            values = {lvl: {y: v for y, v in s.items() if y in last} for lvl, s in values.items()}
        values = {lvl: dict(sorted(s.items())) for lvl, s in values.items() if s}
        if not values:
            notices.append(f"{indicator_id}: no data for these units and years.")
        results.append(IndicatorValues(indicator=indicator, values=values))
    return StatisticsResult(
        area=area.name,
        gmina=gmina_name,
        units={lvl: name for lvl, _, name in levels},
        indicators=results,
        notices=notices,
    )


__all__ = ["BDL", "find_statistics", "get_statistics"]
