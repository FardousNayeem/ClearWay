"""Shared query parameters and their validation.

Coordinates are parsed into a domain ``Point`` at the edge, so nothing below
the API layer ever handles a pair of loose floats.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from app.core.errors import ValidationError
from app.domain.geo import BoundingBox, Point

LatitudeQuery = Annotated[float, Query(ge=-90, le=90, description="Latitude in degrees")]
LongitudeQuery = Annotated[float, Query(ge=-180, le=180, description="Longitude in degrees")]


def to_point(latitude: float, longitude: float) -> Point:
    try:
        return Point(latitude, longitude)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def parse_bbox(raw: str) -> BoundingBox:
    try:
        return BoundingBox.parse(raw)
    except ValueError as exc:
        raise ValidationError(
            f"Invalid bounding box: {exc}. Expected west,south,east,north."
        ) from exc
