"""Company lookup: the National Court Register (KRS) open API, with the Ministry of Finance VAT whitelist to turn
a NIP or REGON into a KRS number and add the VAT status. Neither needs a key.

KRS masks the names of natural persons (board members appear as "B*****"), so boards are reported by role.
Bank account numbers from the whitelist are not kept.
With GEOMCP_REGON_KEY, the GUS REGON register adds sole traders and non-VAT payers; no source offers a
free search by company name for companies (REGON and KRS look up numbers only).
"""

import re
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from geomcp_core import cache, ceidg, http, regon
from geomcp_core.models import GeomcpError

KRS = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}"
WHITELIST = "https://wl-api.mf.gov.pl/api/search/{kind}/{value}"
TTL_S = 24 * 3600
Kind = Literal["krs", "nip", "regon"]


class Business(BaseModel):
    name: str
    legal_form: str | None
    krs: str | None
    nip: str | None
    regon: str | None
    vat_status: str | None = Field(description="Czynny (active), Zwolniony (exempt) or None when not a VAT payer")
    seat: dict[str, str] = Field(default_factory=dict, description="voivodeship, powiat, gmina, town")
    address: str | None = Field(description="Registered address; pass it to resolve_area to map the company")
    website: str | None = None
    registered_on: str | None = None
    last_entry_on: str | None = None
    share_capital_pln: float | None = None
    main_activity: str | None = Field(description="Main PKD activity: code and description")
    other_activities: list[str] = Field(default_factory=list)
    representation: str | None = Field(description="Management body and how it signs for the company")
    board_roles: list[str] = Field(default_factory=list, description="Board functions (names are masked in KRS)")
    owners: list[str] = Field(default_factory=list, description="Shareholders that are companies or the State")
    warnings: list[str] = Field(
        default_factory=list, description="Arrears, curator, liquidation, bankruptcy or restructuring entries"
    )
    sources: list[str]
    retrieved_at: datetime


def _nip_ok(digits: str) -> bool:
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    return len(digits) == 10 and sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 == int(digits[9])


def _kind(identifier: str) -> tuple[Kind, str]:
    """Guess the identifier type: NIP and KRS both have 10 digits, but a NIP has a checksum and KRS numbers
    start with zeros."""
    text = identifier.strip().upper()
    digits = re.sub(r"\D", "", text)
    if not digits or re.search(r"[^\d\s./-]", re.sub(r"^(KRS|NIP|REGON)\s*:?", "", text)):
        raise GeomcpError(
            "invalid_request",
            f"{identifier!r} is not a KRS, NIP or REGON number.",
            hint="Pass a KRS, NIP or REGON number; to search sole traders by name use find_businesses.",
        )
    if text.startswith("KRS") or (len(digits) <= 10 and digits.startswith("0000")):
        return "krs", digits.zfill(10)
    if text.startswith("NIP") or _nip_ok(digits):
        return "nip", digits
    if text.startswith("REGON") or len(digits) in (9, 14):
        return "regon", digits
    if len(digits) <= 10:
        return "krs", digits.zfill(10)
    raise GeomcpError("invalid_request", f"{identifier!r} is not a KRS, NIP or REGON number.")


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _whitelist(kind: str, value: str) -> dict[str, Any] | None:
    today = _today()  # the whitelist answers as of a date
    with http.client(30) as c:
        r = http.with_retries(
            lambda: http.raise_transient(c.get(WHITELIST.format(kind=kind, value=value), params={"date": today}))
        )
    if r.status_code == 400:  # the whitelist answers 400 for unknown or malformed numbers
        return None
    return http.json_of(r).get("result", {}).get("subject")


def _krs(number: str) -> dict[str, Any] | None:
    with http.client(30) as c:
        for register in ("P", "S"):  # businesses, then associations and foundations
            r = http.with_retries(
                lambda reg=register: http.raise_transient(
                    c.get(KRS.format(krs=number), params={"rejestr": reg, "format": "json"})
                )
            )
            if r.status_code == 200:
                return http.json_of(r)["odpis"]
            if r.status_code != 404:
                http.check(r)
    return None


def _pkd(item: dict[str, Any]) -> str:
    code = f"{item.get('kodDzial', '')}.{item.get('kodKlasa', '')}.{item.get('kodPodklasa', '')}".strip(".")
    return f"{code} {item.get('opis', '').capitalize()}".strip()


