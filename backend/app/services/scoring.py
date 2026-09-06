"""The daily reckoning.

Yesterday's forecasts meet what actually happened. This is the whole basis of
the project's claim to "evolve": the ground truth arrives on its own, so the
scorecard cannot be gamed and does not depend on anyone rating anything.

The predictions being scored were written to the database *before* the
observation existed. Re-deriving them now with hindsight would produce much
prettier numbers and mean nothing.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict

import numpy as np
from sqlalchemy.orm import Session

from app.ml.evaluation import score
from app.repositories.forecasts import ForecastRepository
from app.repositories.registry import ModelRepository

logger = logging.getLogger(__name__)

#: Forecasts are kept this long after scoring, then dropped.
RETENTION_DAYS = 120


class ScoringService:
    def __init__(self, session: Session) -> None:
        self._forecasts = ForecastRepository(session)
        self._models = ModelRepository(session)

    def score_day(self, day: dt.date | None = None) -> int:
        """Score every forecast whose target hour fell on ``day``.

        Defaults to yesterday, because today is still incomplete and scoring a
        partial day would bias the numbers towards short horizons.
        """

        day = day or (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1))
        start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC)
        end = start + dt.timedelta(days=1)

        pairs = self._forecasts.due_for_scoring(start, end)
        if not pairs:
            logger.info("nothing to score for %s", day)
            return 0

        grouped: dict[tuple[int | None, str, int], list] = defaultdict(list)
        for row in pairs:
            grouped[(row.model_version_id, row.estimator, row.horizon_hours)].append(row)

        written = []
        for (version_id, estimator, horizon), rows in grouped.items():
            actual = np.array([row.actual for row in rows], dtype=float)
            predicted = np.array([row.pm25 for row in rows], dtype=float)
            lower = np.array(
                [np.nan if row.pm25_low is None else row.pm25_low for row in rows], float
            )
            upper = np.array(
                [np.nan if row.pm25_high is None else row.pm25_high for row in rows], float
            )

            metrics = score(actual, predicted, lower, upper)
            if metrics.sample_size == 0:
                continue

            written.append(
                {
                    "model_version_id": version_id,
                    "estimator": estimator,
                    "scored_on": day,
                    "horizon_hours": horizon,
                    "sample_size": metrics.sample_size,
                    "mae": metrics.mae,
                    "rmse": metrics.rmse,
                    "bias": metrics.bias,
                    "coverage_80": metrics.coverage_80,
                }
            )

        count = self._models.record_scores(written)
        logger.info("scored %s: %s estimator/horizon combinations", day, count)
        return count

    def prune(self) -> int:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=RETENTION_DAYS)
        removed = self._forecasts.prune(cutoff)
        if removed:
            logger.info("pruned %s forecasts older than %s days", removed, RETENTION_DAYS)
        return removed
