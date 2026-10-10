"""GIOŚ (Chief Inspectorate of Environmental Protection) air-quality stations: current conditions and history.

The public API holds only the last ~3 days of hourly data, so the *_station variables are current snapshots
(last 24 hours at the nearest station measuring the pollutant), reported through get_summary.
The *_history variables are verified annual means since 2000 from the GIOŚ statistics service.
Timestamps are Polish local time (guide §10 gotcha 17) and are converted to UTC.
"""

import math
import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import httpx

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

API = "https://api.gios.gov.pl/pjp-api/v1/rest"
WARSAW = ZoneInfo("Europe/Warsaw")
MAX_DISTANCE_KM = 25.0
MIN_INTERVAL_S = 2.0  # the API is rate-limited (guide §10 gotcha 18); station and sensor lists are cached
_lock = threading.Lock()
_last = 0.0

# variable -> (GIOŚ indicator code, description)
POLLUTANTS: dict[str, tuple[str, str]] = {
    "pm10_station": ("PM10", "PM10 at the nearest monitoring station"),
    "pm25_station": ("PM2.5", "PM2.5 at the nearest monitoring station"),
    "no2_station": ("NO2", "NO2 at the nearest monitoring station"),
    "o3_station": ("O3", "Ozone at the nearest monitoring station"),
}
# variable -> (statistics indicator code, description); verified annual means, 2000 to last year
HISTORY: dict[str, tuple[str, str]] = {
    "pm10_history": ("PM10", "PM10 annual mean per year at the nearest station with history (EU limit 40)"),
    "pm25_history": ("PM2,5", "PM2.5 annual mean per year at the nearest station with history (EU limit 25)"),
    "no2_history": ("NO2", "NO2 annual mean per year at the nearest station with history (EU limit 40)"),
}


