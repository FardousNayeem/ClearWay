"""The HTTP surface.

These run against a real Postgres in a rolled-back transaction, because the
queries under test use Postgres-specific upserts and partial indexes that
SQLite would not exercise.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx


def test_health_reports_the_database_and_the_configured_providers(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert set(body["ground_truth_providers"]) == {"openaq", "waqi"}
    assert body["active_model_version"] is None


def test_the_configured_cities_are_listed(client):
    response = client.get("/api/v1/cities")

    slugs = [city["slug"] for city in response.json()]
    assert response.status_code == 200
    assert {"dhaka", "chattogram", "delhi", "beijing", "moscow", "berlin", "london"} <= set(
        slugs
    )


# --- nowcast -------------------------------------------------------------


def test_a_nowcast_blends_nearby_stations_by_distance(
    client, station_factory, measurement_factory
):
    """Both stations are offset from the query point, so the blend is visible.
    The result must sit between the two readings and lean towards the closer."""
    near = station_factory(name="Near", latitude=23.83, longitude=90.43)  # ~3 km
    far = station_factory(name="Far", latitude=23.95, longitude=90.55)  # ~21 km
    measurement_factory(near, 60.0, minutes_ago=20)
    measurement_factory(far, 20.0, minutes_ago=20)

    response = client.get("/api/v1/air/now", params={"latitude": 23.81, "longitude": 90.41})
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "stations"
    assert body["station_count"] == 2
    assert 20 < body["pm25"] < 60, "the estimate must be a blend, not a pick"
    assert body["pm25"] > 40, "the closer station should carry more weight"
    weights = {c["name"]: c["weight"] for c in body["contributions"]}
    assert weights["Near"] > weights["Far"]
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)


def test_a_station_on_the_query_point_dominates_without_dividing_by_zero(
    client, station_factory, measurement_factory
):
    """Zero distance must not produce an infinite weight, but a monitor sitting
    on the spot should still effectively decide the answer."""
    here = station_factory(name="Here", latitude=23.81, longitude=90.41)
    elsewhere = station_factory(name="Elsewhere", latitude=23.95, longitude=90.55)
    measurement_factory(here, 60.0, minutes_ago=20)
    measurement_factory(elsewhere, 20.0, minutes_ago=20)

    body = client.get(
        "/api/v1/air/now", params={"latitude": 23.81, "longitude": 90.41}
    ).json()

    assert body["pm25"] == pytest.approx(60.0, abs=0.5)
    assert body["aqi"]["category"] == "unhealthy"


def test_every_nowcast_carries_provenance(client, station_factory, measurement_factory):
    """A number a person acts on has to say where it came from."""
    station = station_factory(name="Gulshan")
    measurement_factory(station, 42.0, minutes_ago=30)

    body = client.get(
        "/api/v1/air/now", params={"latitude": 23.81, "longitude": 90.41}
    ).json()

    [contribution] = body["contributions"]
    assert contribution["name"] == "Gulshan"
    assert contribution["provider"] == "openaq"
    assert contribution["distance_km"] >= 0
    assert 0 <= contribution["age_minutes"] <= 120
    assert contribution["weight"] == 1.0


def test_a_stale_reading_is_not_presented_as_now(
    client, station_factory, measurement_factory
):
    """Six hours old from 40 km away, shown as 'now', is worse than a fallback."""
    station = station_factory()
    measurement_factory(station, 55.0, minutes_ago=600)

    with respx.mock:
        respx.get("https://air-quality-api.open-meteo.com/v1/air-quality").mock(
            return_value=httpx.Response(
                200,
                json={
                    "hourly": {
                        "time": [_hour_iso()],
                        "pm2_5": [30.0],
                        "pm10": [44.0],
                    }
                },
            )
        )
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=httpx.Response(200, json={"hourly": {"time": [_hour_iso()]}})
        )
        body = client.get(
            "/api/v1/air/now", params={"latitude": 23.81, "longitude": 90.41}
        ).json()

    assert body["source"] == "cams", "a stale station must not be reported as current"
    assert body["station_count"] == 0


def test_nowhere_near_a_station_falls_back_to_the_physics_model(client):
    with respx.mock:
        respx.get("https://air-quality-api.open-meteo.com/v1/air-quality").mock(
            return_value=httpx.Response(
                200, json={"hourly": {"time": [_hour_iso()], "pm2_5": [12.4]}}
            )
        )
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=httpx.Response(200, json={"hourly": {"time": [_hour_iso()]}})
        )
        response = client.get(
            "/api/v1/air/now", params={"latitude": -33.87, "longitude": 151.21}
        )

    assert response.status_code == 200
    assert response.json()["source"] == "cams"


def test_impossible_coordinates_are_rejected_in_the_shared_envelope(client):
    response = client.get("/api/v1/air/now", params={"latitude": 200, "longitude": 0})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "latitude" in body["error"]["details"]
    assert body["error"]["request_id"]


def test_the_request_id_is_echoed_for_log_correlation(client):
    response = client.get("/healthz", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


# --- stations ------------------------------------------------------------


def test_stations_can_be_filtered_by_city(client, station_factory):
    station_factory(city_slug="dhaka", name="Dhaka one")
    station_factory(city_slug="london", name="London one", latitude=51.5, longitude=-0.12)

    body = client.get("/api/v1/stations", params={"city": "london"}).json()

    assert [s["name"] for s in body] == ["London one"]


def test_stations_can_be_filtered_by_bounding_box(client, station_factory):
    station_factory(name="Inside", latitude=23.81, longitude=90.41)
    station_factory(name="Outside", latitude=28.61, longitude=77.20, city_slug="delhi")

    body = client.get("/api/v1/stations", params={"bbox": "90.0,23.5,91.0,24.0"}).json()

    assert [s["name"] for s in body] == ["Inside"]


def test_a_malformed_bounding_box_explains_the_expected_format(client):
    response = client.get("/api/v1/stations", params={"bbox": "1,2,3"})

    assert response.status_code == 422
    assert "west,south,east,north" in response.json()["error"]["message"]


def test_a_station_with_no_fresh_reading_still_appears_but_without_a_value(
    client, station_factory, measurement_factory
):
    station = station_factory(name="Quiet")
    measurement_factory(station, 40.0, minutes_ago=900)

    [row] = client.get("/api/v1/stations", params={"city": "dhaka"}).json()

    assert row["name"] == "Quiet"
    assert row["pm25"] is None
    assert row["aqi"] is None


def test_an_unknown_station_is_a_clean_404(client):
    response = client.get("/api/v1/stations/424242/nearby")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- models --------------------------------------------------------------


def test_the_scorecard_is_empty_but_valid_before_any_training(client):
    response = client.get("/api/v1/models/scorecard")

    assert response.status_code == 200
    body = response.json()
    assert body["active_version"] is None
    assert body["by_horizon"] == {}
    assert "persistence" in body["estimators"]


def test_asking_for_the_active_model_before_one_exists_is_a_404(client):
    response = client.get("/api/v1/models/active")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_openapi_schema_builds(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/air/now" in response.json()["paths"]


def _hour_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M"
    )
