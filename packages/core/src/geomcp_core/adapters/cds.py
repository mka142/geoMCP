"""ERA5-Land weather from the Copernicus Climate Data Store point time-series dataset.

One CDS job per calendar year fetches hourly values for the grid cell nearest to the area centroid;
they are reduced to daily values, cached per year and grid cell, and aggregated locally.
Unlike the gridded ERA5-Land product, this dataset delivers hourly (not accumulated) precipitation
and radiation, so daily totals are plain sums (measured: 2024 at Wrocław sums to 821 mm).
"""

import csv
import io
import math
import threading
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from statistics import fmean
from typing import Any, ClassVar

from geomcp_core import cache, http
from geomcp_core.aggregate import How, to_periods
from geomcp_core.areas import geometry_of
from geomcp_core.config import env
from geomcp_core.models import (
    Area,
    Cost,
    GeomcpError,
    Health,
    Observation,
    Period,
    Provenance,
    SeriesData,
    SourceInfo,
    VariableInfo,
)

CDS_URL = "https://cds.climate.copernicus.eu/api/retrieve/v1"
DATASET = "reanalysis-era5-land-timeseries"
KEY_VAR = "GEOMCP_CDS_KEY"
LICENCE_PAGE = f"https://cds.climate.copernicus.eu/datasets/{DATASET}?tab=download"
LATENCY_DAYS = 6  # the dataset runs about 5 days behind real time
PRELIMINARY_DAYS = 90  # ERA5T: the latest ~3 months may still be revised
MAX_PARALLEL = 4
SECONDS_PER_YEAR_CHUNK = 25.0
_slots = threading.BoundedSemaphore(MAX_PARALLEL)
_key_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_key_locks_guard = threading.Lock()

CDS_VARIABLES = {
    "2m_temperature": "t2m",
    "2m_dewpoint_temperature": "d2m",
    "total_precipitation": "tp",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
    "surface_solar_radiation_downwards": "ssrd",
    "volumetric_soil_water_level_1": "swvl1",
}

Hourly = dict[str, list[float]]
Daily = Callable[[Hourly], float]


def _vpd_kpa(h: Hourly) -> float:
    def es(t_c: float) -> float:
        return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))

    return fmean(es(t - 273.15) - es(d - 273.15) for t, d in zip(h["t2m"], h["d2m"], strict=True))


# variable -> (units, period aggregation, daily reducer over hourly values, description)
DERIVED: dict[str, tuple[str, How, Daily, str]] = {
    "t2m": ("degC", "mean", lambda h: fmean(h["t2m"]) - 273.15, "Air temperature at 2 m, daily mean"),
    "t2m_max": ("degC", "mean", lambda h: max(h["t2m"]) - 273.15, "Daily maximum air temperature"),
    "t2m_min": ("degC", "mean", lambda h: min(h["t2m"]) - 273.15, "Daily minimum air temperature"),
    "precipitation": ("mm", "sum", lambda h: sum(max(0.0, x) for x in h["tp"]) * 1000, "Total precipitation"),
    "wind_speed": (
        "m s-1",
        "mean",
        lambda h: fmean(math.hypot(u, v) for u, v in zip(h["u10"], h["v10"], strict=True)),
        "Wind speed at 10 m, daily mean of hourly speeds",
    ),
    "solar_radiation": (
        "MJ m-2",
        "sum",
        lambda h: sum(max(0.0, x) for x in h["ssrd"]) / 1e6,
        "Surface solar radiation (downward) total",
    ),
    "soil_moisture": (
        "m3 m-3",
        "mean",
        lambda h: fmean(h["swvl1"]),
        "Volumetric soil water, 0-7 cm, daily mean",
    ),
    "vpd": ("kPa", "mean", _vpd_kpa, "Vapour pressure deficit, daily mean of hourly values"),
}


