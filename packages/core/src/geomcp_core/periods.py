"""Period grids: `[start, end)` periods labelled by their start, in UTC dates."""

from datetime import date, timedelta

from geomcp_core.models import Cadence, GeomcpError, Period

_MONTHS: dict[str, int] = {"P1M": 1, "P3M": 3, "P1Y": 12}


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def default_cadence(start: date, end: date) -> Cadence:
    return "P3M" if (end - start).days > 2 * 366 else "P1M"


def period_grid(start: date, end: date, cadence: Cadence) -> list[Period]:
    """Split `[start, end)` into periods; weekly periods are ISO weeks, others start on the 1st."""
    if end <= start:
        raise GeomcpError("invalid_range", f"end ({end}) must be after start ({start}).")
    if cadence == "P1W":
        if start.weekday() != 0:
            raise GeomcpError(
                "invalid_range",
                f"Weekly periods are ISO weeks, so start must be a Monday; {start} is not.",
                hint=f"Use {start - timedelta(days=start.weekday())}.",
            )
        step = timedelta(weeks=1)
        starts = [start + k * step for k in range((end - start + step - timedelta(days=1)) // step)]
        return [Period(start=s, end=min(s + step, end)) for s in starts]
    if start.day != 1:
        raise GeomcpError(
            "invalid_range",
            f"{cadence} periods start on the 1st of a month; {start} is not.",
            hint=f"Use {start.replace(day=1)}.",
        )
    months = _MONTHS[cadence]
    periods: list[Period] = []
    s = start
    while s < end:
        e = _add_months(s, months)
        periods.append(Period(start=s, end=min(e, end)))
        s = e
    return periods
