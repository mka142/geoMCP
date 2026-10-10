"""Quota handling for GUS BDL and derived climate-extreme counts."""

import httpx
import pytest

from geomcp_core.adapters import cds, gus_bdl
from geomcp_core.models import GeomcpError


def _response(status: int, retry_after: str) -> httpx.Response:
    request = httpx.Request("GET", "https://bdl.stat.gov.pl/api/v1/subjects")
    return httpx.Response(status, headers={"Retry-After": retry_after}, json={}, request=request)


def test_a_long_bdl_quota_block_is_reported_without_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOMCP_GUS_BDL_KEY", raising=False)
    with pytest.raises(GeomcpError) as e:
        gus_bdl._checked(_response(429, "3651 sek"))  # pyright: ignore[reportPrivateUsage]
    assert (
        e.value.code == "source_busy" and "61 min" in e.value.message and "GEOMCP_GUS_BDL_KEY" in (e.value.hint or "")
    )


def test_a_short_bdl_limit_is_left_to_the_retry_loop() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        gus_bdl._checked(_response(429, "0 sek"))  # pyright: ignore[reportPrivateUsage]


def test_day_counts_use_inclusive_thresholds() -> None:
    _, hot, _ = cds.DAY_COUNTS["hot_days"]
    _, frost, _ = cds.DAY_COUNTS["frost_days"]
    assert hot(30.0) and not hot(29.9) and frost(-0.1) and not frost(0.0)
