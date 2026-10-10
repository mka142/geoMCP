"""Usage metrics: every call is logged per person, stats summarise them, /metrics speaks Prometheus."""

from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from starlette.applications import Starlette

from geomcp_core import service
from geomcp_server import auth
from geomcp_server.app import build_server
from geomcp_server.metrics import stats_table
from geomcp_server.settings import Settings
from tests.conftest import GOLDEN_AREA_ID, GOLDEN_QUERY


async def test_calls_are_logged_and_summarised(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    async with Client(build_server(settings=settings)) as c:
        await c.call_tool("resolve_area", {"query": GOLDEN_QUERY, "buffer_m": 1581, "shape": "square"})
        args = {"area_id": GOLDEN_AREA_ID, "variables": ["nope"], "start": "2024-01-01", "end": "2025-01-01"}
        await c.call_tool("get_timeseries", args, raise_on_error=False)
    table = stats_table(settings.database_url, 30)
    assert "local" in table and "resolve_area" in table and "get_timeseries" in table
    assert "2 calls" in table


async def test_metrics_endpoint_needs_a_key_over_http(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    server = build_server(auth=auth.ApiKeyVerifier(settings.database_url), settings=settings)
    token = auth.create_key(settings.database_url, "prometheus")
    app: Starlette = server.http_app(path="/mcp")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/metrics")).status_code == 401
        ok = await http.get("/metrics", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200 and "geomcp_tool_calls_total" in ok.text and "geomcp_jobs_running" in ok.text


async def test_units_are_attributed_to_the_call_that_spent_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = service.get_timeseries

    def costly(*args: object, **kwargs: object) -> object:
        r = real(*args, **kwargs)  # type: ignore[arg-type]
        r.cost.sentinel_hub_units = 7.0
        return r

    monkeypatch.setattr(service, "get_timeseries", costly)
    settings = Settings(data_dir=tmp_path)
    async with Client(build_server(settings=settings)) as c:
        args = {"area_id": GOLDEN_AREA_ID, "variables": ["synthetic_t2m"], "start": "2024-01-01", "end": "2025-01-01"}
        await c.call_tool("get_timeseries", args)
    row = next(line for line in stats_table(settings.database_url, 30).splitlines() if "get_timeseries" in line)
    assert row.split()[-1] == "7.0"
