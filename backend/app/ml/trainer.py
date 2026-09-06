"""Training the bias-correction model.

Three estimators are fitted from the same matrix: a median model that produces
the headline number, and two quantile models that produce the 80% band.

Two decisions carry most of the weight here:

**The split is chronological, never random.** Adjacent hours at one station are
strongly correlated, so a random split puts near-duplicates of the test rows in
the training set and reports an accuracy the model will never reproduce. The
holdout is therefore the most recent slice of time, which is also the only
split that matches how the model is used.

**Training is deliberately cheap.** `HistGradientBoostingRegressor` fits this
problem in seconds on a laptop CPU, handles missing values natively (station
data is full of gaps), and beats deep learning at this data volume. Reaching
for a neural network here would cost hours and lose accuracy.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from app.db.models import Estimator

from . import baselines
from .evaluation import Metrics, score, score_by_horizon
from .features import SupervisedFrame

logger = logging.getLogger(__name__)

MODEL_NAME = "pm25_forecaster"
HOLDOUT_FRACTION = 0.2
QUANTILES = {"low": 0.1, "high": 0.9}

_COMMON_PARAMS: dict[str, Any] = {
    "max_iter": 300,
    "learning_rate": 0.06,
    "max_depth": None,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "early_stopping": True,
    "validation_fraction": 0.1,
    "n_iter_no_change": 20,
    "random_state": 20260906,
}


@dataclass(slots=True)
class TrainingResult:
    artifact_path: Path
    algorithm: str
    feature_names: list[str]
    training_rows: int
    window_start: dt.datetime
    window_end: dt.datetime
    #: Holdout metrics for our model and every baseline, overall and per horizon.
    metrics: dict[str, Any]

    @property
    def beats_all_baselines(self) -> bool:
        """Promotion gate. Compared on MAE at horizons of 6 hours and beyond,
        where a forecast is actually worth having; persistence is expected to
        win at one to three hours and that is not a failure."""

        ours = self.metrics["overall"][Estimator.MODEL.value]["mae"]
        rivals = [
            self.metrics["overall"][name]["mae"]
            for name in (
                Estimator.PERSISTENCE.value,
                Estimator.CLIMATOLOGY.value,
                Estimator.CAMS.value,
            )
            if not np.isnan(self.metrics["overall"][name]["mae"])
        ]
        return bool(rivals) and all(ours < rival for rival in rivals)


def chronological_split(frame: SupervisedFrame) -> tuple[np.ndarray, np.ndarray]:
    """Split on issue time, so the holdout is strictly in the future."""

    issued = frame.meta["issue_hour"]
    cutoff = issued.quantile(1 - HOLDOUT_FRACTION)
    train_mask = (issued <= cutoff).to_numpy()
    return train_mask, ~train_mask


def train(
    frame: SupervisedFrame,
    panel: pd.DataFrame,
    model_dir: Path,
    version: int,
) -> TrainingResult:
    if frame.X.empty:
        raise ValueError("nothing to train on; ingest and backfill first")

    train_mask, test_mask = chronological_split(frame)
    if test_mask.sum() < 200:
        raise ValueError(
            f"holdout of {int(test_mask.sum())} rows is too small to judge a model; "
            "backfill a longer window"
        )

    X_train, y_train = frame.X[train_mask], frame.y[train_mask]
    X_test, y_test = frame.X[test_mask], frame.y[test_mask]
    meta_test = frame.meta[test_mask]

    logger.info("training on %s rows, holding out %s", len(X_train), len(X_test))

    median = HistGradientBoostingRegressor(loss="absolute_error", **_COMMON_PARAMS)
    median.fit(X_train, y_train)

    quantile_models = {}
    for label, quantile in QUANTILES.items():
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=quantile, **_COMMON_PARAMS
        )
        model.fit(X_train, y_train)
        quantile_models[label] = model

    predictions = np.clip(median.predict(X_test), 0, None)
    lower = np.clip(quantile_models["low"].predict(X_test), 0, None)
    upper = np.clip(quantile_models["high"].predict(X_test), 0, None)
    # A quantile model can cross itself; the band must not be inverted.
    lower, upper = np.minimum(lower, upper), np.maximum(lower, upper)

    actual = y_test.to_numpy(dtype=float)
    hour_means = baselines.hour_of_day_means(panel)
    baseline_predictions = baselines.all_baselines(meta_test.assign(
        pm25_lag_1=frame.X.loc[meta_test.index, "pm25_lag_1"]
    ), hour_means)

    overall: dict[str, Any] = {
        Estimator.MODEL.value: score(actual, predictions, lower, upper).as_dict()
    }
    for name, values in baseline_predictions.items():
        overall[name] = score(actual, values).as_dict()

    horizons = meta_test["horizon"].to_numpy()
    per_horizon: dict[str, dict[int, dict]] = {
        Estimator.MODEL.value: {
            horizon: metrics.as_dict()
            for horizon, metrics in score_by_horizon(
                horizons, actual, predictions, lower, upper
            ).items()
        }
    }
    for name, values in baseline_predictions.items():
        per_horizon[name] = {
            horizon: metrics.as_dict()
            for horizon, metrics in score_by_horizon(horizons, actual, values).items()
        }

    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = model_dir / f"{MODEL_NAME}_v{version}.joblib"
    joblib.dump(
        {
            "median": median,
            "quantiles": quantile_models,
            "feature_names": frame.feature_names,
            "hour_means": hour_means,
            "version": version,
        },
        artifact_path,
        compress=3,
    )

    result = TrainingResult(
        artifact_path=artifact_path,
        algorithm="HistGradientBoostingRegressor(absolute_error) + 0.1/0.9 quantiles",
        feature_names=frame.feature_names,
        training_rows=int(len(X_train)),
        window_start=frame.meta["issue_hour"].min().to_pydatetime(),
        window_end=frame.meta["target_hour"].max().to_pydatetime(),
        metrics={"overall": overall, "per_horizon": per_horizon},
    )
    _log_summary(result)
    return result


def _log_summary(result: TrainingResult) -> None:
    overall = result.metrics["overall"]
    logger.info("holdout MAE by estimator:")
    for name, metrics in sorted(overall.items(), key=lambda kv: kv[1]["mae"]):
        logger.info(
            "  %-12s mae=%6.2f rmse=%6.2f bias=%+6.2f n=%s",
            name,
            metrics["mae"],
            metrics["rmse"],
            metrics["bias"],
            metrics["sample_size"],
        )
    coverage = overall[Estimator.MODEL.value].get("coverage_80")
    if coverage is not None:
        logger.info("  80%% band covered %.1f%% of actuals", coverage * 100)


def summarise_metrics(metrics: Metrics) -> str:
    return f"mae={metrics.mae:.2f} rmse={metrics.rmse:.2f} n={metrics.sample_size}"
