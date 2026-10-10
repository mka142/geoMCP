import pytest

from geomcp_core.areas import area_from_id, resolve_area
from geomcp_core.models import GeomcpError
from tests.conftest import GOLDEN_AREA_ID, GOLDEN_QUERY


def test_golden_square_is_ten_square_kilometres() -> None:
    (area,), selected = resolve_area(GOLDEN_QUERY, buffer_m=1581, shape_="square")
    assert area.area_id == selected == GOLDEN_AREA_ID
    assert abs(area.area_km2 - 10.0) < 0.05
    minx, miny, maxx, maxy = area.bbox
    assert minx < 17.032 < maxx and miny < 51.110 < maxy


def test_circle_area_matches_pi_r_squared() -> None:
    (area,), _ = resolve_area("51.110,17.032", buffer_m=1000)
    assert abs(area.area_km2 - 3.1416) < 0.02  # 64-gon approximation of the circle


def test_area_id_round_trips() -> None:
    (area,), _ = resolve_area(GOLDEN_QUERY, buffer_m=1581, shape_="square")
    assert area_from_id(area.area_id) == area


@pytest.mark.parametrize("query", ["91,17", "51.1,190"])
def test_invalid_coordinates_raise(query: str) -> None:
    with pytest.raises(GeomcpError):
        resolve_area(query)


def test_unknown_area_id_raises() -> None:
    with pytest.raises(GeomcpError) as e:
        area_from_id("a_nope_0223043")
    assert e.value.code == "unknown_area"
