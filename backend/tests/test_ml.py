"""The feature pipeline and the trainer.

Synthetic data, on purpose: it gives a known answer to check against, and it
means these tests need no API key and no network.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.db.models import Estimator
from app.ml import baselines
from app.ml.evaluation import score, score_by_horizon
from app.ml.features import (
    LAGS,
    SPATIAL_COLUMNS,
    SupervisedFrame,
    _spatial_features,
    build_inference_rows,
    build_panel,
    build_supervised,
)
from app.ml.trainer import chronological_split, train


def _synthetic_rows(stations: int = 3, days: int = 45, seed: int = 7):
    """A believable PM2.5 series.

    Three components, because each one makes a different baseline hard:

    * a **multi-day episode** term, the slow build and clear-out driven by
      synoptic weather. This is what makes long-range forecasting possible at
      all, and it is invisible to climatology.
    * a **daily cycle**, which climatology captures for free.
    * **fast autocorrelated noise**, which makes persistence strong at short
      range and useless at long range.

    CAMS is then derived the way a coarse physics model actually fails: it is
    smoothed (a 40 km cell cannot resolve one street), biased, and noisy. It
    sees the episode, which is precisely the information the statistical layer
    cannot get from the station's own history.
    """

    rng = np.random.default_rng(seed)
    start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    hours = days * 24

    measurements, ambient = [], []
    for station_id in range(1, stations + 1):
        base = 40 + station_id * 8

        truth = np.empty(hours)
        winds = np.empty(hours)
        episode, fast = 0.0, 0.0
        for index in range(hours):
            moment = start + dt.timedelta(hours=index)
            # Slow synoptic swing, persisting over days.
            episode = 0.995 * episode + rng.normal(0, 2.2)
            # Fast local variation, persisting over hours.
            fast = 0.90 * fast + rng.normal(0, 2.5)
            wind = 1.5 + 2.5 * rng.random()
            diurnal = 14 * np.sin(2 * np.pi * (moment.hour - 6) / 24)

            truth[index] = max(base + episode + diurnal - 5 * wind + fast, 1.0)
            winds[index] = wind

        smoothed = pd.Series(truth).rolling(5, center=True, min_periods=1).mean().to_numpy()

        for index in range(hours):
            moment = start + dt.timedelta(hours=index)
            measurements.append(
                (
                    station_id,
                    moment,
                    round(float(truth[index]), 2),
                    23.8 + station_id * 0.02,
                    90.4 + station_id * 0.02,
                    10.0 + station_id,
                    "dhaka",
                )
            )
            ambient.append(
                (
                    station_id,
                    moment,
                    round(float(smoothed[index] * 0.62 + 11 + rng.normal(0, 7)), 2),
                    round(float(smoothed[index] * 0.85 + 25 + rng.normal(0, 12)), 2),
                    26.0 + 4 * np.sin(2 * np.pi * moment.hour / 24),
                    70.0,
                    float(winds[index]),
                    180.0,
                    0.0,
                    1008.0,
                    400.0,
                )
            )
    return measurements, ambient


@pytest.fixture(scope="module")
def panel() -> pd.DataFrame:
    measurements, ambient = _synthetic_rows()
    return build_panel(measurements, ambient, pd.DataFrame())


# --- the panel -----------------------------------------------------------


def test_the_panel_is_gap_free_per_station(panel):
    """Lags are computed with shift(), so a missing hour would silently turn a
    one-hour lag into a longer one. The panel must be reindexed to fix that."""
    for _, group in panel.groupby("station_id"):
        gaps = group["hour"].diff().dropna().unique()
        assert list(gaps) == [pd.Timedelta(hours=1)]


def test_a_hole_in_the_observations_stays_a_hole_rather_than_shifting_lags():
    measurements, ambient = _synthetic_rows(stations=1, days=10)
    # Remove six consecutive hours, as an outage would.
    del measurements[100:106]
    del ambient[100:106]

    built = build_panel(measurements, ambient, pd.DataFrame())

    assert built["pm25"].isna().sum() == 6
    gaps = built["hour"].diff().dropna().unique()
    assert list(gaps) == [pd.Timedelta(hours=1)]


# --- leakage -------------------------------------------------------------


def test_lag_features_come_from_the_issue_hour_and_the_target_from_later(panel):
    """The single most important property in the project. If a feature ever
    carries an observation from after the issue hour, every offline number is
    fiction."""
    frame = build_supervised(panel, horizons=6, max_rows=None)

    truth = panel.set_index(["station_id", "hour"])["pm25"]
    sample = frame.meta.sample(50, random_state=1)

    for index, row in sample.iterrows():
        issue, station = row["issue_hour"], row["station_id"]
        for lag in LAGS:
            expected = truth.get((station, issue - pd.Timedelta(hours=lag)))
            actual = frame.X.loc[index, f"pm25_lag_{lag}"]
            if pd.notna(expected) and pd.notna(actual):
                assert actual == pytest.approx(expected), f"lag {lag} is not a true offset"

        assert frame.y.loc[index] == pytest.approx(
            truth.get((station, row["target_hour"]))
        )
        assert row["target_hour"] == issue + pd.Timedelta(hours=row["horizon"])


def test_the_rolling_mean_excludes_the_issue_hour_itself(panel):
    """A rolling window that includes the current observation is a subtler
    leak, because the current value is legitimate but the window label implies
    it was computed from history alone."""
    frame = build_supervised(panel, horizons=2, max_rows=None)

    truth = panel.set_index(["station_id", "hour"])["pm25"]
    row = frame.meta.iloc[500]
    window = [
        truth.get((row["station_id"], row["issue_hour"] - pd.Timedelta(hours=offset)))
        for offset in range(1, 7)
    ]

    assert frame.X.loc[row.name, "pm25_roll_6_mean"] == pytest.approx(
        np.nanmean(window), rel=1e-6
    )


def test_ambient_features_are_taken_at_the_target_hour_not_the_issue_hour(panel):
    """CAMS and weather are forecasts, so using them at the target hour is
    legitimate. Using an *observation* there would not be."""
    frame = build_supervised(panel, horizons=4, max_rows=None)

    cams = panel.set_index(["station_id", "hour"])["cams_pm25"]
    row = frame.meta.iloc[300]

    assert frame.X.loc[row.name, "cams_pm25"] == pytest.approx(
        cams.get((row["station_id"], row["target_hour"]))
    )


def test_every_horizon_is_represented(panel):
    frame = build_supervised(panel, horizons=24, max_rows=None)

    assert sorted(frame.meta["horizon"].unique()) == list(range(1, 25))


# --- spatial features -----------------------------------------------------


def _bias_panel(biases: dict[int, float], hours: int = 40) -> pd.DataFrame:
    """A panel with a *known* CAMS bias per station.

    Every station sees the same CAMS field and the same true concentration
    shape; the only thing that differs is how far each station's observation
    sits above CAMS. That makes the kriged bias field's expected value
    obvious, which is what these tests check.
    """

    start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    measurements, ambient = [], []
    for index, (station_id, bias) in enumerate(sorted(biases.items())):
        for step in range(hours):
            moment = start + dt.timedelta(hours=step)
            cams = 30.0 + 5.0 * np.sin(2 * np.pi * step / 24)
            measurements.append(
                (
                    station_id,
                    moment,
                    round(cams + bias, 2),
                    23.80 + 0.03 * index,  # a few km apart, well inside the kriging range
                    90.40 + 0.03 * index,
                    10.0,
                    "dhaka",
                )
            )
            ambient.append(
                (station_id, moment, round(cams, 2), 60.0, 26.0, 70.0,
                 2.0, 180.0, 0.0, 1008.0, 400.0)
            )
    return build_panel(measurements, ambient, pd.DataFrame())


def test_a_station_gets_its_neighbours_bias_not_its_own(panel):
    """The leave-one-out property, and the whole point of the feature. One
    station is wildly biased against CAMS and the others are not; that
    station's own kriged bias must reflect the neighbours it cannot see
    itself in."""
    built = build_supervised(
        _bias_panel({1: 40.0, 2: 0.0, 3: 0.0, 4: 0.0}), horizons=1, max_rows=None
    )

    frame = built.X.assign(station_id=built.meta["station_id"])
    biased = frame[frame["station_id"] == 1]["spatial_bias_krige"].dropna()
    unbiased = frame[frame["station_id"] == 2]["spatial_bias_krige"].dropna()

    assert not biased.empty
    assert biased.abs().max() < 5.0, "its own +40 bias leaked into its own feature"
    assert unbiased.mean() > 5.0, "a neighbour's +40 bias should be visible from station 2"


def test_a_city_with_too_few_stations_reports_no_spatial_signal(panel):
    """Two stations cannot support a kriging system. Missing is the honest
    answer, and the estimator handles missing natively."""
    built = build_supervised(_bias_panel({1: 10.0, 2: 0.0}), horizons=1, max_rows=None)

    for column in SPATIAL_COLUMNS:
        assert column in built.X.columns
    assert built.X["spatial_bias_krige"].isna().all()


def test_spatial_features_come_from_the_hour_before_the_issue_hour(panel):
    """Same discipline as the lag columns: the freshest observation any
    feature carries is t-1, neighbours included. Anything fresher would be
    dense in the backfill and missing in production."""
    built = build_supervised(
        _bias_panel({1: 30.0, 2: 0.0, 3: 5.0, 4: -5.0}), horizons=3, max_rows=None
    )

    reference = (
        _spatial_features(_bias_panel({1: 30.0, 2: 0.0, 3: 5.0, 4: -5.0}))
        .set_index(["station_id", "hour"])["spatial_bias_krige"]
        .dropna()
    )

    checked = 0
    for index, row in built.meta.iterrows():
        actual = built.X.loc[index, "spatial_bias_krige"]
        if pd.isna(actual):
            continue
        # The panel row for the issue hour already carries the shifted value.
        assert actual == pytest.approx(reference[(row["station_id"], row["issue_hour"])])
        checked += 1
        if checked >= 50:
            break
    assert checked > 0, "no row carried a spatial feature; the join is broken"


def test_clustering_is_measured_against_chance_not_against_zero():
    """Moran's I averages -1/(n-1) under spatial randomness, which for the
    three or four stations a city typically has reporting is nowhere near
    zero. A bias that varies smoothly across the city - the textbook
    clustered field - must come out positive even though its raw I does not."""
    built = build_supervised(
        _bias_panel({1: 0.0, 2: 4.0, 3: 8.0, 4: 12.0}), horizons=1, max_rows=None
    )

    values = built.X["spatial_bias_clustering"].dropna()

    assert not values.empty
    assert values.mean() > 0.0


def test_the_live_pipeline_serves_the_same_spatial_snapshot_for_every_horizon():
    """Inference rows are built from the issue hour, so all 24 horizons share
    one spatial snapshot. If this ever differs per horizon, something is
    reading forward."""
    built = _bias_panel({1: 15.0, 2: 0.0, 3: 0.0, 4: 0.0})
    issue_hour = built["hour"].max()

    rows = build_inference_rows(built, station_id=1, issue_hour=issue_hour, horizons=6)

    assert len(rows) == 6
    for column in SPATIAL_COLUMNS:
        assert rows[column].nunique(dropna=False) == 1
    assert rows["spatial_bias_krige"].notna().all(), "neighbours were present; expect a value"


def test_a_single_station_panel_at_inference_degrades_to_missing_not_wrong():
    """Serving a station whose neighbours are all offline must not invent a
    spatial signal."""
    built = _bias_panel({1: 15.0})
    issue_hour = built["hour"].max()

    rows = build_inference_rows(built, station_id=1, issue_hour=issue_hour, horizons=3)

    assert rows["spatial_bias_krige"].isna().all()


def _structured_bias_rows(stations: int = 5, days: int = 30, seed: int = 11):
    """Stations on a transect, with a CAMS bias field that varies smoothly
    along it and drifts over days.

    This is the situation the spatial block exists for, and it is not a
    contrivance: it is what a source region a 40 km CAMS cell cannot resolve -
    a kiln cluster, a port, a stretch of stop-start traffic - does to the
    error field. Stations near it share a bias anomaly that none of them can
    infer from its own history.
    """

    rng = np.random.default_rng(seed)
    start = dt.datetime(2026, 4, 1, tzinfo=dt.UTC)
    hours = days * 24
    positions = np.linspace(0.0, 0.18, stations)  # ~20 km of latitude

    episode = np.zeros(hours)  # synoptic swing, shared by the whole city
    source = np.zeros(hours)  # the unresolved source's strength, drifting
    slow_episode = slow_source = 0.0
    for index in range(hours):
        slow_episode = 0.995 * slow_episode + rng.normal(0, 2.4)
        slow_source = 0.99 * slow_source + rng.normal(0, 1.6)
        episode[index], source[index] = slow_episode, slow_source

    measurements, ambient = [], []
    for station_index in range(stations):
        # 1 at the source end of the transect, 0 at the far end.
        proximity = 1.0 - positions[station_index] / positions[-1]
        fast = 0.0
        for index in range(hours):
            moment = start + dt.timedelta(hours=index)
            fast = 0.88 * fast + rng.normal(0, 2.2)
            wind = 1.5 + 2.0 * rng.random()
            diurnal = 12 * np.sin(2 * np.pi * (moment.hour - 6) / 24)

            cams = max(28 + episode[index] + diurnal * 0.7 - 3 * wind, 1.0)
            truth = max(cams + 6.0 + 1.4 * proximity * source[index] + fast, 1.0)

            measurements.append(
                (
                    station_index + 1,
                    moment,
                    round(float(truth), 2),
                    float(23.75 + positions[station_index]),
                    float(90.35 + positions[station_index] * 0.2),
                    10.0,
                    "dhaka",
                )
            )
            ambient.append(
                (
                    station_index + 1,
                    moment,
                    round(float(cams + rng.normal(0, 2.0)), 2),
                    round(float(cams * 1.4), 2),
                    26.0,
                    70.0,
                    float(wind),
                    180.0,
                    0.0,
                    1008.0,
                    400.0,
                )
            )
    return measurements, ambient


def test_the_spatial_block_earns_its_place_when_the_bias_field_is_structured(tmp_path):
    """The claim the feature is added on, measured rather than asserted: the
    same estimator, the same rows, the same split, with and without the four
    spatial columns.

    A neighbour's bias is only worth knowing if bias is spatially correlated,
    so that is what this fixture makes true. The reverse case - an
    unstructured field, where the extra columns cost a couple of percent - is
    documented in `docs/model-improvement-plan.md` rather than pinned here,
    because what governs it in production is the promotion gate.
    """

    measurements, ambient = _structured_bias_rows()
    built = build_panel(measurements, ambient, pd.DataFrame())
    frame = build_supervised(built, horizons=6, max_rows=None)

    assert frame.X["spatial_bias_krige"].notna().mean() > 0.9, "the fixture must have neighbours"

    without = SupervisedFrame(
        X=frame.X[[c for c in frame.X.columns if c not in SPATIAL_COLUMNS]],
        y=frame.y,
        meta=frame.meta,
    )
    with_spatial = train(frame, built, tmp_path / "with", version=1)
    without_spatial = train(without, built, tmp_path / "without", version=1)

    ours = with_spatial.metrics["overall"][Estimator.MODEL.value]["mae"]
    theirs = without_spatial.metrics["overall"][Estimator.MODEL.value]["mae"]

    assert ours < theirs * 0.95, (
        f"spatial features bought only {100 * (theirs - ours) / theirs:.1f}%; "
        "they are four extra columns and should pay for themselves"
    )


# --- the split -----------------------------------------------------------


def test_the_split_is_chronological_so_the_holdout_is_strictly_later(panel):
    """A random split puts near-duplicate neighbouring hours on both sides and
    reports an accuracy the model will never reproduce in production."""
    frame = build_supervised(panel, horizons=12, max_rows=None)

    train_mask, test_mask = chronological_split(frame)

    assert frame.meta[train_mask]["issue_hour"].max() <= frame.meta[test_mask][
        "issue_hour"
    ].min()
    assert 0.1 < test_mask.mean() < 0.35


# --- training ------------------------------------------------------------


@pytest.fixture(scope="module")
def trained(panel, tmp_path_factory):
    frame = build_supervised(panel, horizons=12, max_rows=60_000)
    return train(frame, panel, tmp_path_factory.mktemp("models"), version=1)


def test_training_produces_an_artefact_and_a_full_scorecard(trained):
    assert trained.artifact_path.exists()
    assert trained.training_rows > 1000

    overall = trained.metrics["overall"]
    for estimator in (
        Estimator.MODEL.value,
        Estimator.PERSISTENCE.value,
        Estimator.CLIMATOLOGY.value,
        Estimator.CAMS.value,
    ):
        assert estimator in overall, f"{estimator} missing from the scorecard"
        assert overall[estimator]["sample_size"] > 0


def test_the_model_beats_raw_cams(trained):
    """The project's central claim. CAMS here is deliberately biased, which is
    what a coarse physics model looks like at a specific station."""
    overall = trained.metrics["overall"]

    assert overall[Estimator.MODEL.value]["mae"] < overall[Estimator.CAMS.value]["mae"]


def test_the_model_beats_climatology(trained):
    overall = trained.metrics["overall"]

    assert (
        overall[Estimator.MODEL.value]["mae"] < overall[Estimator.CLIMATOLOGY.value]["mae"]
    )


def test_the_model_beats_persistence_at_longer_horizons(trained):
    """Persistence is expected to win at one to three hours. The value of a
    forecast is further out, so that is where the comparison is made."""
    per_horizon = trained.metrics["per_horizon"]

    for horizon in (6, 9, 12):
        ours = per_horizon[Estimator.MODEL.value][horizon]["mae"]
        theirs = per_horizon[Estimator.PERSISTENCE.value][horizon]["mae"]
        assert ours < theirs, f"persistence still wins at h={horizon}"


def test_error_grows_materially_with_horizon(trained):
    """A leakage canary. If a feature carries the answer, accuracy is flat
    across horizons because the model is reading the target rather than
    forecasting it. Twelve hours out must be clearly harder than one."""
    per_horizon = trained.metrics["per_horizon"][Estimator.MODEL.value]

    # The degenerate case this guards against was flat to within 12%. The
    # absolute plausibility check below is the stronger canary; this one pins
    # the shape.
    assert per_horizon[12]["mae"] > per_horizon[1]["mae"] * 1.15


def test_accuracy_is_plausible_rather_than_suspiciously_perfect(trained):
    """PM2.5 is not predictable to a fraction of a microgram. An MAE near zero
    means a feature is leaking the target, not that the model is brilliant."""
    mae = trained.metrics["overall"][Estimator.MODEL.value]["mae"]

    assert mae > 1.0, "implausibly low error; check the features for leakage"


def test_the_eighty_percent_band_covers_roughly_eighty_percent(trained):
    """An interval nobody checked is decoration."""
    coverage = trained.metrics["overall"][Estimator.MODEL.value]["coverage_80"]

    assert coverage is not None
    assert 0.6 <= coverage <= 0.95


def test_a_tiny_dataset_is_refused_rather_than_silently_trained(tmp_path):
    measurements, ambient = _synthetic_rows(stations=1, days=2)
    small = build_panel(measurements, ambient, pd.DataFrame())
    frame = build_supervised(small, horizons=3, max_rows=None)

    with pytest.raises(ValueError, match="too small"):
        train(frame, small, tmp_path, version=1)


# --- metrics -------------------------------------------------------------


def test_metrics_ignore_pairs_where_a_baseline_has_no_value():
    actual = np.array([10.0, 20.0, 30.0])
    predicted = np.array([12.0, np.nan, 28.0])

    result = score(actual, predicted)

    assert result.sample_size == 2
    assert result.mae == pytest.approx(2.0)


def test_bias_shows_direction_not_just_magnitude():
    actual = np.array([10.0, 10.0, 10.0])

    assert score(actual, np.array([12.0, 12.0, 12.0])).bias == pytest.approx(2.0)
    assert score(actual, np.array([8.0, 8.0, 8.0])).bias == pytest.approx(-2.0)


def test_per_horizon_scoring_splits_the_rows():
    horizons = np.array([1, 1, 6, 6])
    actual = np.array([10.0, 10.0, 10.0, 10.0])
    predicted = np.array([11.0, 11.0, 15.0, 15.0])

    result = score_by_horizon(horizons, actual, predicted)

    assert result[1].mae == pytest.approx(1.0)
    assert result[6].mae == pytest.approx(5.0)


def test_climatology_falls_back_when_a_station_hour_was_never_seen(panel):
    hour_means = baselines.hour_of_day_means(panel)
    frame = pd.DataFrame(
        {
            "station_id": [999],
            "target_hour": [pd.Timestamp("2026-07-01T05:00Z")],
        }
    )

    values = baselines.climatology(frame, hour_means)

    assert not np.isnan(values[0]), "an unseen station must not produce NaN"
