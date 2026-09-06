from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.deps import SessionDep, SettingsDep
from app.ml.trainer import MODEL_NAME
from app.repositories.registry import ModelRepository
from app.schemas.common import HealthSchema

router = APIRouter(tags=["ops"])


@router.get("/healthz", response_model=HealthSchema, summary="Liveness")
def healthz(session: SessionDep, settings: SettingsDep) -> HealthSchema:
    """Includes a real database round trip, so a container that cannot reach
    Postgres is reported unhealthy rather than merely running."""

    session.execute(text("SELECT 1"))
    active = ModelRepository(session).active(MODEL_NAME)
    return HealthSchema(
        status="ok",
        database="ok",
        ground_truth_providers=settings.enabled_measurement_providers,
        active_model_version=active.version if active else None,
    )
