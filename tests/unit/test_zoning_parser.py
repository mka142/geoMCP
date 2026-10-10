"""Zoning answers come as HTML tables whose fields differ per gmina."""

from geomcp_core import zoning

KRAKOW = """<h5>layer</h5><table><tr><th>OBJECTID</th><th>Oznaczenie</th><th>Nazwa MPZP</th><th>WWW</th>
<th>opis_oznac</th></tr><tr><td>1</td><td>MW/U.31</td><td>KAZIMIERZ</td><td>http://bip/1</td>
<td>Tereny zabudowy mieszkaniowo - usługowej</td></tr></table>"""
SMOLEC = """<table><tr><th>Symbolterenu</th><th>NAZWA</th><th>OPIS_TERENU</th><th>SYMBOL_PLANU</th></tr>
<tr><td>RP</td><td>Smolec</td><td>R - tereny rolne z zakazem zabudowy</td><td>SMO-2A</td></tr></table>"""


def _row(html: str) -> dict[str, str]:
    parser = zoning._Tables()  # pyright: ignore[reportPrivateUsage]
    parser.feed(html)
    (row,) = parser.rows
    return row


def test_known_fields_are_picked_across_schemas() -> None:
    k, s = _row(KRAKOW), _row(SMOLEC)
    pick = zoning._pick  # pyright: ignore[reportPrivateUsage]
    assert pick(k, zoning.FIELDS["symbol"]) == "MW/U.31" and pick(k, zoning.FIELDS["plan"]) == "KAZIMIERZ"
    assert pick(k, zoning.FIELDS["link"]) == "http://bip/1"
    assert pick(s, zoning.FIELDS["symbol"]) == "RP" and pick(s, zoning.FIELDS["plan"]) == "SMO-2A"  # not the precinct
    assert (pick(s, zoning.FIELDS["designation"]) or "").startswith("R - tereny rolne")
