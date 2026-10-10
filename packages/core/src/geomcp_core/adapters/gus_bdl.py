"""Statistics Poland (GUS) Local Data Bank: municipal statistics for the gmina an area belongs to.

Gmina areas use their own TERYT code; any other area (a buffer, a parcel) uses the gmina containing its
centroid, found through GUGiK ULDK, which also gives the gmina name for one BDL unit search.
"""

import math
import threading
import time
from datetime import UTC, date, datetime
from typing import Any, ClassVar

import httpx

from geomcp_core import cache, http
from geomcp_core.areas import geometry_of
from geomcp_core.config import env
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

BDL = "https://bdl.stat.gov.pl/api/v1"
ULDK = "https://uldk.gugik.gov.pl/"
DATA_TTL_S = 30 * 24 * 3600

# variable -> (BDL variable id, units, description); budget income is divided by population
VARIABLES: dict[str, tuple[int, str, str]] = {
    "population": (72305, "persons", "Population on 31 December"),
    "population_density": (60559, "persons km-2", "Population per km²"),
    "net_migration": (453193, "per 1000 residents", "Net migration (in minus out) per 1,000 residents"),
    "natural_increase": (450551, "per 1000 residents", "Births minus deaths per 1,000 residents"),
    "dwellings_completed": (747060, "per 1000 residents", "New dwellings completed per 1,000 residents"),
    "new_businesses": (1548700, "per 10000 residents", "New private-sector businesses per 10,000 residents"),
    "budget_income_per_capita": (76037, "PLN", "Gmina budget income per resident"),
}


_lock = threading.Lock()
_last = 0.0
# anonymous BDL limits: 5 requests/s, 100 per 15 minutes, 1,000 per 12 hours (a key gives 5x more)
MIN_INTERVAL_S = 0.3
LONG_WAIT_S = 30


def _checked(r: httpx.Response) -> Any:
    """Long quota windows are not retried: each retry would spend more of the quota."""
    if r.status_code == 429:
        wait = int("".join(ch for ch in r.headers.get("Retry-After", "") if ch.isdigit()) or 0)
        if wait > LONG_WAIT_S:
            keyed = bool(env("GEOMCP_GUS_BDL_KEY"))
            raise GeomcpError(
                "source_busy",
                f"GUS BDL request limit reached; it resets in about {math.ceil(wait / 60)} min.",
                hint="Ask later." if keyed else "Set GEOMCP_GUS_BDL_KEY (free at api.stat.gov.pl) for 5x the limit.",
            )
        time.sleep(wait)  # a short window: wait as asked, then the retry loop tries again
    return http.json_of(r)


def _get(path: str, params: dict[str, Any], attempts: int = 4) -> Any:
    """BDL GET with a client-side rate limit; GEOMCP_GUS_BDL_KEY (free registration) raises the limits."""
    global _last
    headers = {"X-ClientId": key} if (key := env("GEOMCP_GUS_BDL_KEY")) else {}
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            with http.client(120) as c:  # variable searches took 66 s on a busy day (October 2026)
                url, query = f"{BDL}/{path}", params | {"format": "json"}
                return http.with_retries(lambda: _checked(c.get(url, params=query, headers=headers)), attempts=attempts)
        finally:
            _last = time.monotonic()


def _commune_at(area: Area) -> tuple[str, str]:
    """(TERYT, name) of the gmina containing the area's centroid, from GUGiK ULDK."""
    c = geometry_of(area).centroid
    key = f"commune-v2|{c.y:.4f}|{c.x:.4f}"
    if (hit := cache.get("gus_bdl", key)) is not None:
        return str(hit[0]), str(hit[1])
    params = {"request": "GetCommuneByXY", "xy": f"{c.x},{c.y},4326", "result": "teryt,commune"}
    with http.client(60) as client:
        text = http.with_retries(lambda: http.check(client.get(ULDK, params=params)).text)
    lines = text.strip().splitlines()
    if len(lines) < 2 or lines[0].strip() != "0":
        raise GeomcpError("outside_poland", "GUS statistics cover Polish gminas only; this area is outside Poland.")
    terc, name = lines[1].split("|")[:2]
    cache.put("gus_bdl", key, [terc.replace("_", ""), name], None)
    return terc.replace("_", ""), name


