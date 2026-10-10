"""Full tables as files: local paths over stdio, signed expiring links over HTTP."""

import time
from pathlib import Path

import httpx
from fastmcp import Client
from starlette.applications import Starlette
from starlette.routing import Route

from geomcp_core.models import TimeseriesResult
from geomcp_server.app import build_server
from geomcp_server.downloads import Downloads
from geomcp_server.settings import Settings
from tests.conftest import GOLDEN_AREA_ID, structured, text_of

ARGS = {"area_id": GOLDEN_AREA_ID, "variables": ["synthetic_t2m"], "start": "2016-01-01", "end": "2026-01-01"}


async def test_stdio_mode_writes_the_full_table_to_local_files(tmp_path: Path) -> None:
    async with Client(build_server(settings=Settings(data_dir=tmp_path))) as c:
        r = await c.call_tool("get_timeseries", ARGS | {"max_rows": 4})
    result = TimeseriesResult.model_validate(structured(r))
    assert len(result.rows) <= 4
    csv_lines = Path(result.downloads["csv"]).read_text().splitlines()
    assert csv_lines[0].startswith("period,variable,mean") and len(csv_lines) == 1 + 40  # every row, not the sample
    assert Path(result.downloads["parquet"]).stat().st_size > 0
    assert "Full table:" in text_of(r)


async def test_http_links_are_signed_and_expire(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GEOMCP_DATA_DIR", str(tmp_path))
    async with Client(build_server(settings=Settings(data_dir=tmp_path), public_url="https://geo.test")) as c:
        result = TimeseriesResult.model_validate(structured(await c.call_tool("get_timeseries", ARGS)))
    link = result.downloads["csv"]
    assert link.startswith("https://geo.test/files/timeseries-") and "&sig=" in link
    downloads = Downloads(tmp_path, "https://geo.test")
    app = Starlette(routes=[Route("/files/{name}", downloads.serve)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://geo.test") as http:
        ok = await http.get(link.removeprefix("https://geo.test"))
        assert ok.status_code == 200 and ok.text.startswith("period,variable")
        tampered = await http.get(link.removeprefix("https://geo.test").replace("sig=", "sig=0"))
        assert tampered.status_code == 403
        name = link.split("/files/")[1].split("?")[0]
        monkeypatch.setattr(time, "time", lambda: 10**10)  # far future: the link has expired
        expired = await http.get(link.removeprefix("https://geo.test"))
        assert expired.status_code == 410 and name
