"""Provider tests run against recorded response shapes, never the live network.

They exist to pin the two things that actually break: how a payload is parsed,
and what happens when an upstream misbehaves.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx

from app.core.errors import UpstreamError
from app.providers.base import StationRecord
from app.providers.openaq import OpenAqProvider
from app.providers.openmeteo import OpenMeteoProvider
from app.providers.waqi import WaqiProvider

OPENAQ_URL = "https://api.openaq.org/v3"
WAQI_URL = "https://api.waqi.info"


@pytest.fixture
def openaq() -> OpenAqProvider:
    return OpenAqProvider("test-key", OPENAQ_URL, max_retries=1)


@pytest.fixture
def waqi() -> WaqiProvider:
    return WaqiProvider("test-token", WAQI_URL, max_retries=1)


@pytest.fixture
def openmeteo() -> OpenMeteoProvider:
    return OpenMeteoProvider(
        air_url="https://air-quality-api.open-meteo.com/v1/air-quality",
        forecast_url="https://api.open-meteo.com/v1/forecast",
        archive_url="https://archive-api.open-meteo.com/v1/archive",
        geocoding_url="https://geocoding-api.open-meteo.com/v1/search",
        elevation_url="https://api.open-meteo.com/v1/elevation",
        max_retries=1,
    )


# --- OpenAQ --------------------------------------------------------------


@respx.mock
def test_openaq_parses_a_station_and_its_sensors(openaq):
    respx.get(f"{OPENAQ_URL}/locations").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 8118,
                        "name": "US Diplomatic Post: Dhaka",
                        "country": {"code": "BD"},
                        "timezone": "Asia/Dhaka",
                        "coordinates": {"latitude": 23.796, "longitude": 90.424},
                        "datetimeLast": {"utc": "2026-09-06T05:00:00Z"},
                        "sensors": [
                            {"id": 24001, "parameter": {"name": "pm25", "units": "µg/m³"}},
                            {"id": 24002, "parameter": {"name": "pm10", "units": "µg/m³"}},
                        ],
                    }
                ]
            },
        )
    )

    [station] = openaq.find_stations(23.81, 90.41, radius_km=25)

    assert station.provider == "openaq"
    assert station.external_id == "8118"
    assert station.country == "BD"
    assert station.sensor_ids == {"pm25": "24001", "pm10": "24002"}
    assert station.last_seen_at == dt.datetime(2026, 9, 6, 5, 0, tzinfo=dt.UTC)


@respx.mock
def test_openaq_skips_stations_that_measure_nothing_we_want(openaq):
    respx.get(f"{OPENAQ_URL}/locations").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 1,
                        "name": "Ozone only",
                        "coordinates": {"latitude": 23.8, "longitude": 90.4},
                        "sensors": [{"id": 9, "parameter": {"name": "o3"}}],
                    }
                ]
            },
        )
    )

    assert openaq.find_stations(23.81, 90.41, radius_km=25) == []


@respx.mock
def test_openaq_radius_is_capped_at_the_documented_maximum(openaq):
    route = respx.get(f"{OPENAQ_URL}/locations").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    openaq.find_stations(23.81, 90.41, radius_km=500)

    assert route.calls.last.request.url.params["radius"] == "25000"


@respx.mock
def test_openaq_hourly_measurements_are_parsed_from_the_period_start(openaq):
    respx.get(f"{OPENAQ_URL}/sensors/24001/hours").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"found": 2},
                "results": [
                    {
                        "value": 84.3,
                        "period": {"datetimeFrom": {"utc": "2026-09-06T03:00:00Z"}},
                    },
                    {
                        "value": -999,
                        "period": {"datetimeFrom": {"utc": "2026-09-06T04:00:00Z"}},
                    },
                ],
            },
        )
    )
    station = StationRecord(
        provider="openaq",
        external_id="8118",
        name="Dhaka",
        latitude=23.8,
        longitude=90.4,
        sensor_ids={"pm25": "24001"},
    )

    records = openaq.fetch_hourly(
        station,
        dt.datetime(2026, 9, 6, tzinfo=dt.UTC),
        dt.datetime(2026, 9, 7, tzinfo=dt.UTC),
    )

    assert len(records) == 1, "a -999 sentinel is a fault code, not a reading"
    assert records[0].value == 84.3
    assert records[0].parameter == "pm25"


@respx.mock
def test_a_failing_upstream_becomes_an_upstream_error_not_a_transport_error(openaq):
    respx.get(f"{OPENAQ_URL}/locations").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(UpstreamError):
        openaq.find_stations(23.81, 90.41, radius_km=25)


@respx.mock
def test_one_broken_sensor_does_not_lose_the_whole_station(openaq):
    respx.get(f"{OPENAQ_URL}/sensors/1/hours").mock(
        return_value=httpx.Response(500, json={})
    )
    respx.get(f"{OPENAQ_URL}/sensors/2/hours").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"found": 1},
                "results": [
                    {"value": 40.0, "period": {"datetimeFrom": {"utc": "2026-09-06T03:00:00Z"}}}
                ],
            },
        )
    )
    station = StationRecord(
        provider="openaq",
        external_id="8118",
        name="Dhaka",
        latitude=23.8,
        longitude=90.4,
        sensor_ids={"pm25": "1", "pm10": "2"},
    )

    records = openaq.fetch_hourly(
        station,
        dt.datetime(2026, 9, 6, tzinfo=dt.UTC),
        dt.datetime(2026, 9, 7, tzinfo=dt.UTC),
    )

    assert [r.parameter for r in records] == ["pm10"]


def test_openaq_refuses_to_construct_without_a_key():
    with pytest.raises(ValueError, match="API key"):
        OpenAqProvider("", OPENAQ_URL)


# --- WAQI ----------------------------------------------------------------


@respx.mock
def test_waqi_converts_its_index_values_back_to_concentrations(waqi):
    """The single most important line in this provider. WAQI publishes an AQI
    index; storing it as micrograms would overstate pollution about threefold."""
    respx.get(f"{WAQI_URL}/feed/@1451/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "data": {
                    "idx": 1451,
                    "time": {"iso": "2026-09-06T11:00:00+06:00"},
                    "iaqi": {"pm25": {"v": 155}, "pm10": {"v": 50}},
                },
            },
        )
    )
    station = StationRecord(
        provider="waqi", external_id="1451", name="Dhaka US Embassy",
        latitude=23.8, longitude=90.4,
    )

    records = waqi.fetch_hourly(
        station,
        dt.datetime(2026, 9, 6, tzinfo=dt.UTC),
        dt.datetime(2026, 9, 7, tzinfo=dt.UTC),
    )
    by_parameter = {r.parameter: r.value for r in records}

    # AQI 155 sits in the Unhealthy band, which starts at 55.5 ug/m3.
    assert 55.0 < by_parameter["pm25"] < 62.0
    assert by_parameter["pm25"] != 155, "the index was stored raw"
    assert by_parameter["pm10"] == pytest.approx(54.0, abs=1.5)


@respx.mock
def test_waqi_reports_failure_in_the_body_with_a_200(waqi):
    respx.get(f"{WAQI_URL}/feed/@99/").mock(
        return_value=httpx.Response(200, json={"status": "error", "data": "Unknown station"})
    )
    station = StationRecord(
        provider="waqi", external_id="99", name="x", latitude=0.0, longitude=0.0
    )

    assert waqi.fetch_hourly(
        station, dt.datetime(2026, 9, 6, tzinfo=dt.UTC), dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
    ) == []


@respx.mock
def test_waqi_filters_search_results_by_real_distance(waqi):
    respx.get(f"{WAQI_URL}/search/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "data": [
                    {
                        "uid": 1,
                        "time": {"stime": "2026-09-06 11:00:00"},
                        "station": {"name": "Dhaka", "geo": [23.80, 90.41]},
                    },
                    {
                        "uid": 2,
                        "time": {"stime": "2026-09-06 11:00:00"},
                        "station": {"name": "Kolkata", "geo": [22.57, 88.36]},
                    },
                ],
            },
        )
    )

    stations = waqi.find_stations(23.81, 90.41, radius_km=30)

    assert [s.external_id for s in stations] == ["1"]


# --- Open-Meteo ----------------------------------------------------------


@respx.mock
def test_openmeteo_merges_air_quality_and_weather_on_the_hour(openmeteo):
    respx.get("https://air-quality-api.open-meteo.com/v1/air-quality").mock(
        return_value=httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-09-06T00:00", "2026-09-06T01:00"],
                    "pm2_5": [62.1, 58.4],
                    "pm10": [90.0, 88.2],
                }
            },
        )
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-09-06T00:00", "2026-09-06T01:00"],
                    "temperature_2m": [28.4, 27.9],
                    "relative_humidity_2m": [78, 81],
                    "wind_speed_10m": [3.1, 2.8],
                    "wind_direction_10m": [190, 200],
                    "precipitation": [0.0, 0.2],
                    "surface_pressure": [1006.2, 1006.0],
                    "boundary_layer_height": [420, 380],
                }
            },
        )
    )

    records = openmeteo.fetch_forecast(23.81, 90.41)

    assert len(records) == 2
    assert records[0].cams_pm25 == 62.1
    assert records[0].temperature_c == 28.4
    assert records[0].boundary_layer_m == 420
    assert records[0].is_forecast is True
    assert records[0].valid_at == dt.datetime(2026, 9, 6, 0, 0, tzinfo=dt.UTC)


@respx.mock
def test_a_variable_missing_from_a_model_run_is_none_not_zero(openmeteo):
    """Zero precipitation is a claim. Absent precipitation is not."""
    respx.get("https://air-quality-api.open-meteo.com/v1/air-quality").mock(
        return_value=httpx.Response(
            200, json={"hourly": {"time": ["2026-09-06T00:00"], "pm2_5": [40.0]}}
        )
    )
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(
            200, json={"hourly": {"time": ["2026-09-06T00:00"], "temperature_2m": [20.0]}}
        )
    )

    [record] = openmeteo.fetch_forecast(23.81, 90.41)

    assert record.cams_pm10 is None
    assert record.boundary_layer_m is None
    assert record.temperature_c == 20.0


@respx.mock
def test_elevation_is_requested_in_batches(openmeteo):
    route = respx.get("https://api.open-meteo.com/v1/elevation").mock(
        return_value=httpx.Response(200, json={"elevation": [8.0] * 100})
    )

    elevations = openmeteo.fetch_elevations([(23.8, 90.4)] * 150)

    assert route.call_count == 2, "150 points must not go out as one request"
    assert len(elevations) == 150


@respx.mock
def test_geocoding_returns_places_a_user_can_pick(openmeteo):
    respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "Chattogram",
                        "country_code": "BD",
                        "latitude": 22.3569,
                        "longitude": 91.7832,
                        "elevation": 6.0,
                        "timezone": "Asia/Dhaka",
                        "population": 3920222,
                    }
                ]
            },
        )
    )

    [place] = openmeteo.search_places("Chittagong")

    assert place.name == "Chattogram"
    assert place.country == "BD"
    assert place.elevation_m == 6.0


@respx.mock
def test_wind_is_requested_in_metres_per_second(openmeteo):
    """Open-Meteo defaults to km/h. The field is named wind_speed_ms, so the
    unit has to be stated or every wind feature is 3.6x too large."""
    respx.get("https://air-quality-api.open-meteo.com/v1/air-quality").mock(
        return_value=httpx.Response(200, json={"hourly": {"time": [], "pm2_5": []}})
    )
    route = respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json={"hourly": {"time": []}})
    )

    openmeteo.fetch_forecast(23.81, 90.41)

    assert route.calls.last.request.url.params["wind_speed_unit"] == "ms"
