"""Turning stored rows into a model-ready matrix.

The whole design rests on one rule, and getting it wrong is the classic way to
build a forecaster that looks brilliant offline and is useless in production:

    A forecast issued at time ``t`` for time ``t + h`` may use
      * **observations** only up to and including ``t``
      * **CAMS and weather** at ``t + h``, because those are forecasts, and are
        genuinely available at issue time.

Anything else is leakage. The lag columns are therefore built from the
observation series alone, and the ambient columns are joined at the *target*
hour, never the issue hour.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.domain.timeframes import HOURS_IN_DAY

logger = logging.getLogger(__name__)

#: How far back the model is allowed to look at its own history.
LAGS: tuple[int, ...] = (1, 2, 3, 6, 12, 24)
ROLLING_WINDOWS: tuple[int, ...] = (6, 24)

AMBIENT_COLUMNS: tuple[str, ...] = (
    "cams_pm25",
    "cams_pm10",
    "temperature_c",
    "relative_humidity",
    "wind_speed_ms",
    "wind_direction_deg",
    "precipitation_mm",
    "pressure_hpa",
    "boundary_layer_m",
)

TARGET = "target_pm25"


@dataclass(frozen=True, slots=True)
class SupervisedFrame:
    X: pd.DataFrame
    y: pd.Series
    #: Kept out of X but needed to split by time and to report per-horizon.
    meta: pd.DataFrame

    @property
    def feature_names(self) -> list[str]:
        return list(self.X.columns)


def build_panel(
    measurements: list[tuple], ambient: list[tuple], stations: pd.DataFrame
) -> pd.DataFrame:
    """A gap-free hourly panel, one row per station-hour.

    Reindexing onto a complete hourly range matters: without it, ``shift(1)``
    means "the previous row", which after a six-hour outage is a six-hour lag
    wearing a one-hour label.
    """

    if not measurements:
        return pd.DataFrame()

    obs = pd.DataFrame(
        measurements, columns=["station_id", "observed_at", "pm25", "latitude",
                               "longitude", "elevation_m", "city_slug"]
    )
    obs["observed_at"] = pd.to_datetime(obs["observed_at"], utc=True)

    amb = pd.DataFrame(ambient, columns=["station_id", "valid_at", *AMBIENT_COLUMNS])
    if not amb.empty:
        amb["valid_at"] = pd.to_datetime(amb["valid_at"], utc=True)

    frames = []
    for station_id, group in obs.groupby("station_id", sort=True):
        group = group.sort_values("observed_at")
        full_index = pd.date_range(
            group["observed_at"].min(), group["observed_at"].max(), freq="h", tz="UTC"
        )
        series = (
            group.set_index("observed_at")[["pm25"]]
            .reindex(full_index)
            .rename_axis("hour")
        )
        series["station_id"] = station_id
        for column in ("latitude", "longitude", "elevation_m", "city_slug"):
            series[column] = group[column].iloc[0]
        frames.append(series.reset_index())

    panel = pd.concat(frames, ignore_index=True)

    if not amb.empty:
        panel = panel.merge(
            amb, left_on=["station_id", "hour"], right_on=["station_id", "valid_at"], how="left"
        ).drop(columns=["valid_at"])
    else:
        for column in AMBIENT_COLUMNS:
            panel[column] = np.nan

    logger.info(
        "panel: %s station-hours across %s stations",
        len(panel),
        panel["station_id"].nunique(),
    )
    return panel


def _history_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Lags and rolling statistics of the observation series.

    Computed per station on the gap-free index, so every lag is a true offset
    in hours. ``min_periods=2`` on the rolling windows stops a single reading
    from becoming a confident-looking mean.
    """

    out = panel.copy()
    grouped = out.groupby("station_id")["pm25"]

    for lag in LAGS:
        out[f"pm25_lag_{lag}"] = grouped.shift(lag)

    for window in ROLLING_WINDOWS:
        shifted = grouped.shift(1)  # never include the current hour
        rolled = shifted.groupby(out["station_id"]).rolling(window, min_periods=2)
        out[f"pm25_roll_{window}_mean"] = rolled.mean().reset_index(level=0, drop=True)
        out[f"pm25_roll_{window}_std"] = rolled.std().reset_index(level=0, drop=True)

    # How fast it is moving, and in which direction.
    out["pm25_delta_1"] = out["pm25_lag_1"] - out["pm25_lag_2"]
    out["pm25_delta_3"] = out["pm25_lag_1"] - out["pm25_lag_3"]
    return out


def _cyclical_and_wind(frame: pd.DataFrame, hour_column: str) -> pd.DataFrame:
    """Calendar and wind encodings, applied at the *target* hour."""

    out = frame
    hours = out[hour_column].dt.hour
    weekdays = out[hour_column].dt.weekday

    out["hour_sin"] = np.sin(2 * np.pi * hours / HOURS_IN_DAY)
    out["hour_cos"] = np.cos(2 * np.pi * hours / HOURS_IN_DAY)
    out["dow_sin"] = np.sin(2 * np.pi * weekdays / 7)
    out["dow_cos"] = np.cos(2 * np.pi * weekdays / 7)
    out["is_weekend"] = (weekdays >= 5).astype(float)

    radians = np.deg2rad(out["wind_direction_deg"])
    out["wind_u"] = -out["wind_speed_ms"] * np.sin(radians)
    out["wind_v"] = -out["wind_speed_ms"] * np.cos(radians)
    return out


