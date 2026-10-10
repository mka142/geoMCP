"""Core entry points shared by the MCP server and direct Python use."""

import difflib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from geomcp_core import registry
from geomcp_core.adapters import sentinel_hub
from geomcp_core.areas import area_from_id, geometry_of
from geomcp_core.catalog import CATEGORIES, ENTRIES, PLANNED, CatalogEntry
from geomcp_core.models import (
    Area,
    Cadence,
    Cost,
    Fact,
    Gap,
    GeomcpError,
    Period,
    PeriodSpec,
    Provenance,
    RowsTruncated,
    SeriesData,
    SeriesSummary,
    Snapshot,
    SourceStatus,
    TimeseriesResult,
)
from geomcp_core.periods import default_cadence, period_grid
from geomcp_core.summarise import pick_indices, summarise

log = logging.getLogger(__name__)
MAX_AREA_KM2 = 1000.0
MAX_COMPARE_AREAS = 10
ROW_COLUMNS = ["period", "variable", "mean", "p10", "p90", "valid_fraction", "n_obs", "status"]
RECENT_WINDOW_DAYS = {"weather": 30, "default": 60}


class SummaryResult(BaseModel):
    area: Area
    facts: list[Fact]
    gaps: list[Gap]
    notices: list[str]
    cost: Cost


class Ranked(BaseModel):
    rank: int
    area_id: str
    name: str
    area_km2: float
    value: float | None
    valid_fraction: float
    status: str


class CompareResult(BaseModel):
    variable: str
    units: str
    start: date
    end: date
    aggregation: str
    ranking: list[Ranked]
    provenance: Provenance
    notices: list[str]
    cost: Cost


def list_data(topic: str | None = None) -> list[SourceStatus]:
    out: list[SourceStatus] = []
    for adapter in registry.adapters():
        info = adapter.info
        if topic and not any(v.topic == topic for v in info.variables):
            continue
        health, hint = adapter.health()
        if topic:
            info = info.model_copy(update={"variables": [v for v in info.variables if v.topic == topic]})
        out.append(SourceStatus(source=info, health=health, hint=hint))
    return out


def _area(area_id: str, limit_km2: float = MAX_AREA_KM2) -> Area:
    area = area_from_id(area_id)
    if area.area_km2 > limit_km2:
        raise GeomcpError(
            "area_too_large",
            f"{area.name} is {area.area_km2:,.0f} km²; the limit is {limit_km2:,.0f} km².",
            hint="Use a smaller area (for example a gmina instead of a powiat) or split it.",
        )
    return area


def _pick_adapter(variable: str, commercial_safe_only: bool) -> tuple[registry.Adapter | None, str]:
    candidates = registry.adapters_for(variable)
    if not candidates:
        known = {v.name: v.description for a in registry.adapters() for v in a.info.variables}
        close = (
            difflib.get_close_matches(variable, list(known), n=3, cutoff=0.5)
            or [name for name, text in known.items() if variable.lower() in text.lower()][:3]
        )
        hint = f"Did you mean {', '.join(close)}? " if close else ""
        raise GeomcpError(
            "unknown_variable", f"No source provides {variable!r}.", hint=hint + "Call list_data to see variables."
        )
    if commercial_safe_only:
        candidates = [a for a in candidates if a.info.commercial_safe]
        if not candidates:
            return None, f"{variable}: only restricted sources provide it, and commercial_safe_only is set."
    hints: list[str] = []
    for adapter in candidates:
        health, hint = adapter.health()
        if health != "unconfigured":
            return adapter, ""
        hints.append(f"{adapter.info.id}: {hint}")
    return None, f"{variable}: no configured source ({'; '.join(hints)})."


def _aggregation(variable: str) -> str:
    found = registry.variable_info(variable)
    return found[1].aggregation if found else "mean"


def _is_static(variable: str) -> bool:
    found = registry.variable_info(variable)
    return found is not None and found[1].static


