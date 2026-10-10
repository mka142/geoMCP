"""The MCP server: thin, hand-written tools over geomcp_core."""

import json
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, date, datetime
from importlib.metadata import version
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field, TypeAdapter
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from geomcp_core import business, ceidg, constraints, prices, service, statistics, traffic, zoning
from geomcp_core.adapters import cdse
from geomcp_core.adapters.sentinel_hub import LAYERS
from geomcp_core.areas import area_from_id
from geomcp_core.areas import resolve_area as core_resolve_area
from geomcp_core.files import write_table
from geomcp_core.models import Area, Cadence, Cost, GeomcpError, Provenance, SourceStatus, TimeseriesResult
from geomcp_server import metrics, usage
from geomcp_server.db import migrate
from geomcp_server.downloads import Downloads
from geomcp_server.jobs import JobRunner, Outcome
from geomcp_server.settings import Settings, get_settings

log = structlog.get_logger()
_client: ContextVar[str] = ContextVar("geomcp_client", default="local")
JOB_LONG_POLL_S = 20.0

INSTRUCTIONS = """\
geomcp answers questions about places with compact, sourced numbers: satellites, weather and climate, official
Polish registers and statistics, and Europe-wide datasets. Every value carries units, status, licence and source.
Workflow: resolve_area turns a place name, Polish gmina, address, parcel id or 'lat,lon' into an area_id.
Then: get_summary (terrain, land cover, buildings, population, solar, climate projection, air quality now and
history), get_timeseries (trends over years), compare_areas (rank places), get_snapshot (satellite image or
aerial photo), get_constraints (flood, nature protection, geology, noise, drought, warnings), get_prices
(real-estate transactions), get_zoning (local plan), get_traffic (national roads), find_statistics and
get_statistics (official GUS indicators), lookup_business and find_businesses (companies and sole traders).
describe_data explains every dataset in plain language. When someone asks how to use geomcp, what it can do,
or for an onboarding, follow the `onboarding` prompt: call describe_data and give a short guide in their language.
Report gaps and notices, flag non-commercial sources, and never invent values for gaps.
If a tool returns a job_id, call get_job with it until status is succeeded or failed.
"""


class ResolveAreaResult(BaseModel):
    selected: str | None = Field(description="area_id to use, or null when the query is ambiguous")
    candidates: list[Area]


class Quota(BaseModel):
    month: str
    units_used: float
    units_limit: float
    requests_used: int
    requests_limit: int


class ListDataResult(BaseModel):
    sources: list[SourceStatus]
    sentinel_hub_quota: Quota = Field(description="This server's own usage; other uses of the account are not seen")


class JobHandle(BaseModel):
    job_id: str
    status: Literal["running"]
    eta_s: float
    hint: str


class JobStatus(BaseModel):
    job_id: str
    tool: str
    status: Literal["running", "succeeded", "failed"]
    eta_s: float
    result: dict[str, Any] | None = Field(description="The tool's structured result once succeeded")
    error: dict[str, str] | None


class FindStatisticsResult(BaseModel):
    query: str
    indicators: list[statistics.Indicator]


class SnapshotMeta(BaseModel):
    layer: str
    scene_date: date | None
    requested_date: date | None
    scene_cloud_cover: float | None
    size_px: tuple[int, int]
    pixel_size_m: float
    legend: dict[str, Any]
    provenance: Provenance


def _schema(*models: type[BaseModel]) -> dict[str, Any]:
    """Output schema for a tool; with two models, their union (the root must still be an object)."""
    if len(models) == 1:
        return models[0].model_json_schema()
    union: Any = models[0] | models[1]
    return {"type": "object", **TypeAdapter(union).json_schema()}


def _fmt(x: float) -> str:
    return f"{x:.3g}" if abs(x) < 1000 else f"{x:,.0f}"


def _units(u: str) -> str:
    return "" if u == "1" else f" {u}"


def _label(d: date, cadence: str) -> str:
    """A human period label for text: 2025, 2025-07, 2025-Q3 or 2025-W28 (structured data keeps dates)."""
    if cadence == "P1Y":
        return f"{d.year}"
    if cadence == "P3M":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if cadence == "P1W":
        year, week, _ = d.isocalendar()
        return f"{year}-W{week:02d}"
    return f"{d:%Y-%m}"


def _size(km2: float) -> str:
    if km2 >= 1:
        return f"{km2:,.1f} km²"
    return f"{km2 * 100:,.1f} ha" if km2 >= 0.01 else f"{km2 * 1e6:,.0f} m²"


def _timeseries_text(r: TimeseriesResult) -> str:
    lines = [f"{r.period.n_periods} periods ({r.period.cadence}) from {r.period.start} to {r.period.end}."]
    for s in r.series:
        sm = s.summary
        if sm.latest is None:
            lines.append(f"{s.variable}: no valid values.")
            continue
        u = _units(s.units)
        trend = "n/a" if sm.trend_per_year is None else f"{sm.trend_per_year:+.3g}{u}/yr"
        p = "" if sm.trend_p_value is None else f" (p={sm.trend_p_value:.2g})"
        lines.append(
            f"{s.variable}: seasonal trend {trend}{p}; "
            f"latest {_fmt(sm.latest.value)}{u} ({_label(sm.latest.period, r.period.cadence)}); "
            f"{sm.gaps} gap(s); source {s.provenance.source}. Method: {s.provenance.method}."
        )
    lines.extend(f"Notice: {n}" for n in r.notices)
    if r.rows_truncated:
        lines.append(f"Showing {r.rows_truncated.shown} of {r.rows_truncated.total} rows.")
    return "\n".join(lines)


