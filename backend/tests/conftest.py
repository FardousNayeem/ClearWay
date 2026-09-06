from __future__ import annotations

import datetime as dt
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "CLEARWAY_TEST_DATABASE_URL",
    "postgresql+psycopg://clearway:clearway@127.0.0.1:5436/clearway_test",
)


@pytest.fixture(scope="session")
def engine():
    from app.db.models import Base

    engine = create_engine(TEST_DATABASE_URL)
    # The schema is created from the models rather than by running migrations,
    # because these tests are about behaviour; migrations get their own check
    # in CI via `alembic upgrade head` against a clean database.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Session:
    """Each test runs inside a transaction that is rolled back afterwards, so
    tests cannot see or corrupt each other's data."""

    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def settings():
    from app.core.config import Settings

    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        openaq_api_key="test-openaq-key",
        waqi_api_token="test-waqi-token",
        nowcast_max_distance_km=30.0,
        nowcast_max_age_minutes=180,
    )


@pytest.fixture
def client(session, settings) -> TestClient:
    """An app whose session and settings are the test ones."""

    from app.core.config import get_settings
    from app.core.database import get_session
    from app.main import create_app

    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def station_factory(session):
    from app.db.models import Station

    created = []

    def _make(**overrides):
        index = len(created) + 1
        station = Station(
            provider=overrides.pop("provider", "openaq"),
            external_id=overrides.pop("external_id", f"ext-{index}"),
            name=overrides.pop("name", f"Station {index}"),
            city_slug=overrides.pop("city_slug", "dhaka"),
            country=overrides.pop("country", "BD"),
            latitude=overrides.pop("latitude", 23.81),
            longitude=overrides.pop("longitude", 90.41),
            is_active=overrides.pop("is_active", True),
            sensor_ids=overrides.pop("sensor_ids", {"pm25": "1"}),
            **overrides,
        )
        session.add(station)
        session.flush()
        created.append(station)
        return station

    return _make


@pytest.fixture
def measurement_factory(session):
    from app.db.models import Measurement

    def _make(station, value: float, minutes_ago: int = 20, parameter: str = "pm25"):
        observed = (
            dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes_ago)
        ).replace(minute=0, second=0, microsecond=0)
        row = Measurement(
            station_id=station.id,
            parameter=parameter,
            observed_at=observed,
            value=value,
        )
        session.add(row)
        session.flush()
        return row

    return _make
