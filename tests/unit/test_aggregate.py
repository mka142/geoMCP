from datetime import date

from geomcp_core.aggregate import to_periods
from geomcp_core.models import Observation, Period

TODAY = date(2026, 10, 8)
JUNE = [Period(start=date(2024, 6, 1), end=date(2024, 7, 1))]


def test_satellite_valid_fraction_is_over_used_acquisitions_only() -> None:
    obs = [
        Observation(day=date(2024, 6, 3), mean=0.5, valid_fraction=0.9),
        Observation(day=date(2024, 6, 8), mean=0.4, valid_fraction=0.7),
        Observation(day=date(2024, 6, 13), mean=0.0, valid_fraction=0.0),  # fully cloudy: not used
        Observation(day=date(2024, 6, 18), mean=0.9, valid_fraction=0.1),  # under min_valid: not used
    ]
    (v,) = to_periods(obs, JUNE, "mean", TODAY, min_valid=0.2)
    assert v.mean == 0.45 and v.n_obs == 2 and v.valid_fraction == 0.8 and v.status == "final"


def test_weather_sums_report_share_of_days() -> None:
    obs = [Observation(day=date(2024, 6, d), mean=2.0) for d in range(1, 30)]  # 29 of 30 days
    (v,) = to_periods(obs, JUNE, "sum", TODAY, coverage="days")
    assert v.mean == 58.0 and v.n_obs == 29 and v.valid_fraction == 0.967 and v.status == "final"


def test_settled_sum_missing_many_days_is_a_gap_not_a_low_total() -> None:
    obs = [Observation(day=date(2024, 6, d), mean=2.0) for d in range(1, 16)]  # half the month
    (v,) = to_periods(obs, JUNE, "sum", TODAY, coverage="days")
    assert v.mean is None and v.status == "missing" and v.valid_fraction == 0.5
    (mean,) = to_periods(obs, JUNE, "mean", TODAY, coverage="days")  # a mean of the days present is still fair
    assert mean.mean == 2.0 and mean.status == "final"


def test_no_usable_observation_is_missing() -> None:
    (v,) = to_periods([Observation(day=date(2024, 6, 3), mean=0.0, valid_fraction=0.0)], JUNE, "mean", TODAY, 0.2)
    assert v.mean is None and v.status == "missing" and v.n_obs == 0


def test_recent_periods_are_preliminary_and_future_ones_missing() -> None:
    periods = [
        Period(start=date(2026, 9, 1), end=date(2026, 10, 1)),
        Period(start=date(2026, 11, 1), end=date(2026, 12, 1)),
    ]
    obs = [Observation(day=date(2026, 9, 5), mean=1.0)]
    recent, future = to_periods(obs, periods, "mean", TODAY, preliminary_from=date(2026, 7, 10))
    assert recent.status == "preliminary" and future.status == "missing"
