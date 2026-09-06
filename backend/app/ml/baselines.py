"""The three things the model has to beat.

Without these the headline metric is meaningless. A mean absolute error of
"12 ug/m3" says nothing on its own; "12 against persistence's 19" says
everything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.db.models import Estimator


def persistence(frame: pd.DataFrame) -> np.ndarray:
    """"It will be what it is now."

    The last observed value, carried forward. Deceptively strong at one to
    three hours, because pollution is highly autocorrelated. Any model that
    cannot beat this at short range is not earning its place.
    """

    return frame["pm25_lag_1"].to_numpy(dtype=float)


def climatology(frame: pd.DataFrame, hour_means: pd.Series) -> np.ndarray:
    """The station's own average for that hour of the day.

    Captures the daily cycle (rush hours, the nocturnal boundary layer
    collapse) for free, and nothing else.
    """

    keys = pd.MultiIndex.from_arrays(
        [frame["station_id"], frame["target_hour"].dt.hour], names=["station_id", "hour"]
    )
    values = hour_means.reindex(keys).to_numpy(dtype=float)
    return np.where(np.isnan(values), np.nanmean(hour_means.to_numpy()), values)


def cams(frame: pd.DataFrame) -> np.ndarray:
    """The Copernicus physics model, unmodified.

    This is the baseline that matters. The project's claim is that a cheap
    statistical layer on top of CAMS beats CAMS alone at station level, which
    is only a claim if this number is on the same chart.
    """

    return frame["cams_pm25"].to_numpy(dtype=float)


def hour_of_day_means(panel: pd.DataFrame) -> pd.Series:
    """Per station, per hour-of-day mean. Fitted on training data only."""

    working = panel.dropna(subset=["pm25"]).copy()
    working["hour_of_day"] = working["hour"].dt.hour
    return working.groupby(["station_id", "hour_of_day"])["pm25"].mean()


def all_baselines(frame: pd.DataFrame, hour_means: pd.Series) -> dict[str, np.ndarray]:
    return {
        Estimator.PERSISTENCE.value: persistence(frame),
        Estimator.CLIMATOLOGY.value: climatology(frame, hour_means),
        Estimator.CAMS.value: cams(frame),
    }
