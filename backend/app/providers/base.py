"""The outbound HTTP boundary.

Rules for this package:

* ``httpx`` appears here and nowhere else in the codebase.
* Providers return the dataclasses defined below, never raw JSON, so a service
  cannot become coupled to an upstream's field names.
* Every upstream failure becomes :class:`UpstreamError`. Callers above this
  layer never see a transport exception.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, Self

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.errors import UpstreamError

logger = logging.getLogger(__name__)


# --- what providers hand back ------------------------------------------


@dataclass(frozen=True, slots=True)
class StationRecord:
    provider: str
    external_id: str
    name: str
    latitude: float
    longitude: float
    country: str | None = None
    timezone: str | None = None
    last_seen_at: dt.datetime | None = None
    parameters: tuple[str, ...] = ()
    #: Opaque provider-specific handles, e.g. OpenAQ sensor ids per parameter.
    sensor_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    parameter: str
    observed_at: dt.datetime
    #: Always micrograms per cubic metre, whatever units the upstream used.
    value: float


@dataclass(frozen=True, slots=True)
class AmbientRecord:
    valid_at: dt.datetime
    is_forecast: bool
    cams_pm25: float | None = None
    cams_pm10: float | None = None
    temperature_c: float | None = None
    relative_humidity: float | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    precipitation_mm: float | None = None
    pressure_hpa: float | None = None
    boundary_layer_m: float | None = None


@dataclass(frozen=True, slots=True)
class PlaceRecord:
    name: str
    country: str | None
    latitude: float
    longitude: float
    elevation_m: float | None = None
    timezone: str | None = None
    population: int | None = None


class MeasurementProvider(Protocol):
    """Anything that can supply ground truth.

    Two implementations exist, OpenAQ and WAQI, and the ingestion service does
    not care which it is given. Adding a third network means adding a class
    here and nothing else.
    """

    name: str

    def find_stations(
        self, latitude: float, longitude: float, radius_km: float
    ) -> list[StationRecord]: ...

    def fetch_hourly(
        self, station: StationRecord, start: dt.datetime, end: dt.datetime
    ) -> list[MeasurementRecord]: ...


# --- shared client behaviour -------------------------------------------


class HttpProvider:
    """Timeouts, retries and error translation, done once."""

    name: str = "http"

    def __init__(self, *, timeout: float = 20.0, max_retries: int = 3) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": "Clearway/0.1 (open data air quality)"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        @retry(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.5, max=8),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        )
        def _send() -> httpx.Response:
            response = self._client.get(url, params=params)
            # 4xx other than rate limiting will not improve on retry.
            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                raise httpx.HTTPStatusError(
                    "rate limited", request=response.request, response=response
                )
            if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
                raise httpx.HTTPStatusError(
                    "upstream error", request=response.request, response=response
                )
            return response

        try:
            response = _send()
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "%s returned %s for %s", self.name, exc.response.status_code, url
            )
            raise UpstreamError(
                f"{self.name} responded with {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("%s transport failure for %s: %s", self.name, url, exc)
            raise UpstreamError(f"Could not reach {self.name}.") from exc
        except ValueError as exc:
            raise UpstreamError(f"{self.name} returned a response that was not JSON.") from exc

        if not isinstance(payload, dict):
            raise UpstreamError(f"{self.name} returned an unexpected response shape.")
        return payload


def parse_utc(raw: str | None) -> dt.datetime | None:
    """Parse an ISO timestamp, tolerating the ``Z`` suffix, into aware UTC."""

    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def as_float(value: Any) -> float | None:
    """Upstreams use ``None``, ``"-"`` and empty strings for a missing reading."""

    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # drop NaN