def _unit(terc: str, name: str) -> tuple[str, str]:
    """BDL unit id and name of a gmina; BDL ids embed the TERYT code ([2:4] voivodeship, [-5:] the rest)."""
    if (hit := cache.get("gus_bdl", f"unit|{terc}")) is not None:
        return str(hit[0]), str(hit[1])
    query = name.split(" (")[0]  # ULDK says "Wrocław (miasto)"; the TERYT match below picks the exact unit
    for u in _get("units/search", {"name": query, "level": 6, "page-size": 100}).get("results", []):
        if u["id"][2:4] + u["id"][-5:] == terc:
            cache.put("gus_bdl", f"unit|{terc}", [u["id"], u["name"]], None)
            return u["id"], u["name"]
    raise GeomcpError("unknown_gmina", f"GUS BDL has no gmina {name!r} with TERYT {terc}.")


class GusBdlAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="gus_bdl",
        name="GUS Local Data Bank (BDL)",
        variables=[
            VariableInfo(name=n, units=u, topic="population and economy", description=d, static=True)
            for n, (_, u, d) in VARIABLES.items()
        ],
        access_pattern="server_stats",
        resolution="gmina",
        coverage=(date(1995, 1, 1), None),
        latency="P1Y",
        licence="CC-BY-4.0",
        attribution="Statistics Poland (GUS), Local Data Bank",
        commercial_safe=True,
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError("static_variable", f"{variable} is published yearly per gmina.", "Use get_summary for it.")

    def _series(self, unit_id: str) -> tuple[dict[int, dict[int, float]], Cost]:
        key = f"data|{unit_id}|v1"
        if (hit := cache.get("gus_bdl", key)) is not None:
            return {int(v): {int(y): x for y, x in s.items()} for v, s in hit.items()}, Cost(cache_hits=1)
        ids = sorted({vid for vid, _, _ in VARIABLES.values()} | {72305})
        payload = _get(f"data/by-unit/{unit_id}", {"var-id": ids})
        series: dict[int, dict[int, float]] = {}
        for result in payload.get("results", []):
            series[int(result["id"])] = {int(v["year"]): float(v["val"]) for v in result["values"]}
        cache.put("gus_bdl", key, {str(k): {str(y): x for y, x in s.items()} for k, s in series.items()}, DATA_TTL_S)
        return series, Cost(cache_misses=1)

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        own_terc = area.admin.get("terc", "")
        terc, name = _commune_at(area)
        own = len(own_terc) == 7
        if own and own_terc != terc:  # centroid of a concave gmina outside it: trust the gmina's own code
            terc, name = own_terc, area.name.removeprefix("gmina ").removeprefix("Gmina ")
        unit_id, name = _unit(terc, name)
        series, cost = self._series(unit_id)
        population = series.get(72305, {})
        whose = f"gmina {name} (TERYT {terc})" + ("" if own else ", which contains this area")
        facts: list[Fact] = []
        for variable in variables:
            vid, unit, _ = VARIABLES[variable]
            values = dict(series.get(vid, {}))
            if variable == "budget_income_per_capita":
                values = {y: v / population[y] for y, v in values.items() if population.get(y)}
            if not values:
                continue
            latest = max(values)
            earlier = latest - 5 if latest - 5 in values else min(values)
            facts.append(
                Fact(
                    variable=variable,
                    units=unit,
                    value={"latest": round(values[latest], 2), f"in_{earlier}": round(values[earlier], 2)},
                    period=f"{earlier}/{latest}",
                    provenance=Provenance(
                        source=self.info.name,
                        backend="bdl-api",
                        collection=f"variable {vid}",
                        method=f"official yearly statistics for {whose}",
                        licence=self.info.licence,
                        attribution=self.info.attribution,
                        commercial_safe=True,
                        retrieved_at=datetime.now(UTC),
                    ),
                )
            )
        return facts, cost
