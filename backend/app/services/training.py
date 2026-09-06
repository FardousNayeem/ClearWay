"""Training a candidate and deciding whether it deserves to serve.

Promotion is a gate, not a formality. A newly trained model replaces the
incumbent only if it beats every baseline on the holdout. A model that loses to
"it will be what it is now" has no business scheduling anyone's run.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ClearwayError
from app.db.models import ModelVersion
from app.ml import predictor
from app.ml.features import build_panel, build_supervised
from app.ml.trainer import MODEL_NAME, train
from app.repositories.ambient import AmbientRepository
from app.repositories.measurements import MeasurementRepository
from app.repositories.registry import ModelRepository

logger = logging.getLogger(__name__)


class TrainingService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._measurements = MeasurementRepository(session)
        self._ambient = AmbientRepository(session)
        self._models = ModelRepository(session)

    def train_candidate(
        self, window_days: int = 90, promote: bool = True, force: bool = False
    ) -> ModelVersion:
        end = dt.datetime.now(dt.UTC)
        start = end - dt.timedelta(days=window_days)

        measurements = self._measurements.training_rows("pm25", start, end)
        if len(measurements) < 2000:
            raise ClearwayError(
                f"Only {len(measurements)} observations in the last {window_days} days. "
                "Run a backfill before training."
            )

        station_ids = sorted({row.station_id for row in measurements})
        ambient = self._ambient.series(station_ids, start, end)

        panel = build_panel(
            [tuple(row) for row in measurements],
            [tuple(row) for row in ambient],
            pd.DataFrame(),
        )
        frame = build_supervised(panel, horizons=self._settings.forecast_horizons)

        version_number = self._models.next_version(MODEL_NAME)
        result = train(frame, panel, self._settings.model_dir, version_number)

        version = self._models.create(
            name=MODEL_NAME,
            version=version_number,
            algorithm=result.algorithm,
            trained_at=dt.datetime.now(dt.UTC),
            training_rows=result.training_rows,
            window_start=result.window_start,
            window_end=result.window_end,
            feature_names=result.feature_names,
            metrics=result.metrics,
            artifact_path=str(result.artifact_path),
            is_active=False,
            notes="",
        )

        incumbent = self._models.active(MODEL_NAME)
        should_promote = promote and (force or self._is_improvement(result, incumbent))

        if should_promote:
            self._models.promote(version)
            predictor.clear_cache()
            version.notes = (
                "promoted: beat every baseline on the holdout"
                if not force
                else "promoted: forced"
            )
            logger.info("promoted %s v%s", MODEL_NAME, version_number)
        else:
            version.notes = "trained but not promoted: did not beat the incumbent baselines"
            logger.warning(
                "v%s was not promoted; the incumbent keeps serving", version_number
            )

        return version

    def _is_improvement(
        self, result: object, incumbent: ModelVersion | None
    ) -> bool:
        """Two gates: beat the baselines, and do not regress on the incumbent."""

        if not result.beats_all_baselines:  # type: ignore[attr-defined]
            return False
        if incumbent is None:
            return True

        previous = (incumbent.metrics or {}).get("overall", {}).get("model", {}).get("mae")
        current = result.metrics["overall"]["model"]["mae"]  # type: ignore[attr-defined]
        if previous is None:
            return True
        # A small tolerance, because holdout windows differ between runs and
        # noise should not cause churn.
        return bool(current <= previous * 1.05)
