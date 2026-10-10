"""Copernicus Data Space Ecosystem: Sentinel Hub APIs with OAuth client credentials."""

import threading
import time
from datetime import date
from typing import Any

import httpx

from geomcp_core import http
from geomcp_core.config import env
from geomcp_core.models import GeomcpError

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_URL = "https://sh.dataspace.copernicus.eu"
ID_VAR, SECRET_VAR = "GEOMCP_CDSE_CLIENT_ID", "GEOMCP_CDSE_CLIENT_SECRET"
MAX_PARALLEL = 10  # measured: 10 parallel yearly statistics requests run cleanly on the free tier

_token_lock = threading.Lock()
_token: tuple[str, float] | None = None
_slots = threading.BoundedSemaphore(MAX_PARALLEL)


def configured() -> bool:
    return bool(env(ID_VAR) and env(SECRET_VAR))


def unconfigured_hint() -> str:
    return f"Set {ID_VAR} and {SECRET_VAR} (a CDSE Sentinel Hub OAuth client); run `geomcp setup`."


def _access_token(force: bool = False) -> str:
    global _token
    with _token_lock:
        if not force and _token and _token[1] > time.time() + 60:
            return _token[0]
        if not configured():
            raise GeomcpError("source_unconfigured", "Copernicus Data Space is not configured.", unconfigured_hint())
        data = {
            "grant_type": "client_credentials",
            "client_id": env(ID_VAR),
            "client_secret": env(SECRET_VAR),
        }
        with http.client(30) as c:
            r = http.with_retries(lambda: http.raise_transient(c.post(TOKEN_URL, data=data)))
        if r.status_code in (400, 401):
            raise GeomcpError("source_auth_failed", "CDSE rejected the OAuth client credentials.", unconfigured_hint())
        payload = http.json_of(r)
        _token = (payload["access_token"], time.time() + float(payload.get("expires_in", 300)))
        return _token[0]


def _post(path: str, body: dict[str, Any], accept: str = "application/json") -> httpx.Response:
    def call(token: str) -> httpx.Response:
        with http.client(600) as c:
            headers = {"Authorization": f"Bearer {token}", "Accept": accept}
            return http.with_retries(lambda: http.check(c.post(f"{SH_URL}{path}", json=body, headers=headers)))

    with _slots:
        try:
            return call(_access_token())
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:  # token revoked or clock skew: refresh once
                return call(_access_token(force=True))
            if e.response.status_code == 403:
                raise GeomcpError(
                    "source_forbidden", f"CDSE denied {path}: {e.response.text[:200]}", unconfigured_hint()
                ) from e
            raise


def units_spent(r: httpx.Response) -> float:
    return float(r.headers.get("x-processingunits-spent", 0) or 0)


def statistics(body: dict[str, Any]) -> tuple[dict[str, Any], float]:
    r = _post("/api/v1/statistics", body)
    return r.json(), units_spent(r)


def process(body: dict[str, Any], accept: str) -> tuple[bytes, float]:
    r = _post("/api/v1/process", body, accept)
    return r.content, units_spent(r)


def catalog_search(collection: str, bbox: tuple[float, float, float, float], start: date, end: date) -> list[Any]:
    """Scenes intersecting bbox in [start, end) (UTC days)."""
    span = f"{start}T00:00:00Z/{end}T00:00:00Z"
    body = {"collections": [collection], "bbox": list(bbox), "datetime": span, "limit": 100}
    features: list[Any] = _post("/api/v1/catalog/1.0.0/search", body, "application/geo+json").json().get("features", [])
    return features
