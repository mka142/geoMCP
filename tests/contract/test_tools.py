"""MCP contract: tools, schemas, structured results with text, jobs and structured errors (offline)."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastmcp import Client

from geomcp_core.models import TimeseriesResult
from geomcp_core.service import CompareResult, DescribeResult, SummaryResult
from geomcp_server.app import JobHandle, JobStatus, ListDataResult, ResolveAreaResult, build_server
from geomcp_server.settings import Settings
from tests.conftest import GOLDEN_AREA_ID, GOLDEN_QUERY, structured, text_of

TOOLS = {
    "resolve_area",
    "list_data",
    "describe_data",
    "find_statistics",
    "get_statistics",
    "get_constraints",
    "get_prices",
    "get_zoning",
    "get_traffic",
    "lookup_business",
    "find_businesses",
    "get_timeseries",
    "get_summary",
    "compare_areas",
    "get_snapshot",
    "get_job",
}
OTHER_AREA = "a_pt_51.0500_16.9000_sq1581"


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[Client]:
    async with Client(build_server(settings=Settings(data_dir=tmp_path))) as c:
        yield c


async def test_tools_are_listed_with_descriptions_and_output_schemas(client: Client) -> None:
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.description and t.output_schema and t.output_schema.get("type") == "object"
        assert t.annotations is not None and t.annotations.read_only_hint


async def test_sources_resource_lists_licences(client: Client) -> None:
    (content,) = await client.read_resource("geomcp://sources")
    text = getattr(content, "text", "")
    assert "## Sentinel-2 L2A (sentinel2)" in text and "Licence:" in text and "`ndvi` [1]" in text


async def test_resolve_area_returns_structured_and_text(client: Client) -> None:
    r = await client.call_tool("resolve_area", {"query": GOLDEN_QUERY, "buffer_m": 1581, "shape": "square"})
    result = ResolveAreaResult.model_validate(structured(r))
    assert result.selected == GOLDEN_AREA_ID
    assert "10.0 km²" in text_of(r)


async def test_list_data_reports_unconfigured_sources_with_hints(client: Client) -> None:
    r = await client.call_tool("list_data", {})
    result = ListDataResult.model_validate(structured(r))
    by_id = {s.source.id: s for s in result.sources}
    assert by_id["synthetic"].health == "ok"
    for real in ("sentinel2", "sentinel1", "sentinel5p", "copernicus_dem", "era5_land"):
        assert by_id[real].health == "unconfigured" and "GEOMCP_" in by_id[real].hint
    assert result.sentinel_hub_quota.units_limit == 10_000
    assert "RESTRICTED" in text_of(r) and "UNCONFIGURED" in text_of(r)


async def test_get_timeseries_matches_schema_and_summarises(client: Client) -> None:
    r = await client.call_tool(
        "get_timeseries",
        {
            "area_id": GOLDEN_AREA_ID,
            "variables": ["synthetic_ndvi", "synthetic_station_t2m"],
            "start": "2016-01-01",
            "end": "2026-01-01",
        },
    )
    result = TimeseriesResult.model_validate(structured(r))
    assert result.period.n_periods == 40
    assert "synthetic_ndvi: seasonal trend" in text_of(r) and "Notice:" in text_of(r)


async def test_unconfigured_source_becomes_a_gap_not_a_failure(client: Client) -> None:
    r = await client.call_tool(
        "get_timeseries",
        {"area_id": GOLDEN_AREA_ID, "variables": ["synthetic_t2m", "ndvi"], "start": "2024-01-01", "end": "2025-01-01"},
    )
    result = TimeseriesResult.model_validate(structured(r))
    assert [s.variable for s in result.series] == ["synthetic_t2m"]
    assert any(g.variable == "ndvi" and g.reason == "source_unavailable" for g in result.gaps)
    assert any("GEOMCP_CDSE_CLIENT_ID" in n for n in result.notices)


async def test_get_summary_reports_static_facts(client: Client) -> None:
    r = await client.call_tool("get_summary", {"area_id": GOLDEN_AREA_ID, "variables": ["synthetic_elevation"]})
    result = SummaryResult.model_validate(structured(r))
    (fact,) = result.facts
    assert fact.units == "m" and isinstance(fact.value, dict)
    lo, hi = fact.value["min"], fact.value["max"]
    assert lo is not None and hi is not None and lo < hi


async def test_static_variable_in_timeseries_points_to_get_summary(client: Client) -> None:
    r = await client.call_tool(
        "get_timeseries",
        {"area_id": GOLDEN_AREA_ID, "variables": ["synthetic_elevation"], "start": "2024-01-01", "end": "2025-01-01"},
    )
    result = TimeseriesResult.model_validate(structured(r))
    assert result.gaps[0].reason == "static_variable" and "get_summary" in result.notices[0]


async def test_compare_areas_ranks_high_to_low(client: Client) -> None:
    r = await client.call_tool(
        "compare_areas",
        {
            "area_ids": [GOLDEN_AREA_ID, OTHER_AREA],
            "variable": "synthetic_t2m",
            "start": "2024-06-01",
            "end": "2024-09-01",
        },
    )
    result = CompareResult.model_validate(structured(r))
    values = [x.value for x in result.ranking if x.value is not None]
    assert len(values) == 2 and values == sorted(values, reverse=True) and result.aggregation.startswith("mean")


async def test_long_calls_become_jobs(tmp_path: Path) -> None:
    async with Client(build_server(settings=Settings(data_dir=tmp_path, sync_budget_s=-1))) as c:
        r = await c.call_tool(
            "get_timeseries",
            {"area_id": GOLDEN_AREA_ID, "variables": ["synthetic_t2m"], "start": "2024-01-01", "end": "2025-01-01"},
        )
        handle = JobHandle.model_validate(structured(r))
        assert "get_job" in text_of(r)
        status = JobStatus.model_validate(structured(await c.call_tool("get_job", {"job_id": handle.job_id})))
        for _ in range(50):
            if status.status != "running":
                break
            await asyncio.sleep(0.1)
            status = JobStatus.model_validate(structured(await c.call_tool("get_job", {"job_id": handle.job_id})))
        assert status.status == "succeeded" and status.result is not None
        assert TimeseriesResult.model_validate(status.result).period.n_periods == 12


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (
            {"area_id": GOLDEN_AREA_ID, "variables": ["nope"], "start": "2024-01-01", "end": "2025-01-01"},
            "unknown_variable",
        ),
        (
            {"area_id": "a_x", "variables": ["synthetic_t2m"], "start": "2024-01-01", "end": "2025-01-01"},
            "unknown_area",
        ),
        (
            {
                "area_id": GOLDEN_AREA_ID,
                "variables": ["synthetic_t2m"],
                "start": "2024-06-04",
                "end": "2024-07-01",
                "cadence": "P1W",
            },
            "invalid_range",
        ),
        (
            {
                "area_id": "a_pt_51.1100_17.0320_sq20000",
                "variables": ["synthetic_t2m"],
                "start": "2024-01-01",
                "end": "2025-01-01",
            },
            "area_too_large",
        ),
    ],
)
async def test_expected_failures_are_structured_errors(client: Client, args: dict[str, object], code: str) -> None:
    r = await client.call_tool("get_timeseries", args, raise_on_error=False)
    assert r.is_error
    assert structured(r)["error"]["code"] == code
    assert structured(r)["error"]["message"] in text_of(r)


async def test_unknown_job_is_a_structured_error(client: Client) -> None:
    r = await client.call_tool("get_job", {"job_id": "j_nope"}, raise_on_error=False)
    assert r.is_error and structured(r)["error"]["code"] == "unknown_job"


async def test_unknown_variable_suggests_close_names(client: Client) -> None:
    r = await client.call_tool(
        "get_timeseries",
        {"area_id": GOLDEN_AREA_ID, "variables": ["temperature"], "start": "2024-01-01", "end": "2025-01-01"},
        raise_on_error=False,
    )
    assert r.is_error and "Did you mean" in structured(r)["error"]["hint"] and "t2m" in structured(r)["error"]["hint"]


async def test_site_profile_prompt_chains_the_tools(client: Client) -> None:
    prompts = {p.name for p in await client.list_prompts()}
    assert "site_profile" in prompts
    result = await client.get_prompt("site_profile", {"place": "gmina Kąty Wrocławskie", "years": 5})
    text = getattr(result.messages[0].content, "text", "")
    assert 'resolve_area("gmina Kąty Wrocławskie")' in text and "get_job" in text and "cadence P1Y" in text


async def test_risk_profile_prompt_uses_constraints_and_extremes(client: Client) -> None:
    result = await client.get_prompt("risk_profile", {"place": "gmina Sobótka"})
    text = getattr(result.messages[0].content, "text", "")
    assert "get_constraints" in text and "hot_days" in text and 'resolve_area("gmina Sobótka")' in text


async def test_onboarding_prompt_builds_on_the_catalogue(client: Client) -> None:
    result = await client.get_prompt("onboarding", {})
    text = getattr(result.messages[0].content, "text", "")
    assert "describe_data" in text and "Polish" in text and "resolve_area" not in text
    tailored = await client.get_prompt("onboarding", {"place": "gmina Sobótka"})
    assert 'resolve_area("gmina Sobótka")' in getattr(tailored.messages[0].content, "text", "")


async def test_investment_profile_prompt_uses_zoning_prices_and_traffic(client: Client) -> None:
    result = await client.get_prompt("investment_profile", {"place": "Smolec"})
    text = getattr(result.messages[0].content, "text", "")
    assert all(t in text for t in ("get_zoning", "get_prices", "get_traffic", "orthophoto"))


async def test_describe_data_groups_sources_and_checks_the_area(client: Client) -> None:
    r = await client.call_tool("describe_data", {"area_id": "a_pt_48.8566_2.3522_ci1000"})  # Paris
    result = DescribeResult.model_validate(structured(r))
    items = {i.entry.id: i for group in result.categories.values() for i in group}
    assert items["era5_land"].available_here is True and items["gios"].available_here is False
    assert "NOT available for this area" in text_of(r) and result.planned
