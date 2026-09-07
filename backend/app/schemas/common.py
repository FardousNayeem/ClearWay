"""Response DTOs.

Separate from the ORM on purpose. A table is how data is stored; a schema is
what is promised to a client. Coupling them means a column rename becomes a
breaking API change.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CitySchema(Schema):
    slug: str
    name: str
    country: str
    latitude: float
    longitude: float
    #: IANA zone, so the client can show every hour in the city's own time.
    timezone: str


class PlaceSchema(Schema):
    name: str
    country: str | None
    latitude: float
    longitude: float
    timezone: str | None = None
    population: int | None = None


class AqiSchema(Schema):
    value: int
    category: str
    label: str
    advice: str
    pollutant: str


class StationSchema(Schema):
    id: int
    name: str
    provider: str
    city_slug: str
    latitude: float
    longitude: float
    #: As reported by the upstream network; absent for some, so the client
    #: falls back to the zone of the place the user picked.
    timezone: str | None = None
    elevation_m: float | None = None
    distance_km: float | None = None
    pm25: float | None = None
    pm10: float | None = None
    observed_at: dt.datetime | None = None
    aqi: AqiSchema | None = None


class ContributionSchema(Schema):
    """Which station fed an estimate, how far away, how old, how much weight.

    Every estimate carries this. A number without provenance is not something
    a person should act on.
    """

    station_id: int
    name: str
    provider: str
    distance_km: float
    age_minutes: int
    weight: float
    pm25: float


class NowcastSchema(Schema):
    latitude: float
    longitude: float
    observed_at: dt.datetime
    pm25: float
    pm10: float | None = None
    aqi: AqiSchema
    #: "stations" when interpolated from real readings, "cams" when falling
    #: back to the physics model because nothing is close enough or fresh enough.
    source: str
    station_count: int
    nearest_km: float | None = None
    max_age_minutes: int | None = None
    contributions: list[ContributionSchema] = Field(default_factory=list)


class ForecastPointSchema(Schema):
    valid_at: dt.datetime
    pm25: float
    pm25_low: float | None = None
    pm25_high: float | None = None
    cams_pm25: float | None = None
    aqi: AqiSchema


class ForecastSchema(Schema):
    latitude: float
    longitude: float
    issued_at: dt.datetime
    model_version: int | None
    estimator: str
    horizon_hours: int
    station_id: int | None
    station_name: str | None
    points: list[ForecastPointSchema]


class BestHourSchema(Schema):
    valid_at: dt.datetime
    pm25: float
    aqi: AqiSchema
    rank: int


class GuidanceSchema(Schema):
    latitude: float
    longitude: float
    sensitivity: str
    threshold_pm25: float
    issued_at: dt.datetime
    best_hours: list[BestHourSchema]
    clear_windows: list[dict]
    advice: str


class ModelVersionSchema(Schema):
    id: int
    name: str
    version: int
    algorithm: str
    trained_at: dt.datetime
    training_rows: int
    window_start: dt.datetime
    window_end: dt.datetime
    is_active: bool
    metrics: dict
    notes: str


class ScoreSchema(Schema):
    estimator: str
    scored_on: dt.date
    horizon_hours: int
    sample_size: int
    mae: float
    rmse: float
    bias: float
    coverage_80: float | None = None


class HealthSchema(Schema):
    status: str
    database: str
    ground_truth_providers: list[str]
    active_model_version: int | None
