"""Usage metrics: every call is logged per person, stats summarise them, /metrics speaks Prometheus."""

from pathlib import Path

import httpx
from fastmcp import Client
from starlette.applications import Starlette

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
