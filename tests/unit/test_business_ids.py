"""NIP and KRS numbers both have 10 digits; the NIP checksum tells them apart."""

import pytest

from geomcp_core import business
from geomcp_core.models import GeomcpError


@pytest.mark.parametrize(
    ("identifier", "kind", "value"),
    [
        ("5250000251", "nip", "5250000251"),  # PKP S.A.: valid checksum
        ("NIP 525-000-02-51", "nip", "5250000251"),
        ("0000019193", "krs", "0000019193"),
        ("KRS 19193", "krs", "0000019193"),
        ("000126801", "regon", "000126801"),
    ],
)
def test_identifier_kinds(identifier: str, kind: str, value: str) -> None:
    assert business._kind(identifier) == (kind, value)  # pyright: ignore[reportPrivateUsage]


def test_a_name_is_refused_with_a_hint() -> None:
    with pytest.raises(GeomcpError) as e:
        business._kind("Polskie Koleje Państwowe")  # pyright: ignore[reportPrivateUsage]
    assert "NIP" in e.value.hint
