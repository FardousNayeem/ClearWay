import datetime as dt
import math

import pytest

from app.domain.timeframes import (
    calendar_features,
    cyclical,
    floor_to_hour,
    hour_range,
    wind_components,
)


def test_hours_are_floored_and_normalised_to_utc():
    moment = dt.datetime(2026, 9, 6, 14, 47, 31, tzinfo=dt.timezone(dt.timedelta(hours=6)))

    floored = floor_to_hour(moment)

    assert floored.tzinfo is dt.UTC
    assert (floored.hour, floored.minute, floored.second) == (8, 0, 0)


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError, match="naive"):
        floor_to_hour(dt.datetime(2026, 9, 6, 14, 0))


def test_hour_range_is_half_open():
    start = dt.datetime(2026, 9, 6, 0, 0, tzinfo=dt.UTC)
    hours = hour_range(start, start + dt.timedelta(hours=3))

    assert len(hours) == 3
    assert hours[0] == start
    assert hours[-1] == start + dt.timedelta(hours=2)


def test_hour_range_rejects_a_backwards_window():
    start = dt.datetime(2026, 9, 6, 0, 0, tzinfo=dt.UTC)
    with pytest.raises(ValueError, match="end is before start"):
        hour_range(start, start - dt.timedelta(hours=1))


def test_midnight_and_twenty_three_hundred_are_neighbours_in_cyclical_space():
    """The whole point of the encoding: hour 23 must sit next to hour 0."""
    late = cyclical(23, 24)
    midnight = cyclical(0, 24)
    midday = cyclical(12, 24)

    def separation(a, b):
        return math.dist(a, b)

    assert separation(late, midnight) < separation(late, midday)


def test_calendar_features_flag_the_weekend():
    saturday = dt.datetime(2026, 9, 5, 9, 0, tzinfo=dt.UTC)
    tuesday = dt.datetime(2026, 9, 8, 9, 0, tzinfo=dt.UTC)

    assert calendar_features(saturday)["is_weekend"] == 1.0
    assert calendar_features(tuesday)["is_weekend"] == 0.0


def test_wind_splits_into_components_that_encode_direction():
    """A northerly (from 0 degrees) blows air southwards, so v is negative."""
    northerly = wind_components(10.0, 0.0)
    easterly = wind_components(10.0, 90.0)

    assert northerly["wind_v"] == pytest.approx(-10.0)
    assert northerly["wind_u"] == pytest.approx(0.0, abs=1e-9)
    assert easterly["wind_u"] == pytest.approx(-10.0)


def test_missing_wind_is_nan_not_zero():
    """Zero would mean 'dead calm', which is a claim we cannot make."""
    components = wind_components(None, None)

    assert math.isnan(components["wind_u"])
    assert math.isnan(components["wind_v"])