def _plan(variables: list[str], commercial_safe_only: bool) -> tuple[dict[str, registry.Adapter], list[Gap], list[str]]:
    gaps: list[Gap] = []
    notices: list[str] = []
    jobs: dict[str, registry.Adapter] = {}
    for variable in dict.fromkeys(variables):
        adapter, problem = _pick_adapter(variable, commercial_safe_only)
        if adapter is None:
            gaps.append(Gap(variable=variable, period=None, reason="source_unavailable"))
            notices.append(problem)
        elif _is_static(variable):
            gaps.append(Gap(variable=variable, period=None, reason="static_variable"))
            notices.append(f"{variable} does not change over time; use get_summary for it.")
        else:
            jobs[variable] = adapter
    return jobs, gaps, notices


def _run_parallel(
    jobs: dict[str, registry.Adapter], area: Area, periods: list[Period]
) -> tuple[dict[str, SeriesData], list[Gap], list[str]]:
    results: dict[str, SeriesData] = {}
    gaps: list[Gap] = []
    notices: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {v: pool.submit(a.stats, area, v, periods) for v, a in jobs.items()}
        for variable, fut in futures.items():
            try:
                results[variable] = fut.result()
            except GeomcpError as e:
                gaps.append(Gap(variable=variable, period=None, reason=e.code))
                notices.append(f"{variable}: {e.message}" + (f" {e.hint}" if e.hint else ""))
            except Exception as e:  # an adapter bug or provider surprise must not sink the whole answer
                log.exception("adapter failed for %s", variable)
                gaps.append(Gap(variable=variable, period=None, reason="source_error"))
                notices.append(f"{variable}: the source failed unexpectedly ({type(e).__name__}).")
    return results, gaps, notices


def _restricted_notice(variable: str, data: SeriesData) -> str | None:
    p = data.provenance
    if p.commercial_safe:
        return None
    return f"{variable} uses {p.source} ({p.licence}), which is not cleared for commercial use (commercial_safe=false)."


def estimate_timeseries_s(
    area_id: str, variables: list[str], start: date, end: date, cadence: Cadence | None = None
) -> float:
    """Expected seconds for get_timeseries, from cache state only; adapters run in parallel."""
    area = _area(area_id)
    periods = period_grid(start, end, cadence or default_cadence(start, end))
    jobs, _, _ = _plan(variables, False)
    return max(
        (a.estimate_s(area, periods) for a in jobs.values() if isinstance(a, registry.Estimating)),
        default=0.0,
    )


def estimate_compare_s(area_ids: list[str], variable: str, start: date, end: date) -> float:
    adapter = registry.adapters_for(variable)[:1]
    if not adapter or not isinstance(adapter[0], registry.Estimating):
        return 0.0
    period = [Period(start=start, end=end)]
    return max(adapter[0].estimate_s(_area(a), period) for a in area_ids)


