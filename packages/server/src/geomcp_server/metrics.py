"""Simple usage metrics: a SQLite call log (for `geomcp-admin stats`) and Prometheus counters (`/metrics`)."""

from datetime import UTC, datetime, timedelta
from statistics import median

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import select

from geomcp_server.db import Job, ToolCall, sessions

REGISTRY = CollectorRegistry()
CALLS = Counter("geomcp_tool_calls", "Tool calls", ["tool", "outcome"], registry=REGISTRY)
DURATION = Histogram(
    "geomcp_tool_duration_seconds",
    "Tool call duration (calls that became jobs count only the hand-off)",
    ["tool"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 45, 90, 180, 600),
    registry=REGISTRY,
)
UNITS = Counter("geomcp_sentinel_hub_units", "Sentinel Hub processing units spent", registry=REGISTRY)
CACHE = Counter("geomcp_cache_lookups", "Adapter cache lookups", ["result"], registry=REGISTRY)
JOBS_RUNNING = Gauge("geomcp_jobs_running", "Background jobs running", registry=REGISTRY)


def record(db_url: str, tool: str, client: str, ok: bool, code: str | None, duration_s: float) -> None:
    CALLS.labels(tool=tool, outcome="ok" if ok else (code or "error")).inc()
    DURATION.labels(tool=tool).observe(duration_s)
    with sessions(db_url).begin() as s:
        s.add(
            ToolCall(at=datetime.now(UTC), tool=tool, client=client, ok=ok, code=code, duration_s=duration_s, units=0.0)
        )


def spent(db_url: str, client: str, units: float, cache_hits: int, cache_misses: int) -> None:
    """Attribute provider cost to the latest call of this client (jobs finish after their call was logged)."""
    UNITS.inc(units)
    CACHE.labels(result="hit").inc(cache_hits)
    CACHE.labels(result="miss").inc(cache_misses)
    if units <= 0:
        return
    with sessions(db_url).begin() as s:
        last = s.scalars(
            select(ToolCall).where(ToolCall.client == client).order_by(ToolCall.id.desc()).limit(1)
        ).first()
        if last is not None:
            last.units += units


def prometheus_text(db_url: str) -> bytes:
    with sessions(db_url)() as s:
        JOBS_RUNNING.set(len(s.scalars(select(Job.id).where(Job.status == "running")).all()))
    return generate_latest(REGISTRY)


def stats_table(db_url: str, days: int) -> str:
    """A plain-text table per person and tool for the last `days` days."""
    since = datetime.now(UTC) - timedelta(days=days)
    with sessions(db_url)() as s:
        rows = s.scalars(select(ToolCall).where(ToolCall.at >= since)).all()
    if not rows:
        return f"No tool calls in the last {days} days."
    groups: dict[tuple[str, str], list[ToolCall]] = {}
    for r in rows:
        groups.setdefault((r.client, r.tool), []).append(r)
    header = f"{'person':<20} {'tool':<16} {'calls':>6} {'errors':>6} {'median s':>9} {'PU':>8}"
    lines = [f"Last {days} days ({len(rows)} calls)", header, "-" * len(header)]
    for (client, tool), calls in sorted(groups.items()):
        errors = sum(not c.ok for c in calls)
        lines.append(
            f"{client[:20]:<20} {tool:<16} {len(calls):>6} {errors:>6} "
            f"{median(c.duration_s for c in calls):>9.1f} {sum(c.units for c in calls):>8.1f}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'total':<37} {len(rows):>6} {sum(not r.ok for r in rows):>6} {'':>9} {sum(r.units for r in rows):>8.1f}"
    )
    return "\n".join(lines)