def _get(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """GET returning the response's list (its key is a Polish label), politely rate-limited."""
    global _last
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            with http.client(60) as c:
                payload: dict[str, Any] = http.with_retries(
                    lambda: http.json_of(c.get(f"{API}/{path}", params=params or {}))
                )
        finally:
            _last = time.monotonic()
    for value in payload.values():  # the list sits under a Polish label that differs per endpoint
        if isinstance(value, list):
            rows: list[dict[str, Any]] = value  # pyright: ignore[reportUnknownVariableType]
            return rows
    return []


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _stations() -> list[dict[str, Any]]:
    if (hit := cache.get("gios", "stations")) is None:
        hit = _get("station/findAll", {"size": 500})
        cache.put("gios", "stations", hit, 24 * 3600)
    return hit


def _sensors(station_id: int) -> list[dict[str, Any]]:
    if (hit := cache.get("gios", f"sensors|{station_id}")) is None:
        hit = _get(f"station/sensors/{station_id}")
        cache.put("gios", f"sensors|{station_id}", hit, 24 * 3600)
    return hit


def _hourly(sensor_id: int) -> list[tuple[datetime, float]]:
    """(UTC time, value) pairs, newest first; cached for 30 minutes."""
    key = f"data|{sensor_id}"
    if (hit := cache.get("gios", key)) is None:
        hit = _get(f"data/getData/{sensor_id}", {"size": 100})  # 100 hours; larger pages are rejected
        cache.put("gios", key, hit, 1800)
    out: list[tuple[datetime, float]] = []
    for row in hit:
        value = row.get("Wartość")
        if value is None:
            continue
        local = datetime.strptime(row["Data"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW)
        out.append((local.astimezone(UTC), float(value)))
    return sorted(out, reverse=True)


def _statistics(indicator: str, voivodeship: str) -> list[dict[str, Any]]:
    """Annual statistics rows for one pollutant in one voivodeship, cached for 30 days (published yearly)."""
    key = f"stats|{indicator}|{voivodeship}"
    if (hit := cache.get("gios", key)) is not None:
        return hit
    rows: list[dict[str, Any]] = []
    page = 0
    while True:
        part = _get(
            "statistics/getStatisticsForPollutants",
            {"indicator": indicator, "filter[wojewodztwo]": voivodeship, "size": 500, "page": page},
        )
        rows.extend(part)
        if len(part) < 500:
            break
        page += 1
    cache.put("gios", key, rows, 30 * 24 * 3600)
    return rows


def _station_label(station: dict[str, Any]) -> str:
    name, town = str(station["Nazwa stacji"]), str(station.get("Nazwa miasta") or "")
    return name if not town or name.startswith(town) else f"{town}, {name}"


class GiosAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="gios",
        name="GIOŚ air-quality monitoring stations",
        variables=[
            VariableInfo(name=n, units="ug m-3", topic="air quality", description=d, static=True)
            for n, (_, d) in (POLLUTANTS | HISTORY).items()
        ],
        access_pattern="timeseries",
        resolution=f"station (nearest within {MAX_DISTANCE_KM:.0f} km)",
        coverage=(date(2026, 1, 1), None),
        latency="PT1H",
        licence="CC-BY-4.0",
        attribution="Główny Inspektorat Ochrony Środowiska (GIOŚ), Państwowy Monitoring Środowiska",
        commercial_safe=True,
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise GeomcpError(
            "static_variable", "GIOŚ station data is a current snapshot (last 24 h).", "Use get_summary for it."
        )

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        return 15.0  # a few rate-limited calls; station data is cached for only 30 minutes

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        c = geometry_of(area).centroid
        nearby = sorted(
            (
                (_km(c.y, c.x, float(s["WGS84 φ N"]), float(s["WGS84 λ E"])), s)
                for s in _stations()
                if s.get("WGS84 φ N") and s.get("WGS84 λ E")
            ),
            key=lambda x: x[0],
        )
        nearby = [x for x in nearby if x[0] <= MAX_DISTANCE_KM]
        if not nearby:
            raise GeomcpError("no_station", f"No GIOŚ station within {MAX_DISTANCE_KM:.0f} km of this area.")
        facts: list[Fact] = [f for v in variables if v in HISTORY if (f := self._history(v, nearby))]
        for variable in (v for v in variables if v in POLLUTANTS):
            code = POLLUTANTS[variable][0]
            for distance, station in nearby:
                sensor = next(
                    (s for s in _sensors(station["Identyfikator stacji"]) if s["Wskaźnik - kod"] == code), None
                )
                if sensor is None:
                    continue
                try:
                    hourly = _hourly(sensor["Identyfikator stanowiska"])
                except (httpx.HTTPStatusError, GeomcpError):
                    continue  # this sensor is unavailable: try the next-nearest station
                if not hourly:
                    continue
                latest_time, latest = hourly[0]
                day = [v for t, v in hourly if t > latest_time - timedelta(hours=24)]
                facts.append(
                    Fact(
                        variable=variable,
                        units="ug m-3",
                        value={
                            "last_24h_mean": round(sum(day) / len(day), 1),
                            "latest_hour": latest,
                            "hours": len(day),
                        },
                        period=f"{(latest_time - timedelta(hours=23)):%Y-%m-%dT%H:00Z}/{latest_time:%Y-%m-%dT%H:00Z}",
                        status="preliminary",
                        provenance=Provenance(
                            source=self.info.name,
                            backend="gios-api-v1",
                            collection=f"{station['Kod stacji']} / {sensor['Identyfikator stanowiska']}",
                            method=f"station {_station_label(station)}, {distance:.1f} km "
                            "from the area centroid; "
                            "hourly values (unverified operational data), local time converted to UTC",
                            licence=self.info.licence,
                            attribution=self.info.attribution,
                            commercial_safe=True,
                            retrieved_at=datetime.now(UTC),
                        ),
                    )
                )
                break
        return facts, Cost()

    def _history(self, variable: str, nearby: list[tuple[float, dict[str, Any]]]) -> Fact | None:
        """Annual means at the nearest station whose series is current (data in the latest or previous
        published year) and spans at least five years; stations that stopped measuring are passed over."""
        code = HISTORY[variable][0]
        series: list[tuple[float, dict[str, Any], dict[str, float]]] = []
        for distance, station in nearby:
            rows = [
                r
                for r in _statistics(code, str(station["Województwo"]).lower())
                if r["Kod stacji"] == station["Kod stacji"]
            ]
            by_year: dict[str, tuple[float, float]] = {}  # year -> (completeness, mean)
            for r in rows:
                mean, done = r.get("Średnia [µg/m3]"), float(r.get("Kompletność [%]") or 0)
                # a year under 75% complete misrepresents it (often a summer-only record, far below the winter smog)
                if mean is not None and done >= 75 and done >= by_year.get(str(r["Rok"]), (-1.0, 0.0))[0]:
                    by_year[str(r["Rok"])] = (done, round(float(mean), 1))
            if len(by_year) >= 5:
                series.append((distance, station, {y: by_year[y][1] for y in sorted(by_year)}))
        if not series:
            return None
        newest = max(int(max(values)) for _, _, values in series)
        distance, station, values = next(x for x in series if int(max(x[2])) >= newest - 1)
        years = list(values)
        return Fact(
            variable=variable,
            units="ug m-3",
            value={y: values[y] for y in years},
            period=f"{years[0]}/{years[-1]}",
            provenance=Provenance(
                source=self.info.name,
                backend="gios-api-v1 statistics",
                collection=str(station["Kod stacji"]),
                method=f"annual mean per year at station {_station_label(station)}, {distance:.1f} km from the "
                "area centroid: the nearest station with a current series of at least five years (verified "
                "data, years at least 75% complete; the most complete averaging series per year)",
                licence=self.info.licence,
                attribution=self.info.attribution,
                commercial_safe=True,
                retrieved_at=datetime.now(UTC),
            ),
        )
