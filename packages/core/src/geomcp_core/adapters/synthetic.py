"""Deterministic synthetic sources for demos and tests.

Enabled only when GEOMCP_ENABLE_SYNTHETIC is true, so they never appear in a production catalogue.
Values follow plausible Central European seasonal cycles with a small trend and seeded noise.
"""

import hashlib
import math
import os
import random
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar

from geomcp_core.models import (
    Area,
    Cost,
    Fact,
    Health,
    Period,
    PeriodValue,
    Provenance,
    SeriesData,
    SourceInfo,
    VariableInfo,
)

_PRELIMINARY_DAYS = 30


def _enabled() -> bool:
    return os.environ.get("GEOMCP_ENABLE_SYNTHETIC", "").lower() in {"1", "true", "yes"}


def _rng(*parts: object) -> random.Random:
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(seed[:8], "big"))


def _season(d: date, phase_doy: int) -> float:
    return math.sin(2 * math.pi * (d.timetuple().tm_yday - phase_doy) / 365.25)


def _period_season(p: Period, phase_doy: int) -> float:
    """Mean of the seasonal cycle over the whole period (weekly samples), not its midpoint value."""
    days = range(0, max(1, (p.end - p.start).days), 7)
    return sum(_season(p.start + timedelta(days=d), phase_doy) for d in days) / len(days)


def _years_since_2016(d: date) -> float:
    return (d - date(2016, 1, 1)).days / 365.25


def _value(variable: str, area: Area, p: Period, today: date) -> PeriodValue:
    if p.start >= today:
        return PeriodValue(period=p.start, mean=None, valid_fraction=0.0, status="missing")
    rng = _rng(area.area_id, variable, p.start)
    mid = p.mid
    days = (p.end - p.start).days
    spread = 0.0
    if variable == "synthetic_ndvi":
        winter = mid.month in (12, 1, 2)
        if winter and rng.random() < 0.5:
            return PeriodValue(period=p.start, mean=None, valid_fraction=0.0, status="missing")
        mean = 0.3 + 0.15 * _period_season(p, 110) + 0.004 * _years_since_2016(mid) + rng.gauss(0, 0.02)
        spread = 0.25
        valid = rng.uniform(0.3, 0.7) if winter else rng.uniform(0.75, 1.0)
    elif variable == "synthetic_precipitation":
        mean = max(0.0, days * (1.6 + 0.8 * _period_season(p, 100)) * rng.uniform(0.6, 1.4))
        valid = 1.0
    else:  # air temperature, model grid or station
        offset = 0.3 if variable == "synthetic_station_t2m" else 0.0
        mean = 9.0 + offset + 10.0 * _period_season(p, 105) + 0.06 * _years_since_2016(mid) + rng.gauss(0, 1.0)
        spread = 6.0
        valid = 1.0
    status = "preliminary" if p.end > today - timedelta(days=_PRELIMINARY_DAYS) else "final"
    return PeriodValue(
        period=p.start,
        mean=round(mean, 4),
        p10=round(mean - spread, 4) if spread else None,
        p90=round(mean + spread, 4) if spread else None,
        valid_fraction=round(valid, 3),
        n_obs=1,
        status=status,
    )


class SyntheticAdapter:
    info: ClassVar[SourceInfo] = SourceInfo(
        id="synthetic",
        name="Synthetic demo source",
        variables=[
            VariableInfo(name="synthetic_ndvi", units="1", topic="vegetation", description="Synthetic NDVI"),
            VariableInfo(name="synthetic_t2m", units="degC", topic="weather", description="Synthetic 2 m temperature"),
            VariableInfo(
                name="synthetic_precipitation",
                units="mm",
                topic="weather",
                description="Synthetic precipitation total",
                aggregation="sum",
            ),
            VariableInfo(
                name="synthetic_elevation", units="m", topic="terrain", description="Synthetic elevation", static=True
            ),
        ],
        access_pattern="server_stats",
        resolution="n/a",
        coverage=(date(2016, 1, 1), None),
        latency="P0D",
        licence="CC0-1.0",
        attribution="geomcp synthetic data",
        commercial_safe=True,
    )

    @classmethod
    def enabled(cls) -> bool:
        return _enabled()

    def health(self) -> tuple[Health, str]:
        return "ok", ""

    def _provenance(self) -> Provenance:
        return Provenance(
            source=self.info.name,
            backend="synthetic",
            collection="synthetic-v1",
            method="seeded seasonal model plus noise; not real data",
            licence=self.info.licence,
            attribution=self.info.attribution,
            commercial_safe=self.info.commercial_safe,
            retrieved_at=datetime.now(UTC),
        )

    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        rng = _rng(area.area_id, "elevation")
        base = rng.uniform(100, 140)
        value: dict[str, float | None] = {"min": round(base - 5, 2), "mean": round(base, 2), "max": round(base + 12, 2)}
        facts = [Fact(variable=v, units="m", value=value, provenance=self._provenance()) for v in variables]
        return facts, Cost()

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        today = datetime.now(UTC).date()
        units = next(v.units for v in self.info.variables if v.name == variable)
        return SeriesData(
            variable=variable,
            units=units,
            values=[_value(variable, area, p, today) for p in periods],
            provenance=Provenance(
                source=self.info.name,
                backend="synthetic",
                collection="synthetic-v1",
                method="seeded seasonal model plus noise; not real data",
                licence=self.info.licence,
                attribution=self.info.attribution,
                commercial_safe=self.info.commercial_safe,
                retrieved_at=datetime.now(UTC),
            ),
        )


class SyntheticRestrictedAdapter(SyntheticAdapter):
    """A restricted (not commercial-safe) synthetic station, to exercise licence notices."""

    info: ClassVar[SourceInfo] = SourceInfo(
        id="synthetic_station",
        name="Synthetic restricted station",
        variables=[
            VariableInfo(
                name="synthetic_station_t2m",
                units="degC",
                topic="weather",
                description="Synthetic station temperature",
            )
        ],
        access_pattern="timeseries",
        resolution="station",
        coverage=(date(2016, 1, 1), None),
        latency="PT1H",
        licence="restricted-demo",
        attribution="geomcp synthetic restricted data",
        commercial_safe=False,
    )
