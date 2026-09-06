"""Metrics.

Four numbers, each answering a different question:

* **MAE** - how wrong, typically. The headline.
* **RMSE** - how wrong when it is wrong badly. Punishes the missed spike, which
  is the failure a user actually cares about.
* **Bias** - is it wrong in a *direction*. A forecaster that reads consistently
  low is dangerous in a way that a noisy one is not.
* **Coverage** - does the 80% band actually contain the truth 80% of the time.
  An interval nobody has checked is decoration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Metrics:
    sample_size: int
    mae: float
    rmse: float
    bias: float
    coverage_80: float | None = None
    #: Share of hours whose AQI category was called correctly.
    category_accuracy: float | None = None

    def as_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


def _clean(actual: np.ndarray, predicted: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drop pairs where either side is missing.

    A baseline with gaps must be scored on the rows it can answer, and the
    sample size is reported alongside so an unfair comparison is visible.
    """

    mask = ~(np.isnan(actual) | np.isnan(predicted))
    return actual[mask], predicted[mask]


def score(
    actual: np.ndarray,
    predicted: np.ndarray,
    lower: np.ndarray | None = None,
    upper: np.ndarray | None = None,
) -> Metrics:
    truth, estimate = _clean(np.asarray(actual, float), np.asarray(predicted, float))
    if truth.size == 0:
        return Metrics(sample_size=0, mae=float("nan"), rmse=float("nan"), bias=float("nan"))

    errors = estimate - truth
    coverage = None
    if lower is not None and upper is not None:
        mask = ~(
            np.isnan(np.asarray(actual, float))
            | np.isnan(np.asarray(predicted, float))
        )
        low, high = np.asarray(lower, float)[mask], np.asarray(upper, float)[mask]
        valid = ~(np.isnan(low) | np.isnan(high))
        if valid.any():
            inside = (truth[valid] >= low[valid]) & (truth[valid] <= high[valid])
            coverage = float(inside.mean())

    return Metrics(
        sample_size=int(truth.size),
        mae=float(np.mean(np.abs(errors))),
        rmse=float(np.sqrt(np.mean(errors**2))),
        bias=float(np.mean(errors)),
        coverage_80=coverage,
        category_accuracy=_category_accuracy(truth, estimate),
    )


def _category_accuracy(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Agreement on the AQI band, which is what a person actually acts on.

    Being 4 ug/m3 out matters enormously at the 9.0 boundary and not at all at
    200, and only this metric notices that.
    """

    from app.domain.aqi import Pollutant, aqi_from_concentration

    def band(value: float) -> str:
        result = aqi_from_concentration(float(value), Pollutant.PM25)
        return result.category.value if result else "unknown"

    matches = [band(a) == band(p) for a, p in zip(actual, predicted, strict=True)]
    return float(np.mean(matches)) if matches else 0.0


def score_by_horizon(
    horizons: np.ndarray,
    actual: np.ndarray,
    predicted: np.ndarray,
    lower: np.ndarray | None = None,
    upper: np.ndarray | None = None,
) -> dict[int, Metrics]:
    """Per-horizon breakdown. A single average hides the shape of the problem:
    everything is easy at one hour and hard at twenty-four."""

    out: dict[int, Metrics] = {}
    horizons = np.asarray(horizons)
    unique: list[int] = sorted({int(value) for value in horizons.ravel().tolist()})
    for horizon in unique:
        mask = horizons == horizon
        out[horizon] = score(
            np.asarray(actual)[mask],
            np.asarray(predicted)[mask],
            None if lower is None else np.asarray(lower)[mask],
            None if upper is None else np.asarray(upper)[mask],
        )
    return out
