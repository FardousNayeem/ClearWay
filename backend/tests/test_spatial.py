import numpy as np
import pytest

from app.domain.geo import Point, haversine_km
from app.domain.spatial import (
    krige_from_distances,
    morans_i,
    pairwise_distance_km,
)

# --- pairwise_distance_km -------------------------------------------------


def test_pairwise_distances_are_symmetric_with_a_zero_diagonal():
    points = [Point(23.81, 90.41), Point(23.90, 90.50), Point(24.00, 90.30)]

    matrix = pairwise_distance_km(points)

    assert matrix.shape == (3, 3)
    assert np.allclose(np.diag(matrix), 0.0)
    assert matrix[0, 1] == pytest.approx(matrix[1, 0])
    assert matrix[0, 1] > 0


# --- krige_from_distances --------------------------------------------------


def test_a_single_neighbour_is_a_copy_not_an_interpolation_so_it_is_refused():
    """One neighbour would force a weight of 1 and return that station's own
    reading dressed up as a spatial estimate."""
    assert krige_from_distances(np.zeros((1, 1)), np.array([2.0]), np.array([10.0])) is None


def test_identical_neighbours_collapse_to_their_shared_value():
    """Zero spatial variance is a legitimate answer, not a crash."""
    distances = np.array([[0, 5, 5], [5, 0, 5], [5, 5, 0]], dtype=float)
    target = np.array([3.0, 3.0, 3.0])
    values = np.array([20.0, 20.0, 20.0])

    result = krige_from_distances(distances, target, values)

    assert result is not None
    assert result.value == pytest.approx(20.0)
    assert result.variance == pytest.approx(0.0)


def test_a_closer_neighbour_is_weighted_more_towards_its_own_value():
    """One neighbour sits right next to the target, the other far away; the
    estimate should land much closer to the near one's value."""
    points = [Point(0.0, 0.0), Point(0.001, 0.0), Point(5.0, 0.0)]
    target = Point(0.0005, 0.0)  # a few tens of metres from the first two
    distances = pairwise_distance_km(points)
    target_distances = np.array([haversine_km(target, p) for p in points])
    values = np.array([10.0, 12.0, 80.0])

    result = krige_from_distances(distances, target_distances, values)

    assert result is not None
    assert 10.0 <= result.value <= 20.0, "the far outlier must not dominate"


def test_kriging_variance_shrinks_as_neighbours_agree():
    distances = np.array([[0, 8, 8], [8, 0, 8], [8, 8, 0]], dtype=float)
    target = np.array([4.0, 4.0, 4.0])

    agreeing = krige_from_distances(distances, target, np.array([20.0, 21.0, 19.0]))
    disagreeing = krige_from_distances(distances, target, np.array([5.0, 40.0, 22.0]))

    assert agreeing is not None
    assert disagreeing is not None
    assert agreeing.variance < disagreeing.variance


def test_a_singular_system_is_handled_by_the_least_squares_fallback():
    """Two stations on top of each other make the covariance matrix singular.
    This must degrade gracefully rather than raise."""
    distances = np.array([[0, 0, 9], [0, 0, 9], [9, 9, 0]], dtype=float)
    target = np.array([3.0, 3.0, 6.0])
    values = np.array([10.0, 10.0, 40.0])

    result = krige_from_distances(distances, target, values)

    assert result is not None
    assert np.isfinite(result.value)


# --- morans_i ---------------------------------------------------------------


def test_too_few_points_returns_none():
    assert morans_i(np.zeros((2, 2)), np.array([1.0, 2.0])) is None


def test_identical_values_are_neutral_rather_than_undefined():
    distances = np.array([[0, 5, 5], [5, 0, 5], [5, 5, 0]])

    assert morans_i(distances, np.array([10.0, 10.0, 10.0])) == 0.0


def test_a_smooth_spatial_gradient_scores_strongly_clustered():
    """Values that vary smoothly with distance are the textbook case for
    positive spatial autocorrelation: nearby points read alike."""
    points = [
        Point(0.0, 0.0),
        Point(0.02, 0.0),
        Point(0.04, 0.0),
        Point(0.20, 0.0),
        Point(0.22, 0.0),
    ]
    distances = pairwise_distance_km(points)
    # Two nearby pairs at similar levels, far apart from each other.
    values = np.array([10.0, 11.0, 12.0, 80.0, 82.0])

    result = morans_i(distances, values)

    assert result is not None
    assert result > 0.3


def test_a_checkerboard_scores_negative():
    """Alternating high/low values among near-equidistant points is the
    signature of negative spatial autocorrelation."""
    points = [Point(0.0, 0.0), Point(0.01, 0.0), Point(0.0, 0.01), Point(0.01, 0.01)]
    distances = pairwise_distance_km(points)
    values = np.array([100.0, 0.0, 0.0, 100.0])

    result = morans_i(distances, values)

    assert result is not None
    assert result < 0