def _fact_body(value: float | dict[str, float | None], units: str) -> str:
    if isinstance(value, dict):
        if units == "fraction":  # land-cover shares read best as percentages
            return ", ".join(f"{k.replace('_', ' ')} {v:.0%}" for k, v in value.items() if v is not None and v >= 0.005)
        parts = ", ".join(
            f"{k.removeprefix('in_').replace('_', ' ')} {_fmt(v)}" for k, v in value.items() if v is not None
        )
    else:
        parts = _fmt(value)
    return parts + ("" if units in ("count", "fraction") else _units(units))


def _summary_text(r: service.SummaryResult) -> str:
    lines = [f"{r.area.name} ({_size(r.area.area_km2)}):"]
    for f in r.facts:
        when = "" if not f.period else (f" over {f.period}" if "/" in f.period else f" as of {f.period}")
        lines.append(f"{f.variable}{when}: {_fact_body(f.value, f.units)} ({f.status}; {f.provenance.source}).")
    lines.extend(f"Gap: {g.variable} ({g.reason})." for g in r.gaps)
    lines.extend(f"Notice: {n}" for n in r.notices)
    return "\n".join(lines)


def _compare_text(r: service.CompareResult) -> str:
    u = _units(r.units)
    ranked = " > ".join(
        f"{x.name} {_fmt(x.value)}{u}" if x.value is not None else f"{x.name} (no data)" for x in r.ranking
    )
    notices = "".join(f"\nNotice: {n}" for n in r.notices)
    return f"{r.variable}, {r.aggregation}, {r.start} to {r.end}: {ranked}.{notices}"


def _ok(structured: BaseModel, text: str) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)], structured_content=structured.model_dump(mode="json")
    )


def _error(e: GeomcpError) -> ToolResult:
    text = f"{e.code}: {e.message}" + (f" Hint: {e.hint}" if e.hint else "")
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content={"error": e.as_dict()},
        is_error=True,
    )


