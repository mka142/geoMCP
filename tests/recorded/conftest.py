"""Recorded HTTP (VCR) for the real adapters: secrets never reach the cassettes.

Record or refresh with real credentials:  uv run pytest tests/recorded --record-mode=rewrite
Replay (CI, no credentials):              uv run pytest tests/recorded
"""

import json
import os
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote_plus

import pytest

from geomcp_core.adapters import cdse
from tests.conftest import CREDENTIAL_VARS, REPO

SCRUBBED = "REDACTED"


# CEIDG fields that name a person or point straight at their record (id and link open the public entry)
PERSONAL = (
    "nazwa",
    "imie",
    "nazwisko",
    "ulica",
    "budynek",
    "lokal",
    "nip",
    "regon",
    "id",
    "link",
    "email",
    "telefon",
    "www",
)


def _redact(value: Any) -> Any:
    """CEIDG lists sole traders, who are people: their names, addresses and numbers stay out of git."""
    if isinstance(value, dict):
        return {k: SCRUBBED if k in PERSONAL and isinstance(v, str) else _redact(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_redact(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _without_accounts(value: Any) -> Any:
    """The VAT whitelist lists a company's bank accounts; geomcp never keeps them, so neither does git."""
    if isinstance(value, dict):
        return {k: [] if k == "accountNumbers" else _without_accounts(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_without_accounts(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body", {}).get("string")
    if isinstance(body, str):
        body = body.encode()
    if not isinstance(body, bytes):
        return response
    if b'"firmy"' in body and b"adresDzialalnosci" in body:
        body = json.dumps(_redact(json.loads(body)), ensure_ascii=False).encode()
    if b"<ms:DOK_TWORCA" in body:  # RCN: notary names and deed numbers
        body = re.sub(rb"<ms:(DOK_TWORCA|DOK_OZNACZENIE)>[^<]*</ms:\1>", rb"<ms:\1>REDACTED</ms:\1>", body)
    if b"accountNumbers" in body:
        body = json.dumps(_without_accounts(json.loads(body)), ensure_ascii=False).encode()
    if b"ZalogujResult" in body:  # REGON: the session id of the login call
        body = re.sub(rb"<ZalogujResult>[^<]*</ZalogujResult>", rb"<ZalogujResult>REDACTED</ZalogujResult>", body)
    if b"access_token" in body:
        payload = json.loads(body)
        for key in ("access_token", "refresh_token", "id_token"):
            if key in payload:
                payload[key] = SCRUBBED
        body = json.dumps(payload).encode()
    response["body"]["string"] = body
    return response


def _scrub_request(request: Any) -> Any:
    """Header filters miss the REGON session header, so drop it here (requests are matched without headers)."""
    for name in list(getattr(request, "headers", {})):
        if name.lower() == "sid":
            del request.headers[name]
    return request


_FLOAT = re.compile(r"-?\d+\.\d+")


def _stable(text: str | bytes | None) -> str:
    """Numbers rounded to 9 decimals: pyproj and libm differ in the last digits between macOS and Linux."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return _FLOAT.sub(lambda m: f"{float(m.group()):.9f}", unquote_plus(text or ""))


def _same_query(r1: Any, r2: Any) -> None:
    assert _stable(str(sorted(r1.query))) == _stable(str(sorted(r2.query)))


def _same_body(r1: Any, r2: Any) -> None:
    assert _stable(r1.body) == _stable(r2.body)


def pytest_recording_configure(config: pytest.Config, vcr: Any) -> None:
    vcr.register_matcher("stable_query", _same_query)
    vcr.register_matcher("stable_body", _same_body)


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return {
        "filter_headers": ["authorization", "private-token", "x-clientid", "sid", "cookie", "set-cookie", "user-agent"],
        "filter_post_data_parameters": [("client_id", SCRUBBED), ("client_secret", SCRUBBED)],
        "before_record_response": _scrub_response,
        "before_record_request": _scrub_request,
        "match_on": ["method", "scheme", "host", "path", "stable_query", "stable_body"],
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
