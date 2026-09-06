import math

import pytest

from app.domain.geo import (
    BoundingBox,
    Point,
    bounding_box_around,
    haversine_km,
    inverse_distance_weights,
)


def test_distance_between_known_cities():
    """Dhaka to Delhi is roughly 1430 km great-circle."""
    dhaka = Point(23.8103, 90.4125)
    delhi = Point(28.6139, 77.2090)

    assert haversine_km(dhaka, delhi) == pytest.approx(1430, abs=15)


def test_distance_to_self_is_zero():
    assert haversine_km(Point(23.8, 90.4), Point(23.8, 90.4)) == pytest.approx(0.0)


def test_distance_is_symmetric():
    a, b = Point(51.5, -0.12), Point(31.52, 74.35)

    assert haversine_km(a, b) == pytest.approx(haversine_km(b, a))


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
)
def test_impossible_coordinates_are_rejected_at_construction(lat, lon):
    with pytest.raises(ValueError, match="outside"):
        Point(lat, lon)


def test_a_radius_box_actually_contains_the_radius():
    centre = Point(23.8103, 90.4125)
    box = bounding_box_around(centre, radius_km=25)

    due_north = Point(centre.latitude + math.degrees(20 / 6371.0088), centre.longitude)
    assert box.contains(due_north)
    assert box.contains(centre)


def test_the_box_widens_in_longitude_near_the_poles():
    """One degree of longitude is much shorter at high latitude, so the box
    must be wider there to cover the same distance."""
    tropical = bounding_box_around(Point(0.0, 0.0), 100)
    polar = bounding_box_around(Point(70.0, 0.0), 100)

    assert (polar.east - polar.west) > (tropical.east - tropical.west)


def test_bounding_box_parsing_and_ordering():
    box = BoundingBox.parse("90.0,23.6,90.6,24.0")

    assert box.west == 90.0
    assert box.north == 24.0
    with pytest.raises(ValueError, match="wrong order"):
        BoundingBox.parse("90.6,23.6,90.0,24.0")
    with pytest.raises(ValueError, match="four comma separated"):
        BoundingBox.parse("1,2,3")


def test_weights_sum_to_one_and_favour_the_nearest():
    weights = inverse_distance_weights([1.0, 2.0, 10.0])

    assert sum(weights) == pytest.approx(1.0)
    assert weights[0] > weights[1] > weights[2]


def test_a_station_on_top_of_the_query_point_does_not_take_all_the_weight():
    """Without the epsilon guard this collapses to a single noisy reading."""
    weights = inverse_distance_weights([0.0, 1.0, 2.0])

    assert weights[0] < 1.0
    assert weights[1] > 0


def test_no_stations_means_no_weights():
    assert inverse_distance_weights([]) == []
