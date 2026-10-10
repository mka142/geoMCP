"""Recorded HTTP (VCR) for the real adapters: secrets never reach the cassettes.

Record or refresh with real credentials:  uv run pytest tests/recorded --record-mode=rewrite
Replay (CI, no credentials):              uv run pytest tests/recorded
"""

import json
import os
import re
from collections.abc import Iterator
from typing import Any

import pytest

from geomcp_core.adapters import cdse
from tests.conftest import CREDENTIAL_VARS, REPO

SCRUBBED = "REDACTED"


PERSONAL = ("nazwa", "imie", "nazwisko", "ulica", "budynek", "lokal", "nip", "regon")


def _redact(value: Any) -> Any:
    """CEIDG lists sole traders, who are people: their names, addresses and numbers stay out of git."""
    if isinstance(value, dict):
        return {k: SCRUBBED if k in PERSONAL and isinstance(v, str) else _redact(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_redact(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body", {}).get("string")
    if isinstance(body, bytes) and b'"firmy"' in body and b"adresDzialalnosci" in body:
        response["body"]["string"] = json.dumps(_redact(json.loads(body)), ensure_ascii=False).encode()
        body = response["body"]["string"]
    if isinstance(body, bytes) and b"<ms:DOK_TWORCA" in body:  # RCN: notary names and deed numbers
        body = re.sub(rb"<ms:(DOK_TWORCA|DOK_OZNACZENIE)>[^<]*</ms:\1>", rb"<ms:\1>REDACTED</ms:\1>", body)
        response["body"]["string"] = body
    if isinstance(body, bytes) and b"access_token" in body:
        payload = json.loads(body)
        for key in ("access_token", "refresh_token", "id_token"):
            if key in payload:
                payload[key] = SCRUBBED
        response["body"]["string"] = json.dumps(payload).encode()
    return response


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return {
        "filter_headers": ["authorization", "private-token", "x-clientid", "sid", "cookie", "set-cookie"],
        "filter_post_data_parameters": [("client_id", SCRUBBED), ("client_secret", SCRUBBED)],
        "before_record_response": _scrub_response,
        "match_on": ["method", "scheme", "host", "path", "query", "body"],
        "decode_compressed_response": True,
    }


@pytest.fixture(autouse=True)
def own_cache(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each recorded test starts with an empty cache, so its cassette holds every call it needs."""
    monkeypatch.setenv("GEOMCP_DATA_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def credentials(request: pytest.FixtureRequest) -> Iterator[None]:
    """Real credentials when recording; placeholders when replaying (requests carry no secrets)."""
    from dotenv import dotenv_values

    recording = request.config.getoption("--record-mode") in ("once", "new_episodes", "all", "rewrite")
    real = {k: v for k, v in dotenv_values(REPO / ".env").items() if k in CREDENTIAL_VARS and v}
    os.environ.update({v: (real.get(v) or "") if recording else SCRUBBED for v in CREDENTIAL_VARS})
    cdse._token = None  # pyright: ignore[reportPrivateUsage]
    yield
    for v in CREDENTIAL_VARS:
        os.environ.pop(v, None)
    cdse._token = None  # pyright: ignore[reportPrivateUsage]
