"""Typed application settings.

Configuration is read from the environment exactly once, here. No other module
reads ``os.environ``; they take a ``Settings`` instance instead, which keeps the
codebase testable and makes every knob discoverable in one place.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class City(BaseModel):
    """A place Clearway ingests data for.

    Coverage is an explicit list rather than "the whole world" so that ingestion
    stays inside the free rate limits of both upstreams and the database stays
    small enough to run on a laptop.
    """

    slug: str
    name: str
    country: str
    latitude: float
    longitude: float
    #: IANA zone. Every hour a person reads is an hour in *this* zone, never
    #: UTC and never the zone the browser happens to sit in: "the air is clear
    #: at 04:00" is only actionable if 04:00 means 04:00 where the air is.
    timezone: str
    radius_km: int = 30
    #: Alternative names this city is published under upstream.
    aliases: tuple[str, ...] = ()


DEFAULT_CITIES: list[City] = [
    City(
        slug="dhaka", name="Dhaka", country="BD",
        latitude=23.8103, longitude=90.4125, timezone="Asia/Dhaka",
    ),
    # Officially renamed Chattogram in 2018; OpenAQ still carries both spellings,
    # so the search term is kept separate from the display name.
    City(
        slug="chattogram",
        name="Chattogram",
        country="BD",
        latitude=22.3569,
        longitude=91.7832,
        timezone="Asia/Dhaka",
        aliases=("Chittagong",),
    ),
    City(
        slug="delhi", name="Delhi", country="IN",
        latitude=28.6139, longitude=77.2090, timezone="Asia/Kolkata",
    ),
    City(
        slug="beijing", name="Beijing", country="CN",
        latitude=39.9042, longitude=116.4074, timezone="Asia/Shanghai",
    ),
    City(
        slug="moscow", name="Moscow", country="RU",
        latitude=55.7558, longitude=37.6173, timezone="Europe/Moscow",
    ),
    City(
        slug="berlin", name="Berlin", country="DE",
        latitude=52.5200, longitude=13.4050, timezone="Europe/Berlin",
    ),
    City(
        slug="london", name="London", country="GB",
        latitude=51.5072, longitude=-0.1276, timezone="Europe/London",
    ),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLEARWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- application ----------------------------------------------------
    env: str = "local"
    debug: bool = False
    log_level: str = "INFO"
    project_name: str = "Clearway"
    api_prefix: str = "/api/v1"

    # --- database -------------------------------------------------------
    database_url: str = "postgresql+psycopg://clearway:clearway@127.0.0.1:5436/clearway"
    db_echo: bool = False

    # --- upstreams ------------------------------------------------------
    openaq_api_key: str = ""
    openaq_base_url: str = "https://api.openaq.org/v3"
    waqi_api_token: str = ""
    waqi_base_url: str = "https://api.waqi.info"
    openmeteo_air_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality"
    openmeteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    openmeteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    openmeteo_geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    openmeteo_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    http_timeout_seconds: float = 20.0
    http_max_retries: int = 3

    # --- frontend -------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- modelling ------------------------------------------------------
    model_dir: Path = Path("var/models")
    forecast_horizons: int = 24
    #: A station reading older than this is not used for a nowcast.
    nowcast_max_age_minutes: int = 180
    #: Stations further than this from the query point never contribute.
    nowcast_max_distance_km: float = 30.0

    cities: Annotated[list[City], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_CITIES)
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def has_ground_truth(self) -> bool:
        """False means CAMS-only mode: forecasts work, scoring and training do not."""
        return bool(self.openaq_api_key or self.waqi_api_token)

    @property
    def enabled_measurement_providers(self) -> list[str]:
        """Which ground-truth networks are configured, in preference order."""
        providers = []
        if self.openaq_api_key:
            providers.append("openaq")
        if self.waqi_api_token:
            providers.append("waqi")
        return providers

    def city(self, slug: str) -> City | None:
        return next((c for c in self.cities if c.slug == slug), None)


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
