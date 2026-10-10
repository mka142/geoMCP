"""Shared data models: areas, sources, provenance and results."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["final", "preliminary", "missing"]
AccessPattern = Literal["server_stats", "timeseries", "pixels"]
Health = Literal["ok", "degraded", "unconfigured", "quota_low"]
Cadence = Literal["P1W", "P1M", "P3M", "P1Y"]
AreaKind = Literal["buffer", "gmina", "powiat", "wojewodztwo", "parcel", "place"]


class GeomcpError(Exception):
    """An expected failure with a stable code and a hint for the caller."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class Area(BaseModel):
    area_id: str
    name: str
    kind: AreaKind
    area_km2: float
    bbox: tuple[float, float, float, float] = Field(description="min lon, min lat, max lon, max lat")
    geometry: dict[str, Any] = Field(description="GeoJSON in EPSG:4326, simplified to at most ~200 vertices")
    admin: dict[str, str] = Field(default_factory=dict, description="Administrative codes, e.g. TERYT 'terc'")
    source: str
    licence: str


class VariableInfo(BaseModel):
    name: str
    units: str
    topic: str
    description: str
    static: bool = Field(default=False, description="Does not change over time; ask get_summary, not get_timeseries")
    aggregation: Literal["mean", "sum"] = Field(default="mean", description="How values combine over a period")


class SourceInfo(BaseModel):
    id: str
    name: str
    variables: list[VariableInfo]
    access_pattern: AccessPattern
    resolution: str
    coverage: tuple[date, date | None]
    latency: str = Field(description="ISO 8601 duration between observation and availability")
    licence: str
    attribution: str
    commercial_safe: bool
    auth_env: list[str] = Field(default_factory=list, description="Environment variables the source needs")


class Period(BaseModel):
    start: date
    end: date

    @property
    def mid(self) -> date:
        return self.start + (self.end - self.start) / 2


class Provenance(BaseModel):
    source: str
    backend: str
    collection: str
    method: str
    licence: str
    attribution: str
    commercial_safe: bool
    retrieved_at: datetime


class PeriodValue(BaseModel):
    period: date
    mean: float | None
    p10: float | None = None
    p90: float | None = None
    valid_fraction: float = Field(
        description="Satellites: mean cloud-free share of the area over the acquisitions used. "
        "Weather: share of days with data."
    )
    n_obs: int = Field(default=0, description="Acquisitions (satellites) or days (weather) behind the value")
    status: Status


class Observation(BaseModel):
    """One acquisition (satellite) or one day (weather) over the whole area."""

    day: date
    mean: float
    p10: float | None = None
    p90: float | None = None
    valid_fraction: float = 1.0


class Cost(BaseModel):
    duration_s: float = 0.0
    sentinel_hub_units: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0

    def add(self, other: "Cost") -> None:
        self.sentinel_hub_units += other.sentinel_hub_units
        self.cache_hits += other.cache_hits
        self.cache_misses += other.cache_misses


class SeriesData(BaseModel):
    """What an adapter returns for one variable over a period grid."""

    variable: str
    units: str
    values: list[PeriodValue]
    provenance: Provenance
    cost: Cost = Field(default_factory=Cost)


class Extreme(BaseModel):
    value: float
    period: date


class Summary(BaseModel):
    trend_per_year: float | None
    trend_method: Literal["Theil-Sen"] = "Theil-Sen"
    trend_p_value: float | None = Field(description="Two-sided Mann-Kendall p-value")
    min: Extreme | None
    max: Extreme | None
    latest: Extreme | None
    gaps: int


class SeriesSummary(BaseModel):
    variable: str
    units: str
    summary: Summary
    provenance: Provenance


class Gap(BaseModel):
    variable: str
    period: date | None
    reason: str
    valid_fraction: float | None = None


class RowsTruncated(BaseModel):
    shown: int
    total: int
    reason: Literal["max_rows"] = "max_rows"


class PeriodSpec(BaseModel):
    start: date
    end: date
    cadence: Cadence
    n_periods: int


class TimeseriesResult(BaseModel):
    area_id: str
    period: PeriodSpec
    series: list[SeriesSummary]
    rows_columns: list[str]
    rows: list[list[Any]]
    rows_truncated: RowsTruncated | None
    gaps: list[Gap]
    notices: list[str]
    cost: Cost = Field(default_factory=Cost)
    downloads: dict[str, str] = Field(
        default_factory=dict, description="The full table (all rows) as files: csv and parquet links or local paths"
    )
    full_rows: list[list[Any]] = Field(default_factory=list[list[Any]], exclude=True)


class SourceStatus(BaseModel):
    source: SourceInfo
    health: Health
    hint: str = ""


class Fact(BaseModel):
    """A one-shot value for get_summary: static layers, or a recent window of a dynamic variable."""

    variable: str
    units: str
    value: float | dict[str, float | None]
    period: str | None = Field(default=None, description="ISO interval the value covers, if any")
    status: Status = "final"
    provenance: Provenance


class Snapshot(BaseModel):
    layer: str
    png: bytes = Field(exclude=True)
    scene_date: date | None
    requested_date: date | None
    scene_cloud_cover: float | None = Field(description="Cloud fraction of the whole satellite tile (0-1)")
    size_px: tuple[int, int]
    pixel_size_m: float
    legend: dict[str, Any]
    units_spent: float = 0.0
    provenance: Provenance