def get_timeseries(
    area_id: str,
    variables: list[str],
    start: date,
    end: date,
    cadence: Cadence | None = None,
    max_rows: int = 60,
    commercial_safe_only: bool = False,
) -> TimeseriesResult:
    t0 = time.monotonic()
    if not variables:
        raise GeomcpError("invalid_request", "Pass at least one variable.", hint="Call list_data to see variables.")
    if max_rows < 2:
        raise GeomcpError("invalid_request", "max_rows must be at least 2.")
    area = _area(area_id)
    cadence = cadence or default_cadence(start, end)
    periods = period_grid(start, end, cadence)
    jobs, gaps, notices = _plan(variables, commercial_safe_only)
    results, run_gaps, run_notices = _run_parallel(jobs, area, periods)
    gaps += run_gaps
    notices += run_notices

    series: list[SeriesSummary] = []
    rows: list[list[Any]] = []
    cost = Cost()
    for variable, data in results.items():
        cost.add(data.cost)
        series.append(
            SeriesSummary(
                variable=variable,
                units=data.units,
                summary=summarise(data.values, cadence),
                provenance=data.provenance,
            )
        )
        for v in data.values:
            rows.append([v.period, variable, v.mean, v.p10, v.p90, v.valid_fraction, v.n_obs, v.status])
            if v.status == "missing":
                gaps.append(Gap(variable=variable, period=v.period, reason="no_valid_observation"))
        if notice := _restricted_notice(variable, data):
            notices.append(notice)
        if n_pre := sum(v.status == "preliminary" for v in data.values):
            notices.append(f"{variable}: {n_pre} recent value(s) are preliminary or incomplete and may change.")

    # Sample whole periods so every shown period has all its variables.
    rows.sort(key=lambda r: (r[0], variables.index(r[1])))
    row_periods = sorted({r[0] for r in rows})
    keep_periods = {row_periods[i] for i in pick_indices(len(row_periods), max(1, max_rows // max(1, len(results))))}
    keep = [i for i, r in enumerate(rows) if r[0] in keep_periods]
    cost.duration_s = round(time.monotonic() - t0, 2)
    return TimeseriesResult(
        area_id=area.area_id,
        period=PeriodSpec(start=start, end=end, cadence=cadence, n_periods=len(periods)),
        series=series,
        rows_columns=ROW_COLUMNS,
        rows=[rows[i] for i in keep],
        full_rows=rows,
        rows_truncated=RowsTruncated(shown=len(keep), total=len(rows)) if len(keep) < len(rows) else None,
        gaps=gaps,
        notices=notices,
        cost=cost,
    )


def _recent_period(adapter: registry.Adapter, variable: str, window_days: int | None, today: date) -> Period:
    """The trailing window a summary reports for a dynamic variable (weather lags about a week)."""
    topic = next(v.topic for v in adapter.info.variables if v.name == variable)
    days = window_days or RECENT_WINDOW_DAYS.get(topic, RECENT_WINDOW_DAYS["default"])
    latency = timedelta(days=7) if topic == "weather" else timedelta(0)
    return Period(start=today - latency - timedelta(days=days), end=today - latency)


def estimate_summary_s(area_id: str, variables: list[str], window_days: int | None = None) -> float:
    area = _area(area_id)
    today = datetime.now(UTC).date()
    estimates = [0.0]
    for variable in dict.fromkeys(variables):
        hits = registry.adapters_for(variable)
        if not hits or not isinstance(hits[0], registry.Estimating):
            continue
        periods = [] if _is_static(variable) else [_recent_period(hits[0], variable, window_days, today)]
        estimates.append(hits[0].estimate_s(area, periods))
    return max(estimates)


def get_summary(area_id: str, variables: list[str], window_days: int | None = None) -> SummaryResult:
    """Static facts (terrain) plus recent values of dynamic variables over a trailing window."""
    t0 = time.monotonic()
    area = _area(area_id)
    today = datetime.now(UTC).date()
    facts: list[Fact] = []
    gaps: list[Gap] = []
    notices: list[str] = []
    cost = Cost()
    static: dict[str, list[str]] = {}
    static_adapters: dict[str, registry.Adapter] = {}
    dynamic: dict[str, registry.Adapter] = {}
    for variable in dict.fromkeys(variables):
        adapter, problem = _pick_adapter(variable, False)
        if adapter is None:
            gaps.append(Gap(variable=variable, period=None, reason="source_unavailable"))
            notices.append(problem)
        elif _is_static(variable):
            static.setdefault(adapter.info.id, []).append(variable)
            static_adapters[adapter.info.id] = adapter
        else:
            dynamic[variable] = adapter
    for source_id, names in static.items():
        adapter = static_adapters[source_id]
        if not isinstance(adapter, registry.StaticFacts):
            continue
        try:
            found, c = adapter.facts(area, names)
            facts += found
            cost.add(c)
        except GeomcpError as e:
            gaps += [Gap(variable=n, period=None, reason=e.code) for n in names]
            notices.append(f"{', '.join(names)}: {e.message}" + (f" {e.hint}" if e.hint else ""))
        except Exception as e:  # see _run_parallel
            log.exception("adapter failed for %s", names)
            gaps += [Gap(variable=n, period=None, reason="source_error") for n in names]
            notices.append(f"{', '.join(names)}: the source failed unexpectedly ({type(e).__name__}).")
    for variable, adapter in dynamic.items():
        period = _recent_period(adapter, variable, window_days, today)
        try:
            data = adapter.stats(area, variable, [period])
        except GeomcpError as e:
            gaps.append(Gap(variable=variable, period=None, reason=e.code))
            notices.append(f"{variable}: {e.message}" + (f" {e.hint}" if e.hint else ""))
            continue
        except Exception as e:  # see _run_parallel
            log.exception("adapter failed for %s", variable)
            gaps.append(Gap(variable=variable, period=None, reason="source_error"))
            notices.append(f"{variable}: the source failed unexpectedly ({type(e).__name__}).")
            continue
        cost.add(data.cost)
        v = data.values[0]
        if v.mean is None:
            gaps.append(Gap(variable=variable, period=period.start, reason="no_valid_observation"))
            continue
        is_sum = _aggregation(variable) == "sum"
        facts.append(
            Fact(
                variable=variable,
                units=data.units,
                value={"total": v.mean} if is_sum else {"mean": v.mean, "p10": v.p10, "p90": v.p90},
                period=f"{period.start}/{period.end}",
                status=v.status,
                provenance=data.provenance,
            )
        )
        if notice := _restricted_notice(variable, data):
            notices.append(notice)
    cost.duration_s = round(time.monotonic() - t0, 2)
    return SummaryResult(area=area, facts=facts, gaps=gaps, notices=notices, cost=cost)


def compare_areas(area_ids: list[str], variable: str, start: date, end: date) -> CompareResult:
    """One value per area over [start, end): the mean (or total, for sums), ranked high to low."""
    t0 = time.monotonic()
    if not 2 <= len(set(area_ids)) <= MAX_COMPARE_AREAS:
        raise GeomcpError("invalid_request", f"Compare between 2 and {MAX_COMPARE_AREAS} distinct areas.")
    if end <= start:
        raise GeomcpError("invalid_range", f"end ({end}) must be after start ({start}).")
    adapter, problem = _pick_adapter(variable, False)
    if adapter is None:
        raise GeomcpError("source_unavailable", problem)
    if _is_static(variable):
        raise GeomcpError("static_variable", f"{variable} does not change over time.", "Use get_summary per area.")
    areas = [_area(a) for a in dict.fromkeys(area_ids)]
    period = [Period(start=start, end=end)]
    with ThreadPoolExecutor(max_workers=len(areas)) as pool:

        def one(area: Area) -> SeriesData:
            return adapter.stats(area, variable, period)

        series = list(pool.map(one, areas))
    cost = Cost()
    for data in series:
        cost.add(data.cost)
    rows = sorted(
        zip(areas, series, strict=True),
        key=lambda r: (r[1].values[0].mean is None, -(r[1].values[0].mean or 0)),
    )
    ranking = [
        Ranked(
            rank=i + 1,
            area_id=a.area_id,
            name=a.name,
            area_km2=a.area_km2,
            value=d.values[0].mean,
            valid_fraction=d.values[0].valid_fraction,
            status=d.values[0].status,
        )
        for i, (a, d) in enumerate(rows)
    ]
    first = series[0]
    notice = _restricted_notice(variable, first)
    cost.duration_s = round(time.monotonic() - t0, 2)
    return CompareResult(
        variable=variable,
        units=first.units,
        start=start,
        end=end,
        aggregation=f"{'total' if _aggregation(variable) == 'sum' else 'mean'} over the whole period",
        ranking=ranking,
        provenance=first.provenance,
        notices=[notice] if notice else [],
        cost=cost,
    )


def get_snapshot(area_id: str, layer: str, when: date | None = None, size_px: int = 512) -> Snapshot:
    if layer == "orthophoto":
        from geomcp_core import orthophoto

        return orthophoto.snapshot(_area(area_id), size_px)
    return sentinel_hub.snapshot(_area(area_id), layer, when, size_px)


class CatalogItem(BaseModel):
    entry: CatalogEntry
    name: str
    variables: list[str]
    licence: str
    commercial_safe: bool
    health: str
    available_here: bool | None = Field(description="With an area: whether this source covers it")


class DescribeResult(BaseModel):
    categories: dict[str, list[CatalogItem]]
    planned: list[str]
    area: str | None


# datasets served by the statistics tools rather than by an adapter: name, licence, commercial-safe, variables, health
STATISTICS_DATASETS: dict[str, tuple[str, str, bool, list[str], str]] = {
    "gus_statistics": ("GUS Local Data Bank (all indicators)", "CC-BY-4.0", True, ["any BDL indicator"], "ok"),
    "smup": ("GUS public-services monitoring (SMUP)", "CC-BY-4.0", True, ["any SMUP indicator"], "ok"),
    "isok_flood": ("ISOK flood hazard maps (Wody Polskie)", "public data", True, ["flood zone shares"], "ok"),
    "gdos_protected": ("GDOŚ nature protection register", "public data", True, ["protected area shares"], "ok"),
    "pig_geohazards": ("PIG-PIB geohazards and EGMS ground motion", "public data", True, ["hazard shares"], "ok"),
    "iung_drought": ("IUNG agricultural drought monitoring", "public information", True, ["crops at risk"], "ok"),
    "gios_noise": ("GIOŚ strategic noise maps 2017", "CC-BY-4.0", True, ["noise shares"], "ok"),
    "imgw_warnings": ("IMGW-PIB warnings", "IMGW-PIB terms", True, ["active warnings"], "ok"),
    "gugik_orthophoto": ("GUGiK orthophoto", "open data (PZGiK)", True, ["orthophoto layer"], "ok"),
    "rcn_prices": ("Register of real-estate prices (RCN)", "open data (PZGiK)", True, ["price medians"], "ok"),
    "kimpzp_zoning": ("Local zoning plans (KIMPZP)", "public data", True, ["plan designations"], "ok"),
    "ceidg": ("CEIDG sole traders register", "public register", True, ["sole trader counts"], "ok"),
    "krs_business": ("KRS court register and VAT whitelist", "public registers", True, ["company facts"], "ok"),
    "gddkia_traffic": ("GDDKiA General Traffic Survey 2025", "CC0 1.0", True, ["vehicles per day"], "ok"),
}

# Poland's bounding box; good enough to tell which Poland-only sources apply to an area
_POLAND = (14.07, 49.0, 24.15, 54.84)


def describe_data(topic: str | None = None, area_id: str | None = None) -> DescribeResult:
    """The data catalogue, grouped by category, optionally filtered by a topic word and checked for an area."""
    in_poland: bool | None = None
    area_name = None
    if area_id:
        area = area_from_id(area_id)
        area_name = area.name
        c = geometry_of(area).centroid
        in_poland = _POLAND[0] <= c.x <= _POLAND[2] and _POLAND[1] <= c.y <= _POLAND[3]
    sources = {a.info.id: a for a in registry.adapters()}
    items: list[CatalogItem] = []
    for entry in ENTRIES.values():
        adapter = sources.get(entry.id)
        if adapter is None and entry.id not in STATISTICS_DATASETS:
            continue
        if adapter is not None:
            info = adapter.info
            name, licence, safe = info.name, info.licence, info.commercial_safe
            variables = [f"{v.name} [{v.units}]" for v in info.variables]
            health = adapter.health()[0]
        else:
            name, licence, safe, variables, health = STATISTICS_DATASETS[entry.id]
        haystack = " ".join([entry.category, entry.measures, name, *variables]).lower()
        if topic and topic.lower() not in haystack:
            continue
        here = None if in_poland is None else (in_poland or entry.region != "Poland")
        items.append(
            CatalogItem(
                entry=entry,
                name=name,
                variables=variables,
                licence=licence,
                commercial_safe=safe,
                health=health,
                available_here=here,
            )
        )
    grouped = {c: [i for i in items if i.entry.category == c] for c in CATEGORIES}
    return DescribeResult(categories={c: v for c, v in grouped.items() if v}, planned=PLANNED, area=area_name)
