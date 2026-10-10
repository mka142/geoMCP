from geomcp_core.adapters.worldcover import _tiles  # pyright: ignore[reportPrivateUsage]


def test_tiles_are_named_by_their_south_west_corner() -> None:
    assert _tiles((17.0, 51.09, 17.07, 51.13)) == ["N51E015"]
    assert _tiles((14.9, 50.9, 15.1, 51.1)) == ["N48E012", "N48E015", "N51E012", "N51E015"]
    assert _tiles((-0.5, -0.5, 0.5, 0.5)) == ["S03W003", "S03E000", "N00W003", "N00E000"]
