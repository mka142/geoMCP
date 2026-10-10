"""GUS REGON (BIR 1.1) SOAP client: the national business register, including sole traders.

Needs GEOMCP_REGON_KEY (free, requested from GUS by e-mail). GEOMCP_REGON_URL points at the test service
(with GUS's public test key abcde12345abcde12345) for development; data there is frozen in 2018.
SOAP 1.2 with WS-Addressing; answers are MTOM multipart with the payload as escaped XML inside *Result.
REGON has no search by name: only NIP, REGON or KRS numbers.
"""

import html
import re
import threading
import xml.etree.ElementTree as ET
from typing import Any

from geomcp_core import http
from geomcp_core.config import env
from geomcp_core.models import GeomcpError

PRODUCTION = "https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc"
NS = (
    'xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:ns="http://CIS/BIR/PUBL/2014/07" '
    'xmlns:dat="http://CIS/BIR/PUBL/2014/07/DataContract"'
)
SEARCH_FIELDS = {"nip": "Nip", "regon": "Regon", "krs": "Krs"}
PKD_REPORTS = {"P": "BIR11OsPrawnaPkd", "LP": "BIR11OsPrawnaPkd", "F": "BIR11OsFizycznaPkd", "LF": "BIR11OsFizycznaPkd"}
_lock = threading.Lock()
_sid: str | None = None


def configured() -> bool:
    return bool(env("GEOMCP_REGON_KEY"))


def _url() -> str:
    return env("GEOMCP_REGON_URL") or PRODUCTION


def _call(action: str, body: str, sid: str | None = None) -> str:
    url = _url()
    envelope = (
        f'<soap:Envelope {NS}><soap:Header xmlns:wsa="http://www.w3.org/2005/08/addressing"><wsa:To>{url}</wsa:To>'
        f"<wsa:Action>http://CIS/BIR/PUBL/2014/07/IUslugaBIRzewnPubl/{action}</wsa:Action></soap:Header>"
        f"<soap:Body>{body}</soap:Body></soap:Envelope>"
    )
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"} | ({"sid": sid} if sid else {})
    with http.client(30) as c:
        text = http.with_retries(lambda: http.check(c.post(url, content=envelope.encode(), headers=headers)).text)
    m = re.search(r"<(\w+Result)>(.*?)</\1>", text, re.S)
    return html.unescape(m.group(2)) if m else ""


def _session() -> str:
    global _sid
    with _lock:
        if _sid is None:
            key = env("GEOMCP_REGON_KEY")
            if not key:
                raise GeomcpError("source_unconfigured", "REGON is not configured.", "Set GEOMCP_REGON_KEY.")
            _sid = _call("Zaloguj", f"<ns:Zaloguj><ns:pKluczUzytkownika>{key}</ns:pKluczUzytkownika></ns:Zaloguj>")
            if not _sid:
                _sid = None
                raise GeomcpError("source_forbidden", "REGON refused the key.", "Check GEOMCP_REGON_KEY.")
        return _sid


def _rows(payload: str) -> list[dict[str, str]]:
    if not payload.strip():
        return []
    root = ET.fromstring(payload)
    return [{child.tag: (child.text or "").strip() for child in dane} for dane in root.iter("dane")]


def _query(action: str, body: str) -> list[dict[str, str]]:
    """One call with the session; a session that expired (60 minutes) is renewed once."""
    global _sid
    for attempt in range(2):
        rows = _rows(_call(action, body, _session()))
        if rows and rows[0].get("ErrorCode") == "4":  # "not found"
            return []
        if rows and rows[0].get("ErrorCode") and attempt == 0:  # most other codes mean the session ended
            with _lock:
                _sid = None
            continue
        return rows
    return []


def search(kind: str, value: str) -> dict[str, str] | None:
    """The register entry for a NIP, REGON or KRS number, or None."""
    field = SEARCH_FIELDS[kind]
    body = (
        "<ns:DaneSzukajPodmioty><ns:pParametryWyszukiwania>"
        f"<dat:{field}>{value}</dat:{field}></ns:pParametryWyszukiwania></ns:DaneSzukajPodmioty>"
    )
    rows = _query("DaneSzukajPodmioty", body)
    return rows[0] if rows else None


def activities(entry: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """(PKD code, description, is main) for a register entry from `search`."""
    report = PKD_REPORTS.get(entry.get("Typ", ""))
    if not report:
        return []
    body = (
        f"<ns:DanePobierzPelnyRaport><ns:pRegon>{entry['Regon']}</ns:pRegon>"
        f"<ns:pNazwaRaportu>{report}</ns:pNazwaRaportu></ns:DanePobierzPelnyRaport>"
    )
    out: list[tuple[str, str, bool]] = []
    for row in _query("DanePobierzPelnyRaport", body):
        code = row.get("praw_pkdKod") or row.get("fiz_pkd_Kod") or ""
        name = row.get("praw_pkdNazwa") or row.get("fiz_pkd_Nazwa") or ""
        main = (row.get("praw_pkdPrzewazajace") or row.get("fiz_pkd_Przewazajace")) == "1"
        if code:
            out.append((f"{code[:2]}.{code[2:4]}.{code[4:]}", name.capitalize(), main))
    return out