# climate-extreme day counts, derived at read time from cached daily values (no extra downloads)
# variable -> (daily variable, threshold test, description); counts sum per period
DAY_COUNTS: dict[str, tuple[str, Callable[[float], bool], str]] = {
    "hot_days": ("t2m_max", lambda v: v >= 30, "Hot days: daily maximum of 30 °C or more"),
    "summer_days": ("t2m_max", lambda v: v >= 25, "Summer days: daily maximum of 25 °C or more"),
    "tropical_nights": ("t2m_min", lambda v: v >= 20, "Tropical nights: daily minimum of 20 °C or more"),
    "frost_days": ("t2m_min", lambda v: v < 0, "Frost days: daily minimum below 0 °C"),
    "heavy_rain_days": ("precipitation", lambda v: v >= 20, "Heavy-rain days: 20 mm or more in a day"),
    "dry_days": ("precipitation", lambda v: v < 1, "Dry days: less than 1 mm in a day"),
}


def _lock(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks[key]


def _post_period(how: How) -> str:
    return "sum of daily totals" if how == "sum" else "mean of daily values"


class Era5LandAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="era5_land",
        name="ERA5-Land hourly time series",
        variables=[
            VariableInfo(name=n, units=u, topic="weather", description=d, aggregation=how)
            for n, (u, how, _, d) in DERIVED.items()
        ]
        + [
            VariableInfo(name=n, units="days", topic="weather", description=d, aggregation="sum")
            for n, (_, _, d) in DAY_COUNTS.items()
        ],
        access_pattern="timeseries",
        resolution="0.1 deg (~9 km)",
        coverage=(date(1950, 1, 1), None),
        latency="P6D",
        licence="CC-BY-4.0",
        attribution="Muñoz Sabater, J. (2019): ERA5-Land hourly data from 1950 to present. "
        "Copernicus Climate Change Service (C3S) Climate Data Store (CDS)",
        commercial_safe=True,
        auth_env=[KEY_VAR],
    )

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return ("ok", "") if env(KEY_VAR) else ("unconfigured", f"Set {KEY_VAR} (CDS personal access token).")

    def _cell(self, area: Area) -> tuple[float, float]:
        c = geometry_of(area).centroid
        return round(c.y, 1), round(c.x, 1)  # the nearest 0.1 deg grid point

    def _years(self, periods: list[Period], today: date) -> list[int]:
        last_day = min(periods[-1].end - timedelta(days=1), today - timedelta(days=LATENCY_DAYS))
        return list(range(max(periods[0].start.year, 1950), last_day.year + 1))

    def _key(self, cell: tuple[float, float], year: int) -> str:
        return f"{DATASET}|{cell[0]:.1f}|{cell[1]:.1f}|{year}"

    def _job(self, inputs: dict[str, Any]) -> bytes:
        key = env(KEY_VAR)
        if not key:
            raise GeomcpError("source_unconfigured", "CDS is not configured.", f"Set {KEY_VAR}; run `geomcp setup`.")
        headers = {"PRIVATE-TOKEN": key}
        with _slots, http.client(120) as c:
            submit = f"{CDS_URL}/processes/{DATASET}/execution"
            r = http.with_retries(
                lambda: http.raise_transient(c.post(submit, json={"inputs": inputs}, headers=headers))
            )
            if r.status_code in (401, 403):
                raise GeomcpError(
                    "source_forbidden",
                    f"CDS refused the request ({r.status_code}): {r.text[:200]}",
                    hint=f"Check {KEY_VAR} and accept the dataset licence at {LICENCE_PAGE}.",
                )
            job = http.json_of(r)["jobID"]
            delay, deadline = 1.0, time.monotonic() + 900
            while True:
                status = http.with_retries(lambda: http.json_of(c.get(f"{CDS_URL}/jobs/{job}", headers=headers)))
                if status["status"] == "successful":
                    break
                if status["status"] in ("failed", "dismissed") or time.monotonic() > deadline:
                    raise GeomcpError("source_error", f"CDS job {job} ended as {status['status']}.")
                time.sleep(delay)
                delay = min(delay * 1.5, 10.0)
            results = http.with_retries(lambda: http.json_of(c.get(f"{CDS_URL}/jobs/{job}/results", headers=headers)))
            href = results["asset"]["value"]["href"]
            return http.with_retries(lambda: http.check(c.get(href)).content)

    def _fetch_year(self, cell: tuple[float, float], year: int, today: date) -> dict[str, list[list[Any]]]:
        end = min(date(year, 12, 31), today - timedelta(days=LATENCY_DAYS))
        raw = self._job(
            {
                "variable": list(CDS_VARIABLES),
                "location": {"latitude": cell[0], "longitude": cell[1]},
                "date": [f"{year}-01-01/{end}"],
                "data_format": "csv",
            }
        )
        hourly: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))  # day -> var -> values
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for name in z.namelist():
                reader = csv.reader(io.TextIOWrapper(z.open(name), encoding="utf-8"))
                header = next(reader)  # e.g. valid_time,d2m,t2m,latitude,longitude: one file can hold several
                columns = [(i, c) for i, c in enumerate(header) if c in CDS_VARIABLES.values()]
                for row in reader:
                    for i, column in columns:
                        if row[i]:
                            hourly[row[0][:10]][column].append(float(row[i]))
        daily: dict[str, list[list[Any]]] = {v: [] for v in DERIVED}
        for day, h in sorted(hourly.items()):
            if any(len(h.get(c, [])) < 24 for c in CDS_VARIABLES.values()):
                continue  # incomplete day at the end of the record
            for variable, (_, _, reduce, _) in DERIVED.items():
                daily[variable].append([day, round(reduce(h), 5)])
        if not any(daily.values()):
            raise GeomcpError("source_error", f"CDS returned no complete days for {year} at {cell}.")
        return daily

    def _year(self, cell: tuple[float, float], year: int, today: date) -> tuple[dict[str, list[list[Any]]], Cost]:
        key = self._key(cell, year)
        with _lock(key):
            if (hit := cache.get(self.info.id, key)) is not None:
                return hit, Cost(cache_hits=1)
            daily = self._fetch_year(cell, year, today)
            cache.put(self.info.id, key, daily, cache.ttl_for_chunk(date(year + 1, 1, 1), today))
            return daily, Cost(cache_misses=1)

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        today = datetime.now(UTC).date()
        cell = self._cell(area)
        if variable in DAY_COUNTS:
            base, test, _ = DAY_COUNTS[variable]
            units, how = "days", "sum"
        else:
            base, test = variable, None
            units, how, _, _ = DERIVED[variable]
        obs: list[Observation] = []
        cost = Cost()
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:

            def one(year: int) -> tuple[dict[str, list[list[Any]]], Cost]:
                return self._year(cell, year, today)

            for daily, c in pool.map(one, self._years(periods, today)):
                obs.extend(
                    Observation(day=date.fromisoformat(d), mean=v if test is None else float(test(v)))
                    for d, v in daily[base]
                )
                cost.add(c)
        values = to_periods(
            obs, periods, how, today, preliminary_from=today - timedelta(days=PRELIMINARY_DAYS), coverage="days"
        )
        notice = (
            "; the area is larger than one grid cell, so this is the cell at its centroid" if area.area_km2 > 80 else ""
        )
        return SeriesData(
            variable=variable,
            units=units,
            values=values,
            cost=cost,
            provenance=Provenance(
                source=self.info.name,
                backend="cds-timeseries",
                collection=DATASET,
                method=f"grid cell {cell[0]:.1f}N {cell[1]:.1f}E nearest to the area centroid; hourly values reduced "
                f"to daily, then {_post_period(how) if test is None else 'days meeting the threshold counted'} "
                f"per period{notice}",
                licence=self.info.licence,
                attribution=self.info.attribution,
                commercial_safe=True,
                retrieved_at=datetime.now(UTC),
            ),
        )

    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        today = datetime.now(UTC).date()
        cell = self._cell(area)
        misses = sum(cache.get(self.info.id, self._key(cell, y)) is None for y in self._years(periods, today))
        return math.ceil(misses / MAX_PARALLEL) * SECONDS_PER_YEAR_CHUNK
