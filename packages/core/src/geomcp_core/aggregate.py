"""Aggregate observations (per acquisition or per day) into periods."""

from datetime import date
from statistics import fmean, quantiles
from typing import Literal

from geomcp_core.models import Observation, Period, PeriodValue

How = Literal["mean", "sum"]


def to_periods(
    observations: list[Observation],
    periods: list[Period],
    how: How,
    today: date,
    min_valid: float = 0.0,
    preliminary_from: date | None = None,
    coverage: Literal["area", "days"] = "area",
) -> list[PeriodValue]:
    """`mean`: mean of usable observation means (satellite, daily weather means).
    `sum`: total of daily values (precipitation, radiation); valid_fraction is the share of days present.
    Observations below `min_valid` (for example cloudy scenes) are not used but count toward valid_fraction.
    Periods ending after `preliminary_from` are `preliminary`; periods starting today or later are `missing`.
    """
    out: list[PeriodValue] = []
    for p in periods:
        bucket = [o for o in observations if p.start <= o.day < p.end]
        usable = [o for o in bucket if o.valid_fraction >= min_valid]
        if p.start >= today or not usable:
            out.append(PeriodValue(period=p.start, mean=None, valid_fraction=0.0, n_obs=0, status="missing"))
            continue
        means = [o.mean for o in usable]
        if coverage == "days":  # share of the period's days that have data
            vf = min(1.0, len({o.day for o in usable}) / max(1, (min(p.end, today) - p.start).days))
        else:  # mean cloud-free share of the area over the acquisitions actually used
            vf = fmean(o.valid_fraction for o in usable)
        if how == "sum":
            mean = sum(means)
            p10 = p90 = None
        else:
            mean = fmean(means)
            if all(o.p10 is not None and o.p90 is not None for o in usable):
                p10 = fmean(o.p10 for o in usable if o.p10 is not None)
                p90 = fmean(o.p90 for o in usable if o.p90 is not None)
            elif len(means) >= 2:
                q = quantiles(means, n=10, method="inclusive")
                p10, p90 = q[0], q[-1]
            else:
                p10 = p90 = None
        status = "preliminary" if preliminary_from is not None and p.end > preliminary_from else "final"
        out.append(
            PeriodValue(
                period=p.start,
                mean=round(mean, 6),
                p10=None if p10 is None else round(p10, 6),
                p90=None if p90 is None else round(p90, 6),
                valid_fraction=round(vf, 3),
                n_obs=len(usable),
                status=status,
            )
        )
    return out
