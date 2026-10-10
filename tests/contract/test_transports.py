"""End-to-end: the installed `geomcp` command over stdio, and over HTTP with API keys and Google sign-in."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from tests.conftest import GOLDEN_AREA_ID, GOLDEN_QUERY, structured

BIN = Path(sys.executable).parent


def _env(data_dir: Path) -> dict[str, str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("GEOMCP_CDSE", "GEOMCP_CDS"))}
    return {**clean, "GEOMCP_ENABLE_SYNTHETIC": "1", "GEOMCP_DATA_DIR": str(data_dir)}


async def test_stdio_transport(tmp_path: Path) -> None:
    transport = StdioTransport(
        command=str(BIN / "geomcp"), args=["serve", "--stdio"], env=_env(tmp_path), cwd=str(tmp_path)
    )
    async with Client(transport) as c:
        r = await c.call_tool("resolve_area", {"query": GOLDEN_QUERY, "buffer_m": 1581, "shape": "square"})
        assert structured(r)["selected"] == GOLDEN_AREA_ID


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(base: str) -> None:
    for _ in range(100):
        try:
            if httpx.get(f"{base}/healthz").status_code == 200:
                return
        except httpx.TransportError:
            time.sleep(0.1)
    raise RuntimeError("server did not start")


GOOGLE = {
    "GEOMCP_GOOGLE_CLIENT_ID": "123-test.apps.googleusercontent.com",
    "GEOMCP_GOOGLE_CLIENT_SECRET": "test-secret",
    "GEOMCP_ALLOWED_EMAILS": "@example.org",
}


@pytest.fixture(params=[False, True], ids=["keys", "keys+google"])
def http_server(tmp_path: Path, request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    env = _env(tmp_path) | (GOOGLE if request.param else {})
    token = subprocess.run(
        [BIN / "geomcp-admin", "keys", "create", "--name", "test"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    port = _free_port()
    proc = subprocess.Popen(
        [BIN / "geomcp", "serve", "--http", "--port", str(port)], env=env, cwd=tmp_path, stderr=subprocess.PIPE
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for(base)
        yield base, token
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_http_requires_a_valid_key(http_server: tuple[str, str]) -> None:
    base, _ = http_server
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    assert httpx.post(f"{base}/mcp", json=body, headers=headers).status_code == 401
    bad = {**headers, "Authorization": "Bearer gmk_000000000000_nope"}
    assert httpx.post(f"{base}/mcp", json=body, headers=bad).status_code == 401


async def test_http_answers_with_a_valid_key(http_server: tuple[str, str]) -> None:
    base, token = http_server
    async with Client(StreamableHttpTransport(f"{base}/mcp", auth=token)) as c:
        r = await c.call_tool(
            "get_timeseries",
            {
                "area_id": GOLDEN_AREA_ID,
                "variables": ["synthetic_t2m"],
                "start": "2024-01-01",
                "end": "2025-01-01",
            },
        )
        assert structured(r)["period"]["n_periods"] == 12


def test_google_sign_in_is_discoverable(tmp_path: Path) -> None:
    """OAuth-only clients (Claude Desktop connectors) find the sign-in flow from the 401 alone."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = _env(tmp_path) | GOOGLE | {"GEOMCP_PUBLIC_URL": base}
    proc = subprocess.Popen([BIN / "geomcp", "serve", "--http", "--port", str(port)], env=env, cwd=tmp_path)
    try:
        _wait_for(base)
        r = httpx.post(f"{base}/mcp", json={}, headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
        meta = httpx.get(f"{base}/.well-known/oauth-protected-resource/mcp").json()
        server = httpx.get(f"{base}/.well-known/oauth-authorization-server").json()
        assert meta["authorization_servers"] and server["registration_endpoint"].startswith(base)
        register = {"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "test"}
        client = httpx.post(server["registration_endpoint"], json=register).json()
        start = httpx.get(
            server["authorization_endpoint"],
            params={
                "response_type": "code", "client_id": client["client_id"], "redirect_uri": register["redirect_uris"][0],
                "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s",
            },
        )  # fmt: skip
        assert start.status_code in (200, 302)  # consent page, then Google
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_allowed_hosts_reject_other_host_headers(tmp_path: Path) -> None:
    """GEOMCP_ALLOWED_HOSTS is enforced: a request for another host name is misdirected (421), not served."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = _env(tmp_path) | {"GEOMCP_ALLOWED_HOSTS": '["geo.example.org"]'}
    proc = subprocess.Popen([BIN / "geomcp", "serve", "--http", "--port", str(port)], env=env, cwd=tmp_path)
    try:
        _wait_for(base)
        assert httpx.post(f"{base}/mcp", headers={"Host": "evil.example"}).status_code == 421
        assert httpx.post(f"{base}/mcp", headers={"Host": "geo.example.org"}).status_code == 401  # then auth
        assert httpx.get(f"{base}/healthz").status_code == 200  # the container's own health check keeps working
    finally:
        proc.terminate()
        proc.wait(timeout=10)
