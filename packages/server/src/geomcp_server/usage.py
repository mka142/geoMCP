"""Provider usage per calendar month, to warn before a free quota runs out.

Counts only what this server spends; other uses of the same account are invisible to it.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from geomcp_core.models import Cost
from geomcp_server.db import Usage, sessions

SENTINEL_HUB = "sentinel-hub"


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def record(db_url: str, cost: Cost) -> None:
    if cost.sentinel_hub_units <= 0 and cost.cache_misses == 0:
        return
    for attempt in range(2):  # portable upsert; a concurrent first insert of the month retries once
        try:
            with sessions(db_url).begin() as s:
                row = s.get(Usage, (_month(), SENTINEL_HUB))
                if row is None:
                    s.add(
                        Usage(
                            month=_month(),
                            source=SENTINEL_HUB,
                            requests=cost.cache_misses,
                            units=cost.sentinel_hub_units,
                        )
                    )
                else:
                    row.requests += cost.cache_misses
                    row.units += cost.sentinel_hub_units
            return
        except IntegrityError:
            if attempt:
                raise


def this_month(db_url: str) -> Usage | None:
    with sessions(db_url)() as s:
        return s.get(Usage, (_month(), SENTINEL_HUB))
