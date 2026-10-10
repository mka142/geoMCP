"""Sentinel Hub statistics: the cloud-free share is independent of the polygon's shape."""

from datetime import date
from typing import Any

import pytest

from geomcp_core.adapters import cdse
from geomcp_core.adapters.sentinel_hub import Sentinel2Adapter, bbox_fill
from geomcp_core.areas import area_from_id, geometry_of, project
from geomcp_core.periods import period_grid

CIRCLE = "a_pt_51.0500_16.9000_ci700"
SQUARE = "a_pt_51.0500_16.9000_sq700"
JULY = period_grid(date(2024, 7, 1), date(2024, 8, 1), "P1M")


def _payload(sample: int, nodata: int) -> dict[str, Any]:
    stats = {"sampleCount": sample, "noDataCount": nodata, "mean": 0.5, "percentiles": {"10.0": 0.2, "90.0": 0.8}}
    bands = {f"B{i}": {"stats": stats} for i in range(6)}
    span = {"from": "2024-07-10T00:00:00Z", "to": "2024-07-11T00:00:00Z"}
    return {"status": "OK", "data": [{"interval": span, "outputs": {"idx": {"bands": bands}}}]}


def test_bbox_fill_follows_the_shape() -> None:
    square = project(geometry_of(area_from_id(SQUARE)), "EPSG:4326", "EPSG:32633")
    circle = project(geometry_of(area_from_id(CIRCLE)), "EPSG:4326", "EPSG:32633")
    assert bbox_fill(square) == pytest.approx(0.95, abs=0.01)  # the UTM grid is rotated 1.5 deg against north here
    assert bbox_fill(circle) == pytest.approx(0.785, abs=0.01)


def test_pixels_outside_the_polygon_do_not_count_as_clouds(monkeypatch: pytest.MonkeyPatch) -> None:
    # a cloud-free day over a circle: Sentinel Hub reports the 21.5% of the bounding box outside it as no-data
    monkeypatch.setattr(cdse, "statistics", lambda body: (_payload(10_000, 2_150), 1.0))
    (obs,), _, _ = Sentinel2Adapter().observations(area_from_id(CIRCLE), "ndvi", JULY)
    assert obs.valid_fraction == 1.0
    # half the circle under cloud: the share says so
    monkeypatch.setattr(cdse, "statistics", lambda body: (_payload(10_000, 6_075), 1.0))
    (obs,), _, _ = Sentinel2Adapter().observations(area_from_id("a_pt_51.0600_16.9000_ci700"), "ndvi", JULY)
    assert obs.valid_fraction == pytest.approx(0.5, abs=0.02)

