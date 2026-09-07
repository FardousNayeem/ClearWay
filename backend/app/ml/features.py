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

The rule is what constrains the spatial features too: they read *other*
stations' observations, but only at ``t``, never later. A neighbour's reading
at ``t`` is as legitimately available at issue time as the station's own.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.domain import spatial
from app.domain.geo import Point
from app.domain.timeframes import HOURS_IN_DAY

logger = logging.getLogger(__name__)

#: How far back the model is allowed to look at its own history.
LAGS: tuple[int, ...] = (1, 2, 3, 6, 12, 24)
ROLLING_WINDOWS: tuple[int, ...] = (6, 24)

#: Spatial features, computed once per (station, hour) from every *other*
#: active station in the same city. See `_spatial_features` and
#: `app.domain.spatial` for what each one means.
SPATIAL_COLUMNS: tuple[str, ...] = (
    "spatial_bias_krige",
    "spatial_bias_krige_var",
    "spatial_neighbours",
    "spatial_bias_clustering",
)

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


def _spatial_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Leave-one-out ordinary kriging of the CAMS bias field, and how
    spatially clustered that field is, per city, per hour.

    Every other feature in this module describes a station's *own* past. This
    is the only one that looks sideways: an hour ago, what were the stations
    around it saying, and did that even hang together spatially? That is
    information no amount of per-station history can recover, and it is
    exactly the shape of the error a 40 km CAMS cell makes - systematic and
    spatially structured rather than random (see the README's framing of
    Model Output Statistics).

    "Leave-one-out": a station's own spatial features are built only from
    *other* stations. A station cannot corroborate itself, and leave-one-out
    is also what lets this same function serve a future virtual-sensor
    interpolator, which never has its own reading to begin with.

    The field kriged is ``pm25 - cams_pm25``: how much CAMS is missing at each
    station that reported. Like every other observation-derived feature here
    it is then **shifted by one hour**, so the freshest reading any feature
    carries is ``t - 1``. That matches the lag columns, and it matters
    operationally rather than only theoretically: station data for the current
    hour has usually not landed by the time the hourly job runs, so a feature
    built on ``t`` would be dense in training, where the backfill is complete,
    and sparse in production. That is the classic way a feature quietly stops
    meaning the same thing at serving time.
    """

    empty = {column: np.nan for column in SPATIAL_COLUMNS}
    if panel.empty or "city_slug" not in panel.columns:
        return panel.assign(**empty)

    bias = (panel["pm25"] - panel["cams_pm25"]).rename("bias")
    tagged = pd.concat([panel[["station_id", "hour", "city_slug"]], bias], axis=1)

    rows: list[dict[str, float | int]] = []
    for _, city_frame in tagged.groupby("city_slug"):
        coordinates = (
            panel.loc[city_frame.index].drop_duplicates("station_id").set_index("station_id")
        )
        station_ids = list(coordinates.index)
        # Leave-one-out, so a city needs one more station than the kriging
        # floor before any of its stations can have a spatial feature at all.
        if len(station_ids) <= spatial.MIN_NEIGHBOURS_TO_KRIGE:
            continue

        points = [
            Point(coordinates.loc[sid, "latitude"], coordinates.loc[sid, "longitude"])
            for sid in station_ids
        ]
        distance = spatial.pairwise_distance_km(points)
        position = {station_id: index for index, station_id in enumerate(station_ids)}

        # Station coordinates do not change, so the distance matrix above is
        # built once per city and only indexed into per hour.
        pivot = city_frame.pivot_table(index="hour", columns="station_id", values="bias")
        for hour, values_at_hour in pivot.iterrows():
            available = values_at_hour.dropna()
            if len(available) <= spatial.MIN_NEIGHBOURS_TO_KRIGE:
                continue

            local_index = [position[station_id] for station_id in available.index]
            sub_distance = distance[np.ix_(local_index, local_index)]
            values = available.to_numpy()

            moran = spatial.morans_i(sub_distance, values)
            clustering = (
                moran - spatial.expected_morans_i(len(available)) if moran is not None else np.nan
            )

            for local_position, station_id in enumerate(available.index):
                others = [i for i in range(len(available)) if i != local_position]
                estimate = spatial.krige_from_distances(
                    sub_distance[np.ix_(others, others)],
                    sub_distance[local_position, others],
                    values[others],
                )
                rows.append(
                    {
                        "station_id": station_id,
                        "hour": hour,
                        "spatial_bias_krige": estimate.value if estimate else np.nan,
                        "spatial_bias_krige_var": estimate.variance if estimate else np.nan,
                        "spatial_neighbours": estimate.neighbours if estimate else 0,
                        "spatial_bias_clustering": clustering,
                    }
                )

    if not rows:
        return panel.assign(**empty)

    merged = panel.merge(pd.DataFrame(rows), on=["station_id", "hour"], how="left")
    # The one-hour shift, per station, for the reason in the docstring.
    merged[list(SPATIAL_COLUMNS)] = merged.groupby("station_id")[list(SPATIAL_COLUMNS)].shift(1)
    return merged


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
    *SPATIAL_COLUMNS,
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

    history = _history_features(_spatial_features(panel))
    # Only the lag columns, the spatial snapshot and identity travel from the
    # issue hour.
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
        *SPATIAL_COLUMNS,
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

    ``panel`` must therefore hold the target station's *neighbours* too, not
    just the station being forecast: the spatial features are built from the
    other stations in the same city, and a single-station panel would silently
    serve them as missing. :meth:`app.services.forecasting.ForecastService.
    _inference_rows` is what guarantees that.
    """

    history = _history_features(_spatial_features(panel))
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
            **{column: base[column] for column in SPATIAL_COLUMNS},
            **ambient.to_dict(),
        }
        rows.append(row)

    frame = pd.DataFrame(rows)
    return _cyclical_and_wind(frame, "target_hour")
