"""Does the pollution field, right now, have spatial structure?

Two questions this module answers, both from a handful of simultaneous
station readings:

* **Kriging** - what is the value likely to be *here*, given what nearby
  stations are reading at this exact hour, and how much should that estimate
  be trusted (the kriging variance)? Used in :mod:`app.ml.features` to give
  the forecaster a live estimate of the CAMS bias at a station's own location,
  built entirely from *other* stations.
* **Moran's I** - across a city's stations at this hour, is the signal
  spatially clustered (smoke drifting as one mass, an inversion sitting over
  the whole basin) or spatially incoherent (patchy, station-to-station noise,
  or simply too few stations to tell)? That single number is what should
  decide how much weight the kriged estimate above deserves, and it is left
  to the model to learn that rather than hard-coded, by handing both numbers
  over as features.

A city here has at most a handful of active stations, changing hour to hour
as sensors drop in and out. That rules out fitting a fresh semivariogram per
hour - there is rarely enough data to do it stably, and a broken fit is worse
than an honest simplification. So an isotropic exponential covariance is
assumed, with a **fixed** range (see ``DEFAULT_RANGE_KM``) and a per-hour sill
taken directly from the spread of that hour's values. This is a standard
simplification in sparse-network geostatistics; it is not a substitute for a
properly fitted variogram, and the docstring says so rather than pretending
otherwise.

Pure functions, no pandas, no I/O: everything here operates on plain arrays of
distances and values, which is what makes it unit-testable without a database
and reusable from both the forecaster's feature pipeline and, eventually, the
virtual-sensor interpolator described in the README.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geo import Point, haversine_km

#: Decorrelation length for the exponential covariance, in kilometres.
#: Empirical PM2.5 and NO2 semivariograms for urban background networks
#: typically report practical ranges in the 10-30 km band (see the land-use-
#: regression and universal-kriging literature cited in
#: ``docs/model-improvement-plan.md``). 15 km sits in the middle of that band
#: and is a constant specifically so it can be revisited once enough pooled
#: residual pairs exist to fit a real variogram per city.
DEFAULT_RANGE_KM = 15.0

#: Two neighbours is the floor for something that is genuinely an
#: interpolation rather than a copy of one station's reading. It is set this
#: low deliberately: leave-one-out means a city needs ``this + 1`` stations
#: reporting in the same hour before any spatial feature exists at all, and
#: real free-tier networks often have three or four. Sparse support is not
#: hidden - it shows up as a large kriging variance and a low neighbour count,
#: both of which are handed to the model as features in their own right.
MIN_NEIGHBOURS_TO_KRIGE = 2

#: Below this many simultaneous points, "clustered vs incoherent" is not a
#: question the data can answer.
MIN_POINTS_FOR_MORANS_I = 3

#: Measurement and micro-siting noise, as a fraction of the hour's variance.
#: Without it, a station sitting almost exactly on the target would be
#: trusted as if it were a noise-free duplicate.
NUGGET_FRACTION = 0.15


@dataclass(frozen=True, slots=True)
class KrigingEstimate:
    value: float
    variance: float
    neighbours: int


def pairwise_distance_km(points: list[Point]) -> np.ndarray:
    """The full distance matrix for a set of points, computed once.

    Station coordinates do not change hour to hour, so callers that krige the
    same city across many hours should build this once and reuse it, rather
    than re-deriving it from scratch for every hour.
    """

    n = len(points)
    matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            distance = haversine_km(points[i], points[j])
            matrix[i, j] = matrix[j, i] = distance
    return matrix


def _exponential_covariance(distance_km: np.ndarray, range_km: float, sill: float) -> np.ndarray:
    return sill * np.exp(-distance_km / range_km)


def krige_from_distances(
    neighbour_distance_km: np.ndarray,
    target_distance_km: np.ndarray,
    values: np.ndarray,
    *,
    range_km: float = DEFAULT_RANGE_KM,
    nugget_fraction: float = NUGGET_FRACTION,
    min_neighbours: int = MIN_NEIGHBOURS_TO_KRIGE,
) -> KrigingEstimate | None:
    """Ordinary kriging of ``values``, known at stations ``neighbour_distance_km``
    apart, evaluated at a point ``target_distance_km`` away from each of them.

    Returns ``None`` below ``min_neighbours``: that is "no spatial signal
    available", and callers should emit a missing feature rather than a
    number dressed up as an estimate. ``HistGradientBoostingRegressor``
    handles missing features natively, which is precisely why that estimator
    was chosen for the forecaster (see ``app.ml.trainer``).
    """

    k = len(values)
    if k < min_neighbours:
        return None

    sill = float(np.var(values))
    if sill <= 0:
        # Every neighbour agrees exactly. Degenerate, but not undefined: the
        # only sane estimate is their shared value, with zero spatial variance.
        return KrigingEstimate(value=float(values[0]), variance=0.0, neighbours=k)
    nugget = sill * nugget_fraction

    covariance = _exponential_covariance(neighbour_distance_km, range_km, sill)
    np.fill_diagonal(covariance, sill + nugget)

    # Augmented with a Lagrange multiplier so the weights are constrained to
    # sum to one - the "ordinary" in ordinary kriging, which is what lets the
    # estimator stay unbiased without assuming a known global mean.
    system = np.ones((k + 1, k + 1))
    system[:k, :k] = covariance
    system[k, k] = 0.0

    target_covariance = _exponential_covariance(target_distance_km, range_km, sill)
    rhs = np.ones(k + 1)
    rhs[:k] = target_covariance

    try:
        solution = np.linalg.solve(system, rhs)
    except np.linalg.LinAlgError:
        solution = np.linalg.lstsq(system, rhs, rcond=None)[0]

    weights = solution[:k]
    lagrange = solution[k]
    estimate = float(np.dot(weights, values))
    variance = max(float(sill + nugget - np.dot(weights, target_covariance) - lagrange), 0.0)
    return KrigingEstimate(value=estimate, variance=variance, neighbours=k)


def morans_i(
    distance_km: np.ndarray,
    values: np.ndarray,
    *,
    range_km: float = DEFAULT_RANGE_KM,
    min_points: int = MIN_POINTS_FOR_MORANS_I,
) -> float | None:
    """Global Moran's I over one snapshot of simultaneous station values.

    +1 is strongly clustered (near stations read alike - a real, shared
    spatial pattern), 0 is spatially random, negative is checkerboarded
    (rare; usually station-specific noise dominating any real signal). The
    same exponential weighting as the kriging covariance is used, so the two
    numbers describe the same notion of "nearby".
    """

    n = len(values)
    if n < min_points:
        return None

    values = np.asarray(values, dtype=float)
    z = values - values.mean()
    if np.allclose(z, 0.0):
        return 0.0  # every station reads the same; "clustering" is moot, not undefined

    weights = np.exp(-distance_km / range_km)
    np.fill_diagonal(weights, 0.0)
    weight_sum = weights.sum()
    if weight_sum == 0:
        return None

    numerator = z @ weights @ z
    denominator = float(np.sum(z**2))
    if denominator == 0:
        return 0.0
    return float((n / weight_sum) * (numerator / denominator))


def expected_morans_i(n: int) -> float:
    """What Moran's I averages to when there is *no* spatial pattern at all.

    Not zero: for ``n`` points it is ``-1/(n - 1)``, which for the three or
    four stations a city typically has reporting at once is a long way from
    zero (-0.5 at n=3). Any feature built on raw I would therefore move
    whenever a sensor dropped in or out, for reasons that have nothing to do
    with the air. Subtracting this is what makes "0 means spatially random"
    true regardless of how many stations reported.
    """

    if n < 2:
        raise ValueError("Moran's I is undefined below two points")
    return -1.0 / (n - 1)
