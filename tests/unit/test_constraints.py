"""Flood-zone layer selection: only the flood extent is counted, not rivers, embankments, labels or sheet frames."""

import pytest
from shapely.geometry.base import BaseGeometry

from geomcp_core.areas import area_from_id, geometry_of
from geomcp_core.constraints import flood_layers
from geomcp_core.models import Area, GeomcpError


def _layer(name: str, title: str, layer_id: str) -> str:
    return (
        f'<Layer queryable="1">\n<Name>{name}</Name>\n<Title><![CDATA[{title}]]></Title>\n'
        f"<Abstract><![CDATA[[id={layer_id}]]]></Abstract>\n<CRS>CRS:84</CRS>\n</Layer>\n"
    )


# the 1% service as GetCapabilities lists it: a sea block (1-14) and a river block (16-25)
CAPABILITIES = (
    "<Layer><Title><![CDATA[Warstwy]]></Title>"
    + "".join(
        [
            _layer("1", "głębokość wody w [m]", "mzp.glebokosc_1_M_g_1_m"),
            _layer("2", "obszar szczególnego zagrożenia powodzią", "mzp.obszar_zagrozenia_pow_morze_1_M_g_1_m"),
            _layer("3", "cieki naturalne i kanały", "ref.cieki_kanaly_g_1_m"),
            _layer("4", "wody powierzchniowe", "ref.wody_powierzchniowe_g_1_m"),
            _layer("5", "wał przeciwpowodziowy, przeciwsztormowy", "mzp.waly_przeciwpowodziowe_g_1_m"),
            _layer("11", "kilometraż rzeki", "mzp.kilometraz_g_1_m"),
            _layer("14", "skorowidz arkuszy", "ref.ramka_arkusza_m_1_r"),
            _layer("16", "głębokość wody w [m]", "mzp.glebokosc_1_g_1_r"),
            _layer("17", "obszar szczególnego zagrożenia powodzią", "mzp.obszar_zagrozenia_pow_rzeki_1_g_1_r"),
            _layer("19", "wody powierzchniowe", "ref.wody_powierzchniowe_g_1_r"),
            _layer("25", "skorowidz arkuszy", "ref.ramka_arkusza_g_1_r"),
        ]
    )
    + "</Layer>"
)


def test_only_depth_and_hazard_zone_layers_are_drawn() -> None:
    assert flood_layers(CAPABILITIES) == "1,2,16,17"


def test_embankment_failure_service_counts_from_layer_zero() -> None:
    xml = (
        _layer("0", "głębokość wody w [m]", "mzp.glebokosc_WZ_g_zwp_r")
        + _layer("1", "obszar zagrożenia powodziowego", "mzp.obszar_zagrozenia_pow_rzeki_WZ_g_zwp_r")
        + _layer("7", "skorowidz arkuszy", "ref.ramka_arkusza_g_zwp_r")
    )
    assert flood_layers(xml) == "0,1"


def test_a_service_without_flood_layers_is_an_error_not_a_zero_share() -> None:
    with pytest.raises(GeomcpError) as e:
        flood_layers(_layer("4", "wody powierzchniowe", "ref.wody_powierzchniowe_g_1_m"))
    assert e.value.code == "source_error"


def _area() -> tuple[Area, BaseGeometry]:
    area = area_from_id("a_pt_51.4050_16.2000_ci500")
    return area, geometry_of(area)


def test_a_provider_error_is_a_gap_not_an_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    """ArcGIS answers HTTP 200 with {"error": ...}; reading that as "no landslides here" would be a wrong number."""
    from geomcp_core import constraints

    def fake_json(url: str, params: dict[str, object], post: bool = False) -> object:
        if "sopo" in url:
            raise GeomcpError("source_error", "cbdgmapa.pgi.gov.pl: Requested operation is not supported.")
        if "egms" in url:
            return {"statistics": [{"count": 10, "mean": -2.5, "median": -2.4, "min": -5.0, "max": 0.1}]}
        return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(constraints, "_json", fake_json)
    area, geom = _area()
    items, gaps = constraints._pig(area, geom)  # pyright: ignore[reportPrivateUsage]
    assert {i.key for i in items} == {"mining_terrain", "mining_area", "mineral_deposit", "ground_motion_vertical"}
    assert len(gaps) == 2 and all("not supported" in g for g in gaps)


def test_a_truncated_feature_list_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from geomcp_core import constraints

    full = {"type": "FeatureCollection", "features": [{"geometry": None}] * constraints.GDOS_COUNT}
    monkeypatch.setattr(constraints, "_json", lambda url, params, post=False: full)
    area, geom = _area()
    items, gaps = constraints._gdos(area, geom)  # pyright: ignore[reportPrivateUsage]
    assert not items and len(gaps) == len(constraints.PROTECTED) and "more features" in gaps[0]
    with pytest.raises(GeomcpError):
        constraints._features({"features": [], "exceededTransferLimit": True}, "x")  # pyright: ignore[reportPrivateUsage]
