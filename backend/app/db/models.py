"""The database schema.

Tables only: shape, keys, constraints and indexes. No business rules live here,
and nothing in this module imports from ``services`` or ``api``.

Every time series is keyed on a whole UTC hour, so a natural key like
``(station, parameter, observed_at)`` is genuinely unique and upserts are safe
to retry. That is what lets ingestion be re-run without creating duplicates.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Parameter(StrEnum):
    PM25 = "pm25"
    PM10 = "pm10"


class Estimator(StrEnum):
    """Who produced a prediction. Baselines are stored alongside the model so
    the scorecard compares like with like on identical rows."""

    MODEL = "model"
    PERSISTENCE = "persistence"
    CLIMATOLOGY = "climatology"
    CAMS = "cams"


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Station(Base, TimestampMixin):
    """A physical monitoring station, from any ground-truth provider."""

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city_slug: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(2))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    timezone: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: Provider-side handles keyed by parameter, e.g. OpenAQ sensor ids. Opaque
    #: to us, but required to fetch measurements without re-running discovery.
    sensor_ids: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    measurements: Mapped[list[Measurement]] = relationship(
        back_populates="station", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_station_provider_external"),
        # A bounding-box prefilter can use this; haversine then refines it.
        Index("ix_station_lat_lon", "latitude", "longitude"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_station_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_station_longitude"),
    )

    def __repr__(self) -> str:
        return f"<Station {self.provider}:{self.external_id} {self.name!r}>"


class Measurement(Base):
    """One hourly ground-truth observation. The training label."""

    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    parameter: Mapped[str] = mapped_column(String(8), nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    ingested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    station: Mapped[Station] = relationship(back_populates="measurements")

    __table_args__ = (
        UniqueConstraint(
            "station_id", "parameter", "observed_at", name="uq_measurement_natural_key"
        ),
        Index("ix_measurement_station_time", "station_id", "observed_at"),
        Index("ix_measurement_time", "observed_at"),
        CheckConstraint("value >= 0", name="ck_measurement_non_negative"),
    )


class AmbientCondition(Base):
    """CAMS output and weather for one station-hour.

    Both come from Open-Meteo and are always fetched together for the same
    coordinate and hour, so keeping them in one row avoids a join in the hot
    path of feature building.
    """

    __tablename__ = "ambient_conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    valid_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: True when this row came from a forecast run rather than reanalysis.
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    cams_pm25: Mapped[float | None] = mapped_column(Float)
    cams_pm10: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    relative_humidity: Mapped[float | None] = mapped_column(Float)
    wind_speed_ms: Mapped[float | None] = mapped_column(Float)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float)
    precipitation_mm: Mapped[float | None] = mapped_column(Float)
    pressure_hpa: Mapped[float | None] = mapped_column(Float)
    boundary_layer_m: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("station_id", "valid_at", name="uq_ambient_natural_key"),
        Index("ix_ambient_station_time", "station_id", "valid_at"),
    )


class ModelVersion(Base, TimestampMixin):
    """A trained artefact, with everything needed to reproduce and judge it."""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(80), nullable=False)
    trained_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_names: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    #: Held-out metrics at training time, including every baseline.
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_path: Mapped[str] = mapped_column(String(400), nullable=False)
    #: Exactly one version per name serves traffic.
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str] = mapped_column(String(400), default="", nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_model_name_version"),
        Index(
            "uq_model_single_active",
            "name",
            unique=True,
            postgresql_where=(is_active.is_(True)),
        ),
    )


class Forecast(Base):
    """A prediction, kept so it can be scored once the truth arrives.

    Persisting predictions before the outcome is known is the whole basis of
    the scorecard. Scoring after the fact from a re-run would be cheating.
    """

    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    estimator: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Estimator.MODEL.value
    )
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    pm25: Mapped[float] = mapped_column(Float, nullable=False)
    pm25_low: Mapped[float | None] = mapped_column(Float)
    pm25_high: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint(
            "station_id", "estimator", "issued_at", "valid_at", name="uq_forecast_natural_key"
        ),
        Index("ix_forecast_station_valid", "station_id", "valid_at"),
        Index("ix_forecast_issued", "issued_at"),
        CheckConstraint("horizon_hours BETWEEN 1 AND 120", name="ck_forecast_horizon"),
    )


class ModelScore(Base):
    """Daily accuracy, per horizon, per estimator. What the scorecard renders."""

    __tablename__ = "model_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE")
    )
    estimator: Mapped[str] = mapped_column(String(16), nullable=False)
    scored_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    horizon_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mae: Mapped[float] = mapped_column(Float, nullable=False)
    rmse: Mapped[float] = mapped_column(Float, nullable=False)
    bias: Mapped[float] = mapped_column(Float, nullable=False)
    #: Share of actuals that fell inside the 80% band. Only the model has one.
    coverage_80: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        # Baseline rows carry a NULL model_version_id, and by default Postgres
        # treats NULLs as distinct, so this key would not stop a re-run from
        # duplicating every baseline score. NULLS NOT DISTINCT fixes that.
        UniqueConstraint(
            "model_version_id",
            "estimator",
            "scored_on",
            "horizon_hours",
            name="uq_score_natural_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_score_scored_on", "scored_on"),
    )