def _amount(value: dict[str, Any] | None) -> float | None:
    try:
        return float(str((value or {}).get("wartosc", "")).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def lookup_business(identifier: str) -> Business:
    kind, value = _kind(identifier)
    key = f"business|v4|{kind}|{value}|{regon.configured()}|{ceidg.configured()}"
    if (hit := cache.get("business", key)) is not None:
        return Business.model_validate(hit)
    sources: list[str] = []
    vat: dict[str, Any] | None = None
    entry = _regon(kind, value)  # the national register also covers sole traders and non-VAT payers
    if entry:
        sources.append("REGON (GUS)")
    if kind in ("nip", "regon"):
        vat = _whitelist(kind, value)
        if vat is None and entry is None:
            raise GeomcpError("not_found", f"No VAT-register or REGON entry for {kind.upper()} {value}.")
        if vat is not None:
            sources.append("Wykaz podatników VAT (Ministry of Finance)")
        krs_number = (vat or {}).get("krs")
    else:
        krs_number = value
    odpis = _krs(krs_number) if krs_number else None
    if odpis is None and vat is None and entry is None:
        raise GeomcpError("not_found", f"No KRS entry {value}.", hint="Check the number, or pass a NIP instead.")
    if odpis is not None:
        sources.append("Krajowy Rejestr Sądowy (Ministry of Justice), current extract")
        nip = odpis["dane"]["dzial1"]["danePodmiotu"].get("identyfikatory", {}).get("nip")
        if vat is None and nip:
            try:
                vat = _whitelist("nip", nip)
                sources.append("Wykaz podatników VAT (Ministry of Finance)")
            except (GeomcpError, httpx.HTTPError):
                vat = None  # the VAT status is a bonus; the KRS extract stands on its own
    result = _assemble(odpis, vat, sources)
    if entry:
        _add_regon(result, entry)
    if odpis is None and (nip := result.nip or (value if kind == "nip" else None)):
        _add_ceidg(result, nip)
    cache.put("business", key, result.model_dump(mode="json"), TTL_S)
    return result


def _add_ceidg(b: Business, nip: str) -> None:
    """Sole traders: name, activities, status and start date from CEIDG."""
    if not ceidg.configured():
        return
    try:
        record = ceidg.by_nip(nip)
    except (GeomcpError, httpx.HTTPError):
        return
    if not record:
        return
    d = ceidg.describe(record)
    b.sources.append("CEIDG (sole traders register)")
    b.name = b.name or d["name"]
    b.legal_form = b.legal_form or "jednoosobowa działalność gospodarcza (sole trader)"
    b.address = b.address or d["address"] or None
    b.seat = b.seat or d["seat"]
    b.main_activity = b.main_activity or d["main"]
    b.other_activities = b.other_activities or d["others"]
    b.registered_on = b.registered_on or d["started"]
    if d["status"] and d["status"] != "AKTYWNY":
        b.warnings.append(
            {"ZAWIESZONY": "Activity suspended", "WYKRESLONY": "Removed from the register"}.get(
                d["status"], f"CEIDG status {d['status']}"
            )
        )


def _regon(kind: str, value: str) -> dict[str, str] | None:
    if not regon.configured():
        return None
    try:
        return regon.search(kind, value)
    except (GeomcpError, httpx.HTTPError):
        return None  # REGON enriches; the other registers answer on their own


def _add_regon(b: Business, entry: dict[str, str]) -> None:
    """Fill what KRS and the VAT register leave empty, notably for sole traders."""
    b.name = b.name or entry.get("Nazwa", "")
    b.regon = b.regon or entry.get("Regon") or None
    b.nip = b.nip or entry.get("Nip") or None
    if not b.seat:
        b.seat = {
            k: v
            for k, v in (
                ("wojewodztwo", entry.get("Wojewodztwo", "").title()),
                ("powiat", entry.get("Powiat", "")),
                ("gmina", entry.get("Gmina", "")),
                ("miejscowosc", entry.get("Miejscowosc", "")),
            )
            if v
        }
    if not b.address:
        street = " ".join(x for x in (entry.get("Ulica"), entry.get("NrNieruchomosci")) if x)
        b.address = ", ".join(
            x for x in (street, f"{entry.get('KodPocztowy', '')} {entry.get('Miejscowosc', '')}".strip()) if x
        )
    if not b.legal_form:
        b.legal_form = {"F": "osoba fizyczna (sole trader)", "LF": "local unit of a sole trader"}.get(
            entry.get("Typ", "")
        )
    if entry.get("DataZakonczeniaDzialalnosci"):
        b.warnings.append(f"Activity ended on {entry['DataZakonczeniaDzialalnosci']} (REGON)")
    if not b.main_activity:
        try:
            pkd = regon.activities(entry)
        except (GeomcpError, httpx.HTTPError):
            pkd = []
        main = [f"{c} {n}" for c, n, is_main in pkd if is_main]
        b.main_activity = main[0] if main else None
        b.other_activities = [f"{c} {n}" for c, n, is_main in pkd if not is_main][:10]


def _assemble(odpis: dict[str, Any] | None, vat: dict[str, Any] | None, sources: list[str]) -> Business:
    vat = vat or {}
    if odpis is None:  # a sole trader or partnership without KRS: the whitelist is all there is
        return Business(
            name=vat.get("name", ""), legal_form=None, krs=None, nip=vat.get("nip"), regon=vat.get("regon"),
            vat_status=vat.get("statusVat"), address=vat.get("workingAddress") or vat.get("residenceAddress"),
            registered_on=vat.get("registrationLegalDate"), main_activity=None, representation=None,
            sources=sources, retrieved_at=datetime.now(UTC),
        )  # fmt: skip
    head, d = odpis["naglowekA"], odpis["dane"]
    d1, d2, d3 = d.get("dzial1", {}), d.get("dzial2", {}), d.get("dzial3", {})
    entity = d1.get("danePodmiotu", {})
    ids = entity.get("identyfikatory", {})
    seat_raw = d1.get("siedzibaIAdres", {})
    a = seat_raw.get("adres", {})
    street = " ".join(x for x in (a.get("ulica"), a.get("nrDomu")) if x)
    if a.get("nrLokalu"):
        street += f"/{a['nrLokalu']}"
    address = ", ".join(
        x for x in (street, " ".join(x for x in (a.get("kodPocztowy"), a.get("miejscowosc")) if x)) if x
    )
    activity = d3.get("przedmiotDzialalnosci", {})
    main = activity.get("przedmiotPrzewazajacejDzialalnosci", [])
    board = d2.get("reprezentacja", {})
    owners = [
        o.get("nazwa", "") for o in d1.get("jedynyAkcjonariusz", []) + d1.get("wspolnicySpzoo", []) if o.get("nazwa")
    ]
    warnings: list[str] = []
    if d.get("dzial4"):
        warnings.append("Section 4 entries: arrears, debts or enforcement")
    if d.get("dzial5"):
        warnings.append("Section 5 entries: a curator was appointed")
    for k in d.get("dzial6", {}):
        if k == "polaczeniePodzialPrzeksztalcenie":  # a past merger or transformation is history, not a warning
            continue
        warnings.append(
            {
                "likwidacja": "Liquidation",
                "postepowanieUpadlosciowe": "Bankruptcy proceedings",
                "upadlosc": "Bankruptcy",
                "postepowanieRestrukturyzacyjne": "Restructuring proceedings",
            }.get(k, f"Section 6 entry: {k}")
        )
    return Business(
        name=entity.get("nazwa", ""),
        legal_form=entity.get("formaPrawna"),
        krs=head.get("numerKRS"),
        nip=ids.get("nip") or vat.get("nip"),
        regon=(ids.get("regon") or vat.get("regon") or "")[:9] or None,
        vat_status=vat.get("statusVat"),
        seat={k: v.title() for k, v in seat_raw.get("siedziba", {}).items() if k != "kraj" and v},
        address=address.title() or vat.get("workingAddress"),
        website=seat_raw.get("adresStronyInternetowej"),
        registered_on=head.get("dataRejestracjiWKRS"),
        last_entry_on=head.get("dataOstatniegoWpisu"),
        share_capital_pln=_amount(d1.get("kapital", {}).get("wysokoscKapitaluZakladowego")),
        main_activity=_pkd(main[0]) if main else None,
        other_activities=[_pkd(x) for x in activity.get("przedmiotPozostalejDzialalnosci", [])[:10]],
        representation=" - ".join(x for x in (board.get("nazwaOrganu"), board.get("sposobReprezentacji")) if x) or None,
        board_roles=[s.get("funkcjaWOrganie", "") for s in board.get("sklad", []) if s.get("funkcjaWOrganie")],
        owners=owners,
        warnings=warnings,
        sources=sources,
        retrieved_at=datetime.now(UTC),
    )
