"""Loading a trained artefact and producing a forecast.

Feature rows are built by the same code the trainer used, so training and
serving cannot drift apart. The loaded artefact carries its own feature-name
list and the column order is asserted on every call, which turns a silent
misalignment into a loud failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.core.errors import ModelUnavailableError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Prediction:
    horizon: int
    target_hour: pd.Timestamp
    pm25: float
    pm25_low: float
    pm25_high: float
    cams_pm25: float | None


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    import joblib

    if not Path(path).exists():
        raise ModelUnavailableError(f"Model artefact is missing at {path}.")
    logger.info("loading model artefact %s", path)
    return joblib.load(path)


def clear_cache() -> None:
    """Called after a promotion so the next request picks up the new version."""
    _load.cache_clear()


def predict(artifact_path: str, rows: pd.DataFrame) -> list[Prediction]:
    if rows.empty:
        return []

    bundle = _load(artifact_path)
    feature_names: list[str] = bundle["feature_names"]

    missing = [name for name in feature_names if name not in rows.columns]
    if missing:
        raise ModelUnavailableError(
            f"Feature mismatch between the artefact and the live pipeline: {missing}"
        )

    X = rows[feature_names].astype(float)

    centre = np.clip(bundle["median"].predict(X), 0, None)
    low = np.clip(bundle["quantiles"]["low"].predict(X), 0, None)
    high = np.clip(bundle["quantiles"]["high"].predict(X), 0, None)
    low, high = np.minimum(low, high), np.maximum(low, high)

    return [
        Prediction(
            horizon=int(row.horizon),
            target_hour=row.target_hour,
            pm25=round(float(centre[index]), 2),
            pm25_low=round(float(low[index]), 2),
            pm25_high=round(float(high[index]), 2),
            cams_pm25=(
                None if pd.isna(row.cams_pm25) else round(float(row.cams_pm25), 2)
            ),
        )
        for index, row in enumerate(rows.itertuples())
    ]
