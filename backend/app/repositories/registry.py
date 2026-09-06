from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from app.db.models import ModelScore, ModelVersion

from .base import Repository, upsert_many


class ModelRepository(Repository):
    """Model versions and their scores.

    Promotion is deliberately a database operation rather than a file on disk:
    the partial unique index on ``(name) where is_active`` means the database
    itself refuses to let two versions of one model serve traffic at once.
    """

    def next_version(self, name: str) -> int:
        highest = self.session.scalar(
            select(func.max(ModelVersion.version)).where(ModelVersion.name == name)
        )
        return (highest or 0) + 1

    def create(self, **fields: object) -> ModelVersion:
        version = ModelVersion(**fields)
        self.session.add(version)
        self.session.flush()
        return version

    def active(self, name: str) -> ModelVersion | None:
        return self.session.scalar(
            select(ModelVersion).where(
                ModelVersion.name == name, ModelVersion.is_active.is_(True)
            )
        )

    def get(self, version_id: int) -> ModelVersion | None:
        return self.session.get(ModelVersion, version_id)

    def history(self, name: str | None = None, limit: int = 50) -> list[ModelVersion]:
        query = select(ModelVersion).order_by(ModelVersion.trained_at.desc()).limit(limit)
        if name:
            query = query.where(ModelVersion.name == name)
        return list(self.session.scalars(query))

    def promote(self, version: ModelVersion) -> None:
        """Make this the serving version, standing the incumbent down first."""
        incumbent = self.active(version.name)
        if incumbent is not None and incumbent.id != version.id:
            incumbent.is_active = False
            self.session.flush()
        version.is_active = True

    def record_scores(self, rows: list[dict]) -> int:
        return upsert_many(
            self.session,
            ModelScore,
            rows,
            conflict_columns=["model_version_id", "estimator", "scored_on", "horizon_hours"],
            update_columns=["sample_size", "mae", "rmse", "bias", "coverage_80"],
        )

    def scores(
        self, since: dt.date, model_version_id: int | None = None
    ) -> list[ModelScore]:
        query = select(ModelScore).where(ModelScore.scored_on >= since)
        if model_version_id is not None:
            query = query.where(ModelScore.model_version_id == model_version_id)
        return list(
            self.session.scalars(
                query.order_by(ModelScore.scored_on, ModelScore.horizon_hours)
            )
        )
