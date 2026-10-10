"""CEIDG (Central Register of Business Activity): sole traders, searchable by name, gmina, town and PKD.

Needs GEOMCP_CEIDG_TOKEN (free, from biznes.gov.pl). Limits: 50 requests per 3 minutes and 1,000 per hour;
going over locks the token out for 3 minutes, so calls are paced 3.7 s apart in this process.
A search answers with `count` (all matches) plus one page, so counting businesses costs one call.
Covers sole traders only; companies are in KRS (lookup_business), and all businesses per gmina in GUS.
"""

import threading
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from geomcp_core import cache, http
from geomcp_core.config import env
from geomcp_core.models import GeomcpError

API = "https://dane.biznes.gov.pl/api/ceidg/v3"
MIN_INTERVAL_S = 3.7
TTL_S = 24 * 3600
PAGE = 5
_lock = threading.Lock()
_last = 0.0


def configured() -> bool:
    return bool(env("GEOMCP_CEIDG_TOKEN"))


def _get(path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """GET with pacing; None for 204 (no data)."""
    global _last
    token = env("GEOMCP_CEIDG_TOKEN")
    if not token:
        raise GeomcpError("source_unconfigured", "CEIDG is not configured.", "Set GEOMCP_CEIDG_TOKEN.")
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            with http.client(120) as c:  # PKD-filtered searches take up to 30 s
                r = http.with_retries(
                    lambda: http.raise_transient(
                        c.get(f"{API}/{path}", params=params, headers={"Authorization": f"Bearer {token}"})
                    ),
                    attempts=2,
                )
        finally:
            _last = time.monotonic()
    if r.status_code == 204:
        return None
    if r.status_code in (401, 403):
        raise GeomcpError("source_forbidden", "CEIDG refused the token.", "Check GEOMCP_CEIDG_TOKEN.")
    if "json" not in r.headers.get("content-type", ""):  # a slow query ends in an HTML page with status 200
        raise GeomcpError("source_busy", "CEIDG gave up on this query (it took too long).", "Narrow it down.")
    return http.json_of(r)


def _pkd(item: dict[str, Any] | None) -> str | None:
    if not item or not item.get("kod"):
        return None
    k = str(item["kod"])
    return f"{k[:2]}.{k[2:4]}.{k[4:]} {item.get('nazwa', '')}".strip()


def _address(a: dict[str, Any]) -> str:
    street = " ".join(x for x in (a.get("ulica"), a.get("budynek")) if x)
    if a.get("lokal"):
        street += f"/{a['lokal']}"
    return ", ".join(x for x in (street, " ".join(x for x in (a.get("kod"), a.get("miasto")) if x)) if x)


class SoleTrader(BaseModel):
    name: str
    nip: str | None
    regon: str | None
    status: str
    started: str | None
    address: str
    gmina: str | None
    terc: str | None


class BusinessSearch(BaseModel):
    count: int = Field(description="All matching sole traders (not only the ones listed)")
    listed: list[SoleTrader] = Field(description=f"Up to {PAGE} matches")
    filters: dict[str, Any]
    notices: list[str]
    source: str = "CEIDG (Ministry of Development), via dane.biznes.gov.pl"
    retrieved_at: datetime


def _trader(f: dict[str, Any]) -> SoleTrader:
    a: dict[str, Any] = f.get("adresDzialalnosci") or {}
    owner: dict[str, Any] = f.get("wlasciciel") or {}
    return SoleTrader(
        name=f.get("nazwa", ""),
        nip=owner.get("nip"),
        regon=owner.get("regon"),
        status=f.get("status", ""),
        started=f.get("dataRozpoczecia"),
        address=_address(a),
        gmina=a.get("gmina"),
        terc=a.get("terc"),
    )


def _params(
    name: str | None, gmina: str | None, town: str | None, pkd: str | None, active_only: bool
) -> tuple[dict[str, Any], str]:
    """Search parameters and their cache key."""
    params: dict[str, Any] = {"limit": PAGE}
    if name:
        params["nazwa"] = name
    if gmina:
        params["gmina"] = gmina
    if town:
        params["miasto"] = town
    if pkd:
        params["pkd"] = pkd.replace(".", "").upper()
    if active_only:
        params["status"] = "AKTYWNY"
    if len(params) == 1 + active_only:
        raise GeomcpError("invalid_request", "Give a name, an area or a PKD code to search for.")
    return params, "search|" + "|".join(f"{k}={v}" for k, v in sorted(params.items()))


def estimate_s(
    name: str | None = None, gmina: str | None = None, town: str | None = None, pkd: str | None = None,
    active_only: bool = True,
) -> float:  # fmt: skip
    _, key = _params(name, gmina, town, pkd, active_only)
    if cache.get("ceidg", key):
        return 1.0
    return 60.0 if (pkd or name) else 8.0  # CEIDG takes up to 90 s on name and PKD searches


def search(
    name: str | None = None, gmina: str | None = None, town: str | None = None, pkd: str | None = None,
    active_only: bool = True,
) -> BusinessSearch:  # fmt: skip
    params, key = _params(name, gmina, town, pkd, active_only)
    notices = ["Sole traders only (CEIDG); companies are in KRS and are not counted here."]
    if pkd:
        notices.append("A PKD code matches any activity a trader declared, not only the main one.")
    hit: dict[str, Any] = cache.get("ceidg", key) or {}
    if not hit:
        # searches are slow on CEIDG's side and time out with a full page (a PKD search: 90 s for 20 rows,
        # 30 s for 1): ask for a short page, and fall back to the count alone
        sizes = (PAGE, 1)
        for i, size in enumerate(sizes):
            try:
                hit = _get("firmy", params | {"limit": size}) or {"count": 0, "firmy": []}
                break
            except GeomcpError as e:
                if e.code != "source_busy" or i == len(sizes) - 1:
                    raise
        cache.put("ceidg", key, hit, TTL_S)
    return BusinessSearch(
        count=int(hit.get("count") or 0),
        listed=[_trader(f) for f in hit.get("firmy", [])],
        filters={k: v for k, v in params.items() if k != "limit"},
        notices=notices,
        retrieved_at=datetime.now(UTC),
    )


def by_nip(nip: str) -> dict[str, Any] | None:
    """Full record of a sole trader (with all PKD codes) by NIP, or None."""
    key = f"firma|{nip}"
    if (hit := cache.get("ceidg", key)) is not None:
        return hit or None
    found = _get("firmy", {"nip": nip, "limit": 1})
    firm_id = (found or {}).get("firmy", [{}])[0].get("id") if found else None
    detail = _get(f"firma/{firm_id}", {}) if firm_id else None
    record = (detail.get("firma") or [detail])[0] if detail else {}
    cache.put("ceidg", key, record, TTL_S)
    return record or None


def describe(record: dict[str, Any]) -> dict[str, Any]:
    """The fields lookup_business uses from a full CEIDG record."""
    a: dict[str, Any] = record.get("adresDzialalnosci") or {}
    codes: list[dict[str, Any]] = record.get("pkd") or []
    main = _pkd(record.get("pkdGlowny"))
    return {
        "name": record.get("nazwa", ""),
        "address": _address(a),
        "seat": {k: str(v).title() for k, v in (("wojewodztwo", a.get("wojewodztwo")), ("powiat", a.get("powiat")),
                 ("gmina", a.get("gmina")), ("miejscowosc", a.get("miasto"))) if v},
        "main": main,
        "others": [p for p in (_pkd(x) for x in codes) if p and p != main][:10],
        "status": record.get("status"),
        "started": record.get("dataRozpoczecia"),
    }  # fmt: skip