FEATURE_COLUMNS: tuple[str, ...] = (
    *(f"pm25_lag_{lag}" for lag in LAGS),
    *(f"pm25_roll_{w}_{stat}" for w in ROLLING_WINDOWS for stat in ("mean", "std")),
    "pm25_delta_1",
    "pm25_delta_3",
    "cams_pm25",
    "cams_pm10",
    "temperature_c",
    "relative_humidity",
    "precipitation_mm",
    "pressure_hpa",
    "boundary_layer_m",
    "wind_u",
    "wind_v",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "is_weekend",
    "elevation_m",
    "latitude",
    "longitude",
    "horizon",
)


def build_supervised(
    panel: pd.DataFrame,
    horizons: int = 24,
    *,
    max_rows: int | None = 400_000,
    random_state: int = 20260906,
) -> SupervisedFrame:
    """Stack one training row per (station, issue hour, horizon).

    ``max_rows`` caps the matrix by random subsample. Training has to stay in
    the seconds-to-a-minute range on a laptop CPU, and past a few hundred
    thousand rows this problem stops improving anyway.
    """

    if panel.empty:
        return SupervisedFrame(pd.DataFrame(), pd.Series(dtype=float), pd.DataFrame())

    history = _history_features(panel)
    # Only the lag columns and identity travel from the issue hour.
    issue_columns = [
        "station_id",
        "hour",
        "latitude",
        "longitude",
        "elevation_m",
        *(f"pm25_lag_{lag}" for lag in LAGS),
        *(f"pm25_roll_{w}_{s}" for w in ROLLING_WINDOWS for s in ("mean", "std")),
        "pm25_delta_1",
        "pm25_delta_3",
    ]
    issue = history[issue_columns].rename(columns={"hour": "issue_hour"})

    # Everything else is taken at the target hour.
    target_columns = ["station_id", "hour", "pm25", *AMBIENT_COLUMNS]
    target = history[target_columns].rename(
        columns={"hour": "target_hour", "pm25": TARGET}
    )

    blocks = []
    for horizon in range(1, horizons + 1):
        shifted = issue.copy()
        shifted["target_hour"] = shifted["issue_hour"] + pd.Timedelta(hours=horizon)
        shifted["horizon"] = horizon
        blocks.append(shifted.merge(target, on=["station_id", "target_hour"], how="inner"))

    stacked = pd.concat(blocks, ignore_index=True)
    stacked = stacked.dropna(subset=[TARGET, "pm25_lag_1"])

    if max_rows and len(stacked) > max_rows:
        logger.info("subsampling %s rows down to %s", len(stacked), max_rows)
        stacked = stacked.sample(max_rows, random_state=random_state)

    stacked = _cyclical_and_wind(stacked, "target_hour")

    meta = stacked[["station_id", "issue_hour", "target_hour", "horizon", "cams_pm25"]].copy()
    X = stacked[list(FEATURE_COLUMNS)].astype(float)
    y = stacked[TARGET].astype(float)

    logger.info("supervised frame: %s rows, %s features", len(X), X.shape[1])
    return SupervisedFrame(X=X, y=y, meta=meta)


def build_inference_rows(
    panel: pd.DataFrame, station_id: int, issue_hour: dt.datetime, horizons: int
) -> pd.DataFrame:
    """Feature rows for one live forecast run.

    Built through exactly the same code path as training, which is the only
    reliable way to stop training and serving drifting apart.
    """

    history = _history_features(panel)
    station = history[history["station_id"] == station_id]
    if station.empty:
        return pd.DataFrame()

    issue_row = station[station["hour"] == pd.Timestamp(issue_hour)]
    if issue_row.empty:
        # Fall back to the most recent hour we actually hold.
        issue_row = station.tail(1)
    if issue_row.empty or pd.isna(issue_row.iloc[0].get("pm25_lag_1")):
        return pd.DataFrame()

    base = issue_row.iloc[0]
    issue_hour_actual = base["hour"]

    rows = []
    for horizon in range(1, horizons + 1):
        target_hour = issue_hour_actual + pd.Timedelta(hours=horizon)
        forward = station[station["hour"] == target_hour]
        ambient = (
            forward.iloc[0][list(AMBIENT_COLUMNS)]
            if not forward.empty
            else pd.Series({column: np.nan for column in AMBIENT_COLUMNS})
        )
        row = {
            "station_id": station_id,
            "issue_hour": issue_hour_actual,
            "target_hour": target_hour,
            "horizon": horizon,
            "latitude": base["latitude"],
            "longitude": base["longitude"],
            "elevation_m": base["elevation_m"],
            **{
                column: base[column]
                for column in issue_row.columns
                if column.startswith("pm25_lag_")
                or column.startswith("pm25_roll_")
                or column.startswith("pm25_delta_")
            },
            **ambient.to_dict(),
        }
        rows.append(row)

    frame = pd.DataFrame(rows)
    return _cyclical_and_wind(frame, "target_hour")
