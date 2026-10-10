"""GPR survey parsing: the 2020/21 comparison follows GDDKiA's mapping, and only national roads are national."""

import pytest

from geomcp_core import traffic

SECTION = {
    "Numer_odcinka_pomiarowego": "", "Porównanie_z_2020": "", "Miejscowość": "Kąty Wrocławskie",
    "Szer._geo_punktu_prawego": "51,02", "Dł._geo_punktu_prawego": "16,77",
    "Szer._geo_punktu_lewego": "", "Dł._geo_punktu_lewego": "",
}  # fmt: skip
COUNT = {"numer_drogi_kraj": "A4", "nazwa": "", "pikietaz_pocz": "0", "pikietaz_kon": "1", "sdrr2025": "1000"} | {
    f"sdrr_p{i}": "0" for i in range(1, 9)
}


def _csv(url: str, encoding: str) -> list[dict[str, str]]:
    if url == traffic.SDRR_2020:  # 91501 was a DK1 section in 2020/21; in 2025 the number names an A1 section
        return [
            {"NR2020": "30202", "NRDROGI": "A4", "NAZWA_ODC": "KOSTOMŁOTY - KĄTY WR.", "SDRR2020": "59432"},
            {"NR2020": "91501", "NRDROGI": "1", "NAZWA_ODC": "PIOTRKÓW - KAMIEŃSK", "SDRR2020": "12000"},
        ]
    if url == traffic.SECTIONS_2025:
        return [
            SECTION | {"Numer_odcinka_pomiarowego": "30202", "Porównanie_z_2020": "30202"},
            SECTION | {"Numer_odcinka_pomiarowego": "91501"},  # not comparable with 2020/21
        ]
    return [
        COUNT | {"nr_odcinka": "30202", "nazwa": "W. KOSTOMŁOTY - W. KĄTY WR."},
        COUNT | {"nr_odcinka": "91501", "numer_drogi_kraj": "A1", "nazwa": "W. PIOTRKÓW PŁD. - W. KAMIEŃSK"},
    ]


def test_2020_counts_follow_the_comparison_column_not_the_section_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(traffic, "_csv", _csv)
    rows = {r["section"]: r for r in traffic._survey()}  # pyright: ignore[reportPrivateUsage]
    assert rows["30202"]["total_2020"] == 59432
    assert rows["91501"]["total_2020"] is None  # a 2020/21 count of another road must not become a trend


@pytest.mark.parametrize(
    ("ref", "national"),
    [("A4", True), ("S8", True), ("35", True), ("DK 94", True), ("347", False), ("DW347", False), ("E40", False)],
)
def test_only_national_road_numbers_count(ref: str, national: bool) -> None:
    assert bool(traffic.NATIONAL.match(ref.upper())) is national
