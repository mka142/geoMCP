import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

# Isolate every test run: synthetic demo sources on, a throwaway data dir, and no real credentials
# (live tests load them explicitly). All of this must happen before geomcp modules are imported.
os.environ["GEOMCP_ENABLE_SYNTHETIC"] = "1"
os.environ["GEOMCP_DATA_DIR"] = tempfile.mkdtemp(prefix="geomcp-test-")
CREDENTIAL_VARS = (
    "GEOMCP_CDSE_CLIENT_ID", "GEOMCP_CDSE_CLIENT_SECRET", "GEOMCP_CDS_KEY", "GEOMCP_CEIDG_TOKEN",
    "GEOMCP_CONTACT",  # an operator email; keeps it out of recorded User-Agent headers
)  # fmt: skip
REPO = Path(__file__).resolve().parents[1]
_saved = {v: os.environ.pop(v) for v in CREDENTIAL_VARS if v in os.environ}

GOLDEN_QUERY = "51.110,17.032"
GOLDEN_AREA_ID = "a_pt_51.1100_17.0320_sq1581"


def text_of(result: CallToolResult) -> str:
    block = next(b for b in result.content if isinstance(b, TextContent))
    return block.text


def structured(result: CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def live_credentials(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Real credentials from the environment or the repository's .env; skip when absent."""
    from dotenv import dotenv_values

    values = {k: v for k, v in dotenv_values(REPO / ".env").items() if k in CREDENTIAL_VARS and v}
    values = {**values, **_saved}
    if not all(values.get(v) for v in CREDENTIAL_VARS):
        pytest.skip("live test: CDSE and CDS credentials are not configured")
    os.environ.update(values)  # type: ignore[arg-type]
    yield
    for v in CREDENTIAL_VARS:
        os.environ.pop(v, None)
