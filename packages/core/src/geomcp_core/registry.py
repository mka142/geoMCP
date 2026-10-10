"""Source registry: adapters are discovered through the `geomcp.adapters` entry-point group."""

import logging
from functools import cache
from importlib.metadata import entry_points
from typing import ClassVar, Protocol, runtime_checkable

from geomcp_core.models import (
    AccessPattern,
    Area,
    Cost,
    Fact,
    Health,
    Period,
    SeriesData,
    SourceInfo,
    VariableInfo,
)

log = logging.getLogger(__name__)

# Router preference: server-side aggregates first, then time-chunked stores, then our own pixel reads.
ACCESS_ORDER: dict[AccessPattern, int] = {"server_stats": 0, "timeseries": 1, "pixels": 2}


class Adapter(Protocol):
    info: ClassVar[SourceInfo]

    @classmethod
    def enabled(cls) -> bool:
        """False hides the source entirely (for example, demo sources in production)."""
        ...

    def health(self) -> tuple[Health, str]:
        """Health and a hint, without network calls (for example, missing credentials)."""
        ...

    def stats(self, area: Area, variable: str, periods: list[Period]) -> SeriesData:
        """Aggregate one variable over the area for every period."""
        ...


@runtime_checkable
class Estimating(Protocol):
    def estimate_s(self, area: Area, periods: list[Period]) -> float:
        """Expected seconds to answer, from cache state alone (no network calls)."""
        ...


@runtime_checkable
class StaticFacts(Protocol):
    def facts(self, area: Area, variables: list[str]) -> tuple[list[Fact], Cost]:
        """Values that do not change over time (terrain, for example)."""
        ...


@cache
def adapters() -> tuple[Adapter, ...]:
    """Every enabled adapter; one that fails to load is skipped and logged, never taking the others down."""
    found: list[Adapter] = []
    for ep in entry_points(group="geomcp.adapters"):
        try:
            cls = ep.load()
            if cls.enabled():
                found.append(cls())
        except Exception:
            log.exception("adapter %s failed to load and is disabled", ep.name)
    return tuple(found)


def adapters_for(variable: str) -> list[Adapter]:
    """Adapters that provide `variable`, in router preference order."""
    hits = [a for a in adapters() if any(v.name == variable for v in a.info.variables)]
    return sorted(hits, key=lambda a: ACCESS_ORDER[a.info.access_pattern])


def variable_info(variable: str) -> tuple[Adapter, VariableInfo] | None:
    for a in adapters_for(variable):
        return a, next(v for v in a.info.variables if v.name == variable)
    return None