def build_server(
    auth: AuthProvider | None = None, settings: Settings | None = None, public_url: str | None = None
) -> FastMCP:
    """`public_url` set (HTTP mode): download links are signed URLs; unset (stdio): local file paths."""
    settings = settings or get_settings()
    db_url = settings.database_url
    migrate(db_url)
    runner = JobRunner(db_url)
    downloads = Downloads(settings.data_dir, public_url)
    mcp = FastMCP("geomcp", instructions=INSTRUCTIONS, version=version("geomcp-server"), auth=auth)

    def guard(tool: str, fn: Callable[[], ToolResult]) -> ToolResult:
        """Expected failures become structured errors (isError) instead of protocol errors; every call is logged."""
        token = get_access_token()
        client = token.client_id if token else "local"
        _client.set(client)
        t0 = time.monotonic()
        ok, code = True, None
        try:
            return fn()
        except GeomcpError as e:
            ok, code = False, e.code
            return _error(e)
        finally:
            duration = round(time.monotonic() - t0, 2)
            log.info("tool", tool=tool, client=client, ok=ok, code=code, duration_s=duration)
            metrics.record(db_url, tool, client, ok, code, duration)

    def charge(cost: Cost) -> None:
        """Provider cost, for the monthly quota and per-person stats (jobs inherit the caller's context)."""
        usage.record(db_url, cost)
        metrics.spent(db_url, _client.get(), cost.sentinel_hub_units, cost.cache_hits, cost.cache_misses)

    def maybe_job(tool: str, eta_s: float, work: Callable[[], Outcome]) -> ToolResult:
        """Run now if the estimate fits the sync budget; otherwise start a job and return its handle."""
        if eta_s <= settings.sync_budget_s:
            structured, text = work()
            return ToolResult(content=[TextContent(type="text", text=text)], structured_content=structured)
        job_id = runner.submit(tool, eta_s, work)
        wait = round(eta_s)
        handle = JobHandle(
            job_id=job_id,
            status="running",
            eta_s=wait,
            hint=f"Fetching data that is not cached yet. Call get_job('{job_id}') in about {wait} s.",
        )
        return _ok(handle, f"This takes about {wait} s; call get_job('{job_id}') to fetch the result.")

    @mcp.tool(output_schema=_schema(ResolveAreaResult), annotations={"readOnlyHint": True})
    def resolve_area(
        query: Annotated[
            str,
            Field(
                description="A place name or address ('gmina Kąty Wrocławskie', 'Rynek, Wrocław'), "
                "a Polish parcel id ('026401_1.0001.2395'), or coordinates 'lat,lon' ('51.110,17.032')."
            ),
        ],
        buffer_m: Annotated[
            int,
            Field(
                ge=10,
                le=20_000,
                description=(
                    "For points and addresses: circle radius, or half the side of a square, in metres. "
                    "For a square of A km² use 500*sqrt(A), e.g. 1581 for 10 km²."
                ),
            ),
        ] = 1000,
        shape: Annotated[Literal["circle", "square"], Field(description="Buffer shape around a point.")] = "circle",
    ) -> ToolResult:
        """Turn a place into an area_id for the other tools. Ambiguous names return several candidates."""

        def run() -> ToolResult:
            areas, selected = core_resolve_area(query, buffer_m, shape)
            result = ResolveAreaResult(selected=selected, candidates=areas)
            if selected:
                a = areas[0]
                terc = f", TERC {a.admin['terc']}" if "terc" in a.admin else ""
                text = f"Resolved {query!r} to {a.name} ({a.kind}{terc}), {_size(a.area_km2)} (area_id {a.area_id})."
            else:
                options = "; ".join(f"{a.area_id}: {a.admin.get('display_name', a.name)}" for a in areas)
                text = f"{query!r} is ambiguous; ask the user which one: {options}"
            return _ok(result, text)

        return guard("resolve_area", run)

    @mcp.tool(output_schema=_schema(ListDataResult), annotations={"readOnlyHint": True})
    def list_data(
        topic: Annotated[
            str | None,
            Field(
                description="Optional topic: vegetation, water, built-up, radar, weather, air quality, terrain, "
                "land cover, infrastructure, population and economy."
            ),
        ] = None,
    ) -> ToolResult:
        """List data sources and variables with units, coverage, licence, commercial_safe flag and health."""

        def run() -> ToolResult:
            sources = service.list_data(topic)
            used = usage.this_month(db_url)
            quota = Quota(
                month=datetime.now(UTC).strftime("%Y-%m"),
                units_used=round(used.units, 1) if used else 0.0,
                units_limit=settings.sentinel_hub_monthly_units,
                requests_used=used.requests if used else 0,
                requests_limit=settings.sentinel_hub_monthly_requests,
            )
            if quota.units_used > 0.8 * quota.units_limit or quota.requests_used > 0.8 * quota.requests_limit:
                hint = "Over 80% of the monthly Sentinel Hub quota is used; cached answers still work."
                sources = [
                    s.model_copy(update={"health": "quota_low", "hint": hint})
                    if s.health == "ok" and cdse.ID_VAR in s.source.auth_env
                    else s
                    for s in sources
                ]
            n_vars = sum(len(s.source.variables) for s in sources)
            text = f"{len(sources)} source(s), {n_vars} variable(s)" + (f" for topic {topic!r}." if topic else ".")
            for s in sources:
                names = ", ".join(f"{v.name} [{v.units}]" for v in s.source.variables)
                flag = "" if s.source.commercial_safe else " RESTRICTED (not commercial-safe)"
                state = "" if s.health == "ok" else f" {s.health.upper()}: {s.hint}"
                text += f"\n- {s.source.name}{flag}{state}: {names}"
            text += f"\nSentinel Hub this month: {quota.units_used:,.0f} of {quota.units_limit:,.0f} processing units."
            return _ok(ListDataResult(sources=sources, sentinel_hub_quota=quota), text)

        return guard("list_data", run)

    @mcp.tool(output_schema=_schema(TimeseriesResult, JobHandle), annotations={"readOnlyHint": True})
    def get_timeseries(
        area_id: Annotated[str, Field(description="An area_id from resolve_area.")],
        variables: Annotated[list[str], Field(min_length=1, description="Variable names from list_data.")],
        start: Annotated[date, Field(description="First day, inclusive (UTC).")],
        end: Annotated[
            date,
            Field(description="Day after the last day (exclusive, UTC); for all of 2025 use 2026-01-01."),
        ],
        cadence: Annotated[
            Cadence | None,
            Field(description="P1W (ISO weeks), P1M, P3M or P1Y; default P3M above 2 years, else P1M."),
        ] = None,
        max_rows: Annotated[int, Field(ge=2, le=500, description="Rows to return; a sample if more exist.")] = 60,
        commercial_safe_only: Annotated[bool, Field(description="Exclude restricted sources.")] = False,
    ) -> ToolResult:
        """Per-period statistics for one area: a seasonal trend summary per variable plus a sample of rows."""

        def work() -> Outcome:
            r = service.get_timeseries(area_id, variables, start, end, cadence, max_rows, commercial_safe_only)
            charge(r.cost)
            if r.full_rows:
                files = write_table(r.rows_columns, r.full_rows, f"timeseries-{area_id}".lower().replace(".", "_"))
                r.downloads = {fmt: downloads.link(path) for fmt, path in files.items()}
            text = _timeseries_text(r) + (f"\nFull table: {r.downloads['csv']}" if r.downloads else "")
            return r.model_dump(mode="json"), text

        def run() -> ToolResult:
            eta = service.estimate_timeseries_s(area_id, variables, start, end, cadence)
            return maybe_job("get_timeseries", eta, work)

        return guard("get_timeseries", run)

    @mcp.tool(output_schema=_schema(service.SummaryResult, JobHandle), annotations={"readOnlyHint": True})
    def get_summary(
        area_id: Annotated[str, Field(description="An area_id from resolve_area.")],
        variables: Annotated[
            list[str],
            Field(
                min_length=1,
                description="Static facts (terrain, land cover, OSM counts, GUS statistics, current station air "
                "quality) or dynamic variables, which report the recent window (30 days for weather, 60 otherwise).",
            ),
        ],
        window_days: Annotated[
            int | None, Field(ge=1, le=366, description="Override the trailing window for dynamic variables.")
        ] = None,
    ) -> ToolResult:
        """One-shot facts about an area: terrain, land cover, infrastructure, population and economy, current
        air quality, and recent values of satellite and weather variables."""

        def work() -> Outcome:
            r = service.get_summary(area_id, variables, window_days)
            charge(r.cost)
            return r.model_dump(mode="json"), _summary_text(r)

        def run() -> ToolResult:
            return maybe_job("get_summary", service.estimate_summary_s(area_id, variables, window_days), work)

        return guard("get_summary", run)

    @mcp.tool(output_schema=_schema(service.CompareResult, JobHandle), annotations={"readOnlyHint": True})
    def compare_areas(
        area_ids: Annotated[list[str], Field(min_length=2, max_length=10, description="2-10 area_ids.")],
        variable: Annotated[str, Field(description="One variable from list_data.")],
        start: Annotated[date, Field(description="First day, inclusive (UTC).")],
        end: Annotated[date, Field(description="Day after the last day (exclusive, UTC).")],
    ) -> ToolResult:
        """Rank areas by one variable over a period (mean, or total for precipitation and radiation)."""

        def work() -> Outcome:
            r = service.compare_areas(area_ids, variable, start, end)
            charge(r.cost)
            return r.model_dump(mode="json"), _compare_text(r)

        def run() -> ToolResult:
            return maybe_job("compare_areas", service.estimate_compare_s(area_ids, variable, start, end), work)

        return guard("compare_areas", run)

    @mcp.tool(output_schema=_schema(SnapshotMeta), annotations={"readOnlyHint": True})
    def get_snapshot(
        area_id: Annotated[str, Field(description="An area_id from resolve_area.")],
        layer: Annotated[
            Literal["true_color", "false_color", "ndvi", "ndwi", "sar_vv", "elevation", "orthophoto"],
            Field(description="What to draw; orthophoto is the GUGiK aerial photo (Poland, up to 25 cm)."),
        ],
        date: Annotated[
            date | None,
            Field(description="Target date; the least cloudy scene within 15 days is used. Default: last month."),
        ] = None,
        size_px: Annotated[int, Field(ge=64, le=1024, description="Longest side in pixels (capped at native).")] = 512,
    ) -> ToolResult:
        """A north-up PNG map of the area (satellite image, index colour map, radar or elevation)."""

        def run() -> ToolResult:
            snap = service.get_snapshot(area_id, layer, date, size_px)
            charge(Cost(sentinel_hub_units=snap.units_spent, cache_misses=1))
            meta = SnapshotMeta(**snap.model_dump(exclude={"png", "units_spent"}))
            when = f"scene {snap.scene_date}" if snap.scene_date else snap.provenance.source
            cloud = f", tile cloud {snap.scene_cloud_cover:.0%}" if snap.scene_cloud_cover is not None else ""
            w, h = snap.size_px
            text = f"{layer} for {area_id}: {when}{cloud}, {w}x{h} px at {snap.pixel_size_m} m. Legend: {snap.legend}"
            return ToolResult(
                content=[
                    Image(data=snap.png, format="png").to_image_content(),
                    TextContent(type="text", text=text),
                ],
                structured_content=meta.model_dump(mode="json"),
            )

        return guard("get_snapshot", run)

    @mcp.tool(output_schema=_schema(service.DescribeResult), annotations={"readOnlyHint": True})
    def describe_data(
        topic: Annotated[
            str | None,
            Field(description="Optional word to filter by, e.g. 'air', 'climate', 'population', 'satellite'."),
        ] = None,
        area_id: Annotated[str | None, Field(description="Optional area_id: also says which sources cover it.")] = None,
    ) -> ToolResult:
        """What data geomcp offers: by category, what each dataset measures, resolution, history, update
        frequency, limits, cost, licence, example questions and caveats. Use it to plan which tools to call."""

        def run() -> ToolResult:
            r = service.describe_data(topic, area_id)
            lines = [f"Data available{f' for {r.area}' if r.area else ''}:"]
            for category, items in r.categories.items():
                lines.append(f"\n{category}:")
                for i in items:
                    e = i.entry
                    where = "" if i.available_here is not False else " (NOT available for this area)"
                    state = "" if i.health == "ok" else f" [{i.health}]"
                    lines.append(
                        f"- {i.name}{where}{state}: {e.measures}. {e.region}; {e.history}; "
                        f"updated {e.update_frequency}. "
                        f"Tools: {', '.join(e.tools)}. Example: {e.examples[0]}"
                    )
            lines.append("\nComing next: " + "; ".join(r.planned) + ".")
            return _ok(r, "\n".join(lines))

        return guard("describe_data", run)

    @mcp.tool(output_schema=_schema(FindStatisticsResult), annotations={"readOnlyHint": True})
    def find_statistics(
        query: Annotated[
            str,
            Field(
                description="Words to search official Polish statistics for. Use POLISH words (GUS names are "
                "Polish), e.g. 'ludność', 'wynagrodzenia', 'przedszkola', 'podmioty gospodarki', "
                "'pozwolenia na budowę'."
            ),
        ],
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum results per source.")] = 20,
    ) -> ToolResult:
        """Search thousands of official indicators (GUS Local Data Bank and public-services monitoring SMUP).
        Returns ids for get_statistics, with the lowest territorial level that has data."""

        def run() -> ToolResult:
            found = statistics.find_statistics(query, limit)
            text = f"{len(found)} indicator(s) for {query!r}:\n" + "\n".join(
                f"- {i.id} [{i.level or '?'}{', ' + i.unit if i.unit else ''}] {i.name}" for i in found
            )
            return _ok(FindStatisticsResult(query=query, indicators=found), text)

        return guard("find_statistics", run)

    @mcp.tool(output_schema=_schema(statistics.StatisticsResult), annotations={"readOnlyHint": True})
    def get_statistics(
        area_id: Annotated[str, Field(description="An area_id from resolve_area (in Poland).")],
        indicator_ids: Annotated[
            list[str], Field(min_length=1, max_length=20, description="Ids from find_statistics, e.g. bdl:72305.")
        ],
        years: Annotated[
            list[int] | None, Field(description="Years to return; default: the last six available.")
        ] = None,
    ) -> ToolResult:
        """Official statistics for the area's gmina, with its powiat, voivodeship and Poland for comparison."""

        def run() -> ToolResult:
            r = statistics.get_statistics(area_id, indicator_ids, years)
            lines = [f"{r.gmina} ({r.units.get('powiat', '')}, {r.units.get('voivodeship', '')}):"]
            for iv in r.indicators:
                unit = f" [{iv.indicator.unit}]" if iv.indicator.unit else ""
                lines.append(f"{iv.indicator.id} {iv.indicator.name}{unit}:")
                for level, series in iv.values.items():
                    lines.append(f"  {level}: " + ", ".join(f"{y} {_fmt(v)}" for y, v in series.items()))
            lines.extend(f"Notice: {n}" for n in r.notices)
            return _ok(r, "\n".join(lines))

        return guard("get_statistics", run)

    @mcp.tool(output_schema=_schema(constraints.ConstraintsResult, JobHandle), annotations={"readOnlyHint": True})
    def get_constraints(
        area_id: Annotated[str, Field(description="An area_id from resolve_area (in Poland).")],
    ) -> ToolResult:
        """Hazards and constraints for an area: flood zones (10%, 1%, 0.2%, embankment failure), protected
        nature (Natura 2000, parks, reserves), landslides, mining terrains and deposits, ground motion
        (subsidence, mm/year), road, rail, aircraft and industrial noise (Lden 55 and 65 dB, 2017 maps), and
        current IMGW weather and hydrological warnings. Shares are 0-1 of the area."""

        def work() -> Outcome:
            r = constraints.get_constraints(area_id)
            lines = [f"{r.area} ({_size(r.area_km2)}):"]
            for i in r.items:
                if i.share is not None:
                    share = "under 1%" if 0 < i.share < 0.005 else f"{i.share:.0%}"
                    detail = f"{share} of the area" + (f"; {', '.join(i.names[:4])}" if i.names else "")
                    if i.category == "noise" and i.value.get("share_lden_65_db_plus"):
                        detail += f" (65 dB or more: {i.value['share_lden_65_db_plus']:.0%})"
                elif i.value:
                    detail = ", ".join(f"{k} {v}" for k, v in i.value.items())
                else:
                    detail = f"{i.count} active" + (f": {'; '.join(i.names[:12])}" if i.names else "")
                if i.share or i.count or i.value or i.category == "flood":
                    lines.append(f"- {i.label}: {detail}")
            quiet = [
                i.label
                for i in r.items
                if not (i.share or i.count or i.value) and i.category != "flood"  # covers 0 shares and no warnings
            ]
            if quiet:
                lines.append("None found: " + "; ".join(quiet) + ".")
            lines.extend(f"Gap: {g}" for g in r.gaps)
            lines.extend(f"Notice: {n}" for n in r.notices)
            return r.model_dump(mode="json"), "\n".join(lines)

        def run() -> ToolResult:
            return maybe_job("get_constraints", constraints.estimate_s(area_id), work)

        return guard("get_constraints", run)

    @mcp.tool(output_schema=_schema(prices.PricesResult, JobHandle), annotations={"readOnlyHint": True})
    def get_prices(
        area_id: Annotated[str, Field(description="An area_id from resolve_area (in Poland, up to 25 km²).")],
        since_year: Annotated[int | None, Field(description="First year of sales. Default: five years ago.")] = None,
    ) -> ToolResult:
        """Real-estate transaction prices from the national price register (RCN, notarial deeds): yearly
        median, 25th and 75th percentile and count for apartments (PLN/m², new and resale), houses (PLN)
        and undeveloped plots (PLN/m²), free-market sales inside the area."""

        def work() -> Outcome:
            r = prices.get_prices(area_id, since_year)
            lines = [f"{r.area} ({_size(r.area_km2)}), {r.source}:"]
            for s in r.series:
                if s.years:
                    years = "; ".join(f"{y}: {v.median:,.0f} (n={v.count})" for y, v in s.years.items())
                    lines.append(f"- {s.description}, median {s.unit}: {years}")
            lines.extend(f"Notice: {n}" for n in r.notices)
            return r.model_dump(mode="json"), "\n".join(lines)

        def run() -> ToolResult:
            return maybe_job("get_prices", prices.estimate_s(area_id), work)

        return guard("get_prices", run)

    @mcp.tool(output_schema=_schema(zoning.ZoningResult, JobHandle), annotations={"readOnlyHint": True})
    def get_zoning(
        area_id: Annotated[str, Field(description="A parcel or small area_id (up to 5 km²) in Poland.")],
    ) -> ToolResult:
        """Local zoning plan (MPZP): designations (symbol and meaning, e.g. MN housing, U services, R farmland),
        plan name, resolution, adoption date and link, sampled at points of the area. Some gminas (Wrocław)
        do not publish to the national service."""

        def work() -> Outcome:
            r = zoning.get_zoning(area_id)
            lines = [f"{r.area}: {r.points_in_a_plan} of {r.points_sampled} sample points lie in a local plan."]
            for d in r.designations[:12]:
                what = " - ".join(x for x in (d.symbol, d.description) if x) or "plan without published designations"
                plan = ", ".join(x for x in (d.plan, d.adopted and f"adopted {d.adopted}", d.link) if x)
                lines.append(f"- {what} ({d.share_of_points:.0%} of points){f'; {plan}' if plan else ''}")
            lines.extend(f"No plan: {m}" for m in r.no_plan_messages[:3])
            if r.parcel:
                lines.append("Cadastre: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in r.parcel.items()))
            lines.extend(f"Gap: {g}" for g in r.gaps)
            lines.extend(f"Notice: {n}" for n in r.notices)
            return r.model_dump(mode="json"), "\n".join(lines)

        def run() -> ToolResult:
            return maybe_job("get_zoning", zoning.estimate_s(area_id), work)

        return guard("get_zoning", run)

    @mcp.tool(output_schema=_schema(traffic.TrafficResult, JobHandle), annotations={"readOnlyHint": True})
    def get_traffic(
        area_id: Annotated[str, Field(description="An area_id from resolve_area (in Poland).")],
    ) -> ToolResult:
        """Traffic on national roads crossing the area: vehicles per day in 2025, change since 2020/21 and
        heavy-vehicle share per measured road section (GDDKiA General Traffic Survey)."""

        def work() -> Outcome:
            r = traffic.get_traffic(area_id)
            lines = [f"{r.area}: national roads {', '.join(r.roads_in_area) or 'none'} ({r.survey})."]
            for s in r.segments:
                change = (
                    f", {s.vehicles_per_day / s.vehicles_per_day_2020 - 1:+.0%} since 2020/21"
                    if s.vehicles_per_day_2020
                    else ""
                )
                where = "measured inside the area" if s.distance_km == 0 else f"measured {s.distance_km} km away"
                lines.append(
                    f"- {s.road} {s.name}: {s.vehicles_per_day:,} vehicles/day{change}, heavy {s.heavy_share:.0%} "
                    f"({where}{f', {s.measured_at}' if s.measured_at else ''})"
                )
            for t in r.toll_trends:
                change = (
                    f", {t.change_vs_year_before:+.0%} on the year before"
                    if t.change_vs_year_before is not None
                    else ""
                )
                lines.append(
                    f"- e-TOLL {t.road}, whole road: {t.last_12_months:,} heavy-vehicle passages "
                    f"in the last 12 months{change}"
                )
            lines.extend(f"Notice: {n}" for n in r.notices)
            return r.model_dump(mode="json"), "\n".join(lines)

        def run() -> ToolResult:
            return maybe_job("get_traffic", traffic.estimate_s(area_id), work)

        return guard("get_traffic", run)

    @mcp.tool(output_schema=_schema(business.Business), annotations={"readOnlyHint": True})
    def lookup_business(
        identifier: Annotated[str, Field(description="A KRS, NIP or REGON number, e.g. 0000019193 or 5250000251.")],
    ) -> ToolResult:
        """A Polish company from the court register (KRS) and the VAT register: name, legal form, identifiers,
        VAT status, registered address (pass it to resolve_area to map it), share capital, main and other
        activities (PKD), how it is represented, board roles, corporate owners, and warning entries
        (arrears, liquidation, bankruptcy). Sole traders come from CEIDG. To search by name, use find_businesses."""

        def run() -> ToolResult:
            b = business.lookup_business(identifier)
            lines = [f"{b.name} ({b.legal_form or 'no KRS entry'})"]
            ids = ", ".join(f"{k} {v}" for k, v in (("KRS", b.krs), ("NIP", b.nip), ("REGON", b.regon)) if v)
            lines.append(f"{ids}; VAT: {b.vat_status or 'not a VAT payer'}")
            if b.address:
                lines.append(f"Address: {b.address}")
            if b.share_capital_pln:
                lines.append(f"Share capital: {b.share_capital_pln:,.0f} PLN")
            if b.main_activity:
                lines.append(f"Main activity: {b.main_activity}")
            if b.board_roles:
                lines.append(f"Board: {', '.join(b.board_roles)} (names are masked in KRS)")
            if b.owners:
                lines.append(f"Corporate owners: {', '.join(b.owners)}")
            if b.registered_on:
                lines.append(
                    f"Registered {b.registered_on}" + (f"; last entry {b.last_entry_on}" if b.last_entry_on else "")
                )
            lines.append("Warnings: " + ("; ".join(b.warnings) if b.warnings else "none in the register"))
            lines.append("Sources: " + "; ".join(b.sources))
            return ToolResult(
                content=[TextContent(type="text", text="\n".join(lines))], structured_content=b.model_dump(mode="json")
            )

        return guard("lookup_business", run)

    @mcp.tool(output_schema=_schema(ceidg.BusinessSearch, JobHandle), annotations={"readOnlyHint": True})
    def find_businesses(
        name: Annotated[str | None, Field(description="Words of the business name, e.g. 'piekarnia'.")] = None,
        area_id: Annotated[str | None, Field(description="Count in the gmina containing this area.")] = None,
        town: Annotated[str | None, Field(description="Town or village name, e.g. 'Smolec'.")] = None,
        pkd: Annotated[str | None, Field(description="PKD 2007 code, e.g. 56.10.A (restaurants).")] = None,
        active_only: Annotated[bool, Field(description="Only active businesses (default).")] = True,
    ) -> ToolResult:
        """Sole traders (CEIDG) by name, gmina, town and PKD activity: how many match, and up to 20 of them
        with NIP, address, status and start date. Use it to count e.g. restaurants or builders in a gmina.
        Companies (sp. z o.o., S.A.) are not in CEIDG; look them up by number with lookup_business."""

        def gmina() -> str | None:
            if not area_id:
                return None
            from geomcp_core.adapters.gus_bdl import _commune_at  # pyright: ignore[reportPrivateUsage]

            return re.sub(r"\s*\(.*\)$", "", _commune_at(area_from_id(area_id))[1])  # "Wrocław (miasto)"

        def work() -> Outcome:
            r = ceidg.search(name=name, gmina=gmina(), town=town, pkd=pkd, active_only=active_only)
            where = ", ".join(f"{k} {v}" for k, v in r.filters.items())
            lines = [f"{r.count:,} sole traders match ({where})."]
            lines += [f"- {t.name}, {t.address} (NIP {t.nip}, {t.status.lower()}, since {t.started})" for t in r.listed]
            lines.extend(f"Notice: {n}" for n in r.notices)
            return r.model_dump(mode="json"), "\n".join(lines)

        def run() -> ToolResult:
            eta = ceidg.estimate_s(name=name, gmina=gmina(), town=town, pkd=pkd, active_only=active_only)
            return maybe_job("find_businesses", eta, work)

        return guard("find_businesses", run)

    @mcp.tool(output_schema=_schema(JobStatus), annotations={"readOnlyHint": True})
    def get_job(
        job_id: Annotated[str, Field(description="A job_id returned by another tool.")],
    ) -> ToolResult:
        """Status and, once finished, the result of a long-running call."""

        def run() -> ToolResult:
            job = runner.wait(job_id, JOB_LONG_POLL_S)  # long-poll, so clients do not spin
            if job is None:
                raise GeomcpError("unknown_job", f"No job {job_id!r} (jobs are kept for 7 days).")
            payload = json.loads(job.result) if job.result else None
            status = JobStatus(
                job_id=job.id,
                tool=job.tool,
                status=job.status,  # type: ignore[arg-type]
                eta_s=job.eta_s,
                result=payload["structured"] if payload else None,
                error=json.loads(job.error) if job.error else None,
            )
            if job.status == "running":
                elapsed = (datetime.now(UTC) - job.created_at.replace(tzinfo=UTC)).total_seconds()
                left = max(10, round(job.eta_s - elapsed))
                text = (
                    f"Job {job_id} is still fetching data ({round(elapsed)} s so far, about {left} s left). "
                    "Call get_job again; do not shorten or change the request, it is progressing normally."
                )
            elif job.status == "failed":
                text = f"Job {job_id} failed: {status.error}"
            else:
                text = payload["text"] if payload else ""
            return _ok(status, text)

        return guard("get_job", run)

    @mcp.prompt(
        name="onboarding",
        description="Wprowadzenie do geomcp: jakie pytania można zadawać i skąd pochodzą odpowiedzi.",
    )
    def onboarding(
        place: Annotated[
            str | None, Field(description="Optional place the person cares about, e.g. 'gmina Sobótka'")
        ] = None,
    ) -> str:
        starter = (
            f'Then call resolve_area("{place}") and describe_data with that area_id, and tailor the three starter '
            f"questions to {place}, saying which sources cover it."
            if place
            else "Then suggest three starter questions about Wrocław and its suburbs (Smolec, Kąty Wrocławskie)."
        )
        return f"""Give a new geomcp user (a company co-founder, not a GIS specialist) a short, friendly
onboarding in Polish, right here in the chat. First call describe_data (no arguments) and build the guide
only from what it returns, so it is current. {starter}

Structure:
1. Czym jest geomcp (2-3 zdania): liczby z oficjalnych i satelitarnych źródeł dla dowolnego miejsca, ze
   źródłem, licencją i datą przy każdej liczbie; działa w Polsce najpełniej, część danych w Europie i na świecie.
2. Jak pytać: miejsce podaj jako gminę, adres, numer działki ewidencyjnej albo współrzędne; można prosić o
   porównania kilku miejsc, trendy z wielu lat i obraz z satelity lub zdjęcie lotnicze. Gotowe raporty:
   site_profile, risk_profile, investment_profile.
3. Tematy: for each category from describe_data, one line on what it answers, two example questions in
   Polish (use the catalogue's examples), and the sources in brackets (e.g. GUS, GIOŚ, GDDKiA, CEIDG, JRC).
4. Ważne ograniczenia (krótko): pierwsze pytanie o duży obszar lub długi okres może trwać 1-3 minuty
   (powtórka jest natychmiastowa); źródła oznaczone jako niekomercyjne (np. prognozy klimatu) nie nadają się do
   raportów dla klientów bez licencji; dane o firmach obejmują spółki po numerze KRS/NIP i jednoosobowe
   działalności także po nazwie.
5. Trzy pytania na start, gotowe do skopiowania, each using several sources at once.

Keep it under about 450 words, use headings and short bullet lists, no tables wider than three columns.
Do not call any other tool unless needed for the place above. End by asking which place or question to try
first."""

    @mcp.prompt(name="site_profile", description="A standard sourced briefing about one place.")
    def site_profile(
        place: Annotated[str, Field(description="A gmina, address, parcel id or 'lat,lon'")],
        years: Annotated[int, Field(ge=1, le=10, description="How many past full years to analyse")] = 10,
    ) -> str:
        last = datetime.now(UTC).year - 1
        first = last - years + 1
        return f"""Prepare a concise site profile of {place} for decision makers, using only the geomcp tools.

1. resolve_area("{place}"). If it is ambiguous, ask which place is meant before continuing.
2. get_summary with: elevation_ground, slope_ground (or elevation, slope outside Poland), landcover, buildings,
   road_length, schools, healthcare, shops, transit_stops, population, population_density, net_migration,
   budget_income_per_capita, ndvi, t2m, precipitation, no2_column, pm10_station, pm25_station.
3. get_timeseries for ndvi, t2m and precipitation from {first}-01-01 to {last + 1}-01-01 with cadence P1Y.
   If it returns a job_id, call get_job until it finishes; do not shorten the period.
4. get_snapshot with layer true_color.

Write the profile in the language the user writes in, with sections: Location and terrain; Land cover and
built environment; Population and economy; Environment and climate trends ({first}-{last}); Air quality now;
Image. Give numbers with units, say which trends are statistically significant (p < 0.05), and end with
Sources (name, licence) and Caveats (gaps, preliminary values, restricted data, resolution limits).
Use only values and links the tools returned: never invent URLs, image links, domains or thresholds; the image
from get_snapshot is shown by the client, so just describe it."""

    @mcp.prompt(name="risk_profile", description="Climate and environmental risks and constraints of one place.")
    def risk_profile(
        place: Annotated[str, Field(description="A gmina, address, parcel id or 'lat,lon' in Poland")],
        years: Annotated[int, Field(ge=5, le=30, description="How many past full years of climate to analyse")] = 20,
    ) -> str:
        last = datetime.now(UTC).year - 1
        first = last - years + 1
        return f"""Prepare a risk profile of {place} for decision makers, using only the geomcp tools.

1. resolve_area("{place}"). If it is ambiguous, ask which place is meant before continuing.
2. get_constraints: flood zones, protected nature, landslides, mining, ground motion, noise, drought and
   current warnings.
3. get_timeseries for hot_days, tropical_nights, heavy_rain_days, dry_days and frost_days from {first}-01-01
   to {last + 1}-01-01 with cadence P1Y. If it returns a job_id, call get_job until it finishes.
4. get_summary with: elevation_ground, slope_ground, landcover, soil_moisture, pm10_station, pm25_station,
   pm10_history, climate_projection.

Write the profile in the language the user writes in, with sections: Flooding; Nature protection (what limits
building); Ground (landslides, mining, subsidence); Heat, heavy rain and drought trends ({first}-{last}, say which
trends are significant at p < 0.05, and the projection to 2031-2050); Noise; Air quality now and its trend;
Current warnings and drought; Overall assessment (high, medium, low per
risk, with the numbers behind it). End with Sources (name, licence) and Caveats (gaps, resolution, what an
on-site survey or official certificate would still need to confirm). Use only values the tools returned."""

    @mcp.prompt(name="investment_profile", description="Investment briefing for a site or district in Poland.")
    def investment_profile(
        place: Annotated[str, Field(description="A parcel id, address or 'lat,lon' in Poland, or a small district")],
    ) -> str:
        return f"""Prepare an investment profile of {place} for decision makers, using only the geomcp tools.

1. resolve_area("{place}") with a buffer of about 1000 m for a point or address. If ambiguous, ask first.
2. get_zoning: what the local plan allows here.
3. get_prices: apartment, house and land prices and their trend.
4. get_constraints: flood zones, protected nature, mining, ground motion.
5. get_traffic: national roads and their traffic.
6. get_summary with: building_functions, schools, healthcare, shops, transit_stops, road_length,
   population_grid, population, net_migration, landcover_history, solar_potential, elevation_ground, slope_ground.
7. get_snapshot with layer orthophoto.
For any tool that returns a job_id, call get_job until it finishes.

Write in the language the user writes in, with sections: Site and zoning; Market prices (medians per year with
counts, trend); Access and traffic; Buildings and services nearby; Population in the area and the gmina, and
how the land was built up since 1990; Solar potential; Risks and constraints (including noise); Opportunities and
red flags. End with Sources (name, licence) and Caveats (what the plan text, a land-registry extract or a site
visit must confirm). Use only values the tools returned; never invent prices or designations."""

    @mcp.resource("geomcp://sources", mime_type="text/markdown")
    def sources_catalogue() -> str:
        """The data catalogue: sources, variables, units, coverage and licences."""
        out = ["# geomcp data sources", ""]
        for s in service.list_data():
            i = s.source
            out += [
                f"## {i.name} ({i.id})",
                "",
                f"Licence: {i.licence}; commercial-safe: {i.commercial_safe}.",
                f"Attribution: {i.attribution}",
                f"Resolution: {i.resolution}; coverage from {i.coverage[0]}.",
                "",
            ]
            out += [f"- `{v.name}` [{v.units}] ({v.topic}): {v.description}" for v in i.variables]
            out.append("")
        out.append("Snapshot layers: " + ", ".join(sorted(LAYERS)) + ".")
        return "\n".join(out)

    @mcp.custom_route("/metrics", methods=["GET"])
    async def prometheus(request: Request) -> Response:
        """Prometheus metrics; over HTTP it needs an API key (e.g. one named "prometheus")."""
        if auth is not None:
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not token or await auth.verify_token(token) is None:
                return JSONResponse({"error": "metrics need an API key (Authorization: Bearer ...)"}, status_code=401)
        return Response(metrics.prometheus_text(db_url), media_type=CONTENT_TYPE_LATEST)

    @mcp.custom_route("/files/{name}", methods=["GET"])
    async def files(request: Request) -> Response:
        return await downloads.serve(request)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": version("geomcp-server")})

    return mcp
