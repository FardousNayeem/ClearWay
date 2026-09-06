"""The scorecard.

This is the endpoint the whole project is arguing for: it shows how every
model version has performed against persistence, climatology and raw CAMS, on
predictions that were written before the outcome was known.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from fastapi import APIRouter, Query

from app.core.deps import SessionDep
from app.core.errors import NotFoundError
from app.db.models import Estimator
from app.ml.trainer import MODEL_NAME
from app.repositories.registry import ModelRepository
from app.schemas.common import ModelVersionSchema, ScoreSchema

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelVersionSchema], summary="Version history")
def versions(session: SessionDep, limit: int = Query(30, ge=1, le=200)) -> list:
    return ModelRepository(session).history(MODEL_NAME, limit)


@router.get("/active", response_model=ModelVersionSchema, summary="Serving version")
def active(session: SessionDep) -> object:
    version = ModelRepository(session).active(MODEL_NAME)
    if version is None:
        raise NotFoundError("No model has been promoted yet.")
    return version


@router.get("/scores", response_model=list[ScoreSchema], summary="Daily accuracy")
def scores(
    session: SessionDep,
    days: int = Query(30, ge=1, le=365),
    model_version_id: int | None = None,
) -> list:
    since = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days)
    return ModelRepository(session).scores(since, model_version_id)


@router.get("/scorecard", summary="Us against the baselines, per horizon")
def scorecard(session: SessionDep, days: int = Query(30, ge=1, le=365)) -> dict:
    """Averaged over the window, so a single bad day does not dominate.

    Sample sizes are returned alongside because a baseline with gaps is scored
    on fewer rows, and an unfair comparison should be visible rather than
    hidden behind a tidy number.
    """

    repository = ModelRepository(session)
    since = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days)
    rows = repository.scores(since)

    aggregate: dict[str, dict[int, dict[str, float | int | None]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    weights: dict[tuple[str, int], int] = defaultdict(int)
    totals: dict[tuple[str, int], dict[str, float]] = defaultdict(
        lambda: {"mae": 0.0, "rmse": 0.0, "bias": 0.0, "coverage_80": 0.0, "covered": 0}
    )

    for row in rows:
        key = (row.estimator, row.horizon_hours)
        weights[key] += row.sample_size
        bucket = totals[key]
        bucket["mae"] += row.mae * row.sample_size
        bucket["rmse"] += row.rmse * row.sample_size
        bucket["bias"] += row.bias * row.sample_size
        if row.coverage_80 is not None:
            bucket["coverage_80"] += row.coverage_80 * row.sample_size
            bucket["covered"] += row.sample_size

    for (estimator, horizon), bucket in totals.items():
        n = weights[(estimator, horizon)]
        if n == 0:
            continue
        aggregate[estimator][horizon] = {
            "mae": round(bucket["mae"] / n, 3),
            "rmse": round(bucket["rmse"] / n, 3),
            "bias": round(bucket["bias"] / n, 3),
            "sample_size": n,
            "coverage_80": (
                round(bucket["coverage_80"] / bucket["covered"], 3)
                if bucket["covered"]
                else None
            ),
        }

    active_version = repository.active(MODEL_NAME)
    return {
        "window_days": days,
        "active_version": active_version.version if active_version else None,
        "estimators": [e.value for e in Estimator],
        "by_horizon": {estimator: dict(sorted(v.items())) for estimator, v in aggregate.items()},
    }
