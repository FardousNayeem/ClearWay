"""Geospatial maths, in plain Python.

The spatial problem here is "which handful of stations are near this point",
over hundreds of rows. That does not justify a PostGIS dependency, and doing it
here keeps the logic unit testable without a database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True, slots=True)
class Point:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude {self.latitude} is outside -90..90")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude {self.longitude} is outside -180..180")


@dataclass(frozen=True, slots=True)
class BoundingBox:
    west: float
    south: float
    east: float
    north: float

    @classmethod
    def parse(cls, raw: str) -> BoundingBox:
        """Parse ``west,south,east,north``, the order used by OpenAQ and OGC."""
        parts = raw.split(",")
        if len(parts) != 4:
            raise ValueError("a bounding box needs four comma separated numbers")
        west, south, east, north = (float(p) for p in parts)
        if west >= east or south >= north:
            raise ValueError("bounding box corners are in the wrong order")
        return cls(west, south, east, north)

    def contains(self, point: Point) -> bool:
        return (
            self.west <= point.longitude <= self.east
            and self.south <= point.latitude <= self.north
        )


def haversine_km(a: Point, b: Point) -> float:
    """Great-circle distance between two points, in kilometres."""

    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    d_lat = lat2 - lat1
    d_lon = math.radians(b.longitude - a.longitude)

    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def bounding_box_around(centre: Point, radius_km: float) -> BoundingBox:
    """A box that certainly contains the circle of the given radius.

    Used to narrow a database query cheaply before the exact distance filter,
    because an index can serve a box but not a haversine.
    """

    lat_delta = math.degrees(radius_km / EARTH_RADIUS_KM)
    # Longitude degrees shrink towards the poles; guard against division by zero.
    cos_lat = max(math.cos(math.radians(centre.latitude)), 1e-6)
    lon_delta = lat_delta / cos_lat
    return BoundingBox(
        west=max(centre.longitude - lon_delta, -180.0),
        south=max(centre.latitude - lat_delta, -90.0),
        east=min(centre.longitude + lon_delta, 180.0),
        north=min(centre.latitude + lat_delta, 90.0),
    )


def inverse_distance_weights(
    distances_km: list[float], power: float = 2.0, epsilon: float = 0.05
) -> list[float]:
    """Normalised inverse-distance weights.

    ``epsilon`` stops a station that sits almost exactly on the query point from
    taking all the weight and turning the estimate into a single noisy reading.
    """

    if not distances_km:
        return []
    raw = [1.0 / ((max(d, 0.0) + epsilon) ** power) for d in distances_km]
    total = sum(raw)
    if total == 0:
        return [1.0 / len(raw)] * len(raw)
    return [value / total for value in raw]
