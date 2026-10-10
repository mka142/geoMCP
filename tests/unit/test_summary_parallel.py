"""get_summary asks every source at once, so a profile with a dozen sources takes as long as the slowest one."""

import time
from datetime import date
from typing import ClassVar

import pytest

from geomcp_core import registry, service
from geomcp_core.models import Area, Cost, Fact, Health, Period, Provenance, SeriesData, SourceInfo, VariableInfo
from tests.conftest import GOLDEN_AREA_ID

SLEEP_S = 0.4


class _Slow:
    info: ClassVar[SourceInfo]

    @classmethod
    def enabled(cls) -> bool:
        return True

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        raise NotImplementedError

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        time.sleep(SLEEP_S)
        prov = Provenance(
            source=self.info.name, backend="test", collection="test", method="sleep", licence="n/a",
            attribution="n/a", commercial_safe=True, retrieved_at=date(2026, 1, 1).isoformat(),  # type: ignore[arg-type]
        )  # fmt: skip
        return [Fact(variable=v, units="1", value=1.0, provenance=prov) for v in variables], Cost()


def _source(name: str) -> _Slow:
    cls = type(name, (_Slow,), {})
    variable = VariableInfo(name=f"{name}_var", units="1", topic="test", description="", static=True)
    cls.info = SourceInfo(
        id=name, name=name, variables=[variable], access_pattern="server_stats", resolution="n/a",
        coverage=(date(2020, 1, 1), None), latency="P0D", licence="n/a", attribution="n/a", commercial_safe=True,
    )  # fmt: skip
    return cls()


def test_static_sources_are_queried_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = tuple(_source(f"slow{i}") for i in range(4))
    monkeypatch.setattr(registry, "adapters", lambda: sources)
    t0 = time.monotonic()
    r = service.get_summary(GOLDEN_AREA_ID, [f"slow{i}_var" for i in range(4)])
    elapsed = time.monotonic() - t0
    assert [f.variable for f in r.facts] == [f"slow{i}_var" for i in range(4)] and not r.gaps
    assert elapsed < 2 * SLEEP_S, f"four sources took {elapsed:.2f} s: they ran one after another"
