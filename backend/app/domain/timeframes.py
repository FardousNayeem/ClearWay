"""Time features and hour bucketing.

Hour of day and day of week are cyclical: 23:00 is one hour from 00:00, not
twenty-three. Encoding them as sine and cosine pairs tells the model that,
which a raw integer does not.
"""

from __future__ import annotations

import datetime as dt
import math

HOURS_IN_DAY = 24
DAYS_IN_WEEK = 7


def floor_to_hour(moment: dt.datetime) -> dt.datetime:
    """All time series in Clearway are keyed on a whole UTC hour."""

    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; everything is timezone aware")
    return moment.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)


def hour_range(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Every whole hour in ``[start, end)``, ascending."""

    if end < start:
        raise ValueError("end is before start")
    cursor, hours = floor_to_hour(start), []
    stop = floor_to_hour(end)
    while cursor < stop:
        hours.append(cursor)
        cursor += dt.timedelta(hours=1)
    return hours


def cyclical(value: float, period: float) -> tuple[float, float]:
    angle = 2 * math.pi * (value / period)
    return math.sin(angle), math.cos(angle)


def calendar_features(moment: dt.datetime) -> dict[str, float]:
    """The calendar part of a feature row, in the local sense of 'time of day'."""

    hour_sin, hour_cos = cyclical(moment.hour, HOURS_IN_DAY)
    dow_sin, dow_cos = cyclical(moment.weekday(), DAYS_IN_WEEK)
    return {
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "dow_sin": dow_sin,
        "dow_cos": dow_cos,
        "is_weekend": float(moment.weekday() >= 5),
    }


def wind_components(speed_ms: float | None, direction_deg: float | None) -> dict[str, float]:
    """Wind as u/v components.

    A bearing is cyclical in the same way an hour is, and 359 degrees is not far
    from 1 degree. Splitting into components also lets the model learn that wind
    *from* a particular direction carries a particular source of pollution.
    """

    if speed_ms is None or direction_deg is None:
        return {"wind_u": float("nan"), "wind_v": float("nan")}
    radians = math.radians(direction_deg)
    return {
        "wind_u": -speed_ms * math.sin(radians),
        "wind_v": -speed_ms * math.cos(radians),
    }
