"""AQI conversion. These are the numbers a user acts on, so they get pinned."""

import pytest

from app.domain.aqi import (
    AqiCategory,
    Pollutant,
    aqi_from_concentration,
    category_for_aqi,
    concentration_from_aqi,
    overall_aqi,
)


@pytest.mark.parametrize(
    ("concentration", "expected_aqi", "expected_category"),
    [
        (0.0, 0, AqiCategory.GOOD),
        (9.0, 50, AqiCategory.GOOD),
        (9.1, 51, AqiCategory.MODERATE),
        (35.4, 100, AqiCategory.MODERATE),
        (35.5, 101, AqiCategory.UNHEALTHY_SENSITIVE),
        (55.4, 150, AqiCategory.UNHEALTHY_SENSITIVE),
        (55.5, 151, AqiCategory.UNHEALTHY),
        (125.4, 200, AqiCategory.UNHEALTHY),
        (125.5, 201, AqiCategory.VERY_UNHEALTHY),
        (225.4, 300, AqiCategory.VERY_UNHEALTHY),
        (225.5, 301, AqiCategory.HAZARDOUS),
    ],
)
def test_pm25_category_boundaries_match_the_2024_epa_table(
    concentration, expected_aqi, expected_category
):
    result = aqi_from_concentration(concentration, Pollutant.PM25)

    assert result is not None
    assert result.value == expected_aqi
    assert result.category is expected_category


def test_the_good_ceiling_is_the_2024_value_not_the_old_one():
    """Before May 2024 the Good band ran to 12.0. Anything on the old table
    would call 10 ug/m3 'Good'; the current table calls it Moderate."""
    result = aqi_from_concentration(10.0, Pollutant.PM25)

    assert result is not None
    assert result.category is AqiCategory.MODERATE


def test_concentration_is_truncated_before_conversion_not_rounded():
    """EPA truncates to one decimal place. 9.09 is Good, 9.1 is not."""
    assert aqi_from_concentration(9.09, Pollutant.PM25).category is AqiCategory.GOOD
    assert aqi_from_concentration(9.10, Pollutant.PM25).category is AqiCategory.MODERATE


def test_readings_beyond_the_table_clamp_to_the_top_of_the_scale():
    result = aqi_from_concentration(900.0, Pollutant.PM25)

    assert result is not None
    assert result.value == 500
    assert result.category is AqiCategory.HAZARDOUS


def test_a_negative_reading_is_a_sensor_fault_not_clean_air():
    assert aqi_from_concentration(-3.0, Pollutant.PM25) is None


def test_pm10_uses_its_own_unchanged_table():
    result = aqi_from_concentration(54.0, Pollutant.PM10)

    assert result is not None
    assert result.value == 50
    assert result.pollutant is Pollutant.PM10


def test_the_overall_index_is_the_worst_pollutant_not_the_average():
    """PM2.5 of 5 is Good (AQI 28); PM10 of 300 is Unhealthy (AQI 173).
    The overall index reports the worse of the two, never a blend."""
    result = overall_aqi(pm25=5.0, pm10=300.0)

    assert result is not None
    assert result.pollutant is Pollutant.PM10
    assert result.value == 173
    assert result.category is AqiCategory.UNHEALTHY


def test_overall_index_copes_with_one_pollutant_missing():
    assert overall_aqi(pm25=40.0, pm10=None) is not None
    assert overall_aqi(pm25=None, pm10=None) is None


@pytest.mark.parametrize(
    ("value", "category"),
    [
        (0, AqiCategory.GOOD),
        (50, AqiCategory.GOOD),
        (51, AqiCategory.MODERATE),
        (150, AqiCategory.UNHEALTHY_SENSITIVE),
        (201, AqiCategory.VERY_UNHEALTHY),
        (450, AqiCategory.HAZARDOUS),
    ],
)
def test_bucketing_a_precomputed_index(value, category):
    assert category_for_aqi(value) is category


def test_every_category_carries_advice_a_person_can_act_on():
    for concentration in (5.0, 20.0, 45.0, 90.0, 200.0, 400.0):
        result = aqi_from_concentration(concentration, Pollutant.PM25)
        assert result is not None
        assert result.advice
        assert result.label


@pytest.mark.parametrize("concentration", [3.0, 15.0, 42.0, 80.0, 180.0, 260.0])
def test_the_inverse_recovers_the_concentration_it_started_from(concentration):
    """Providers that publish an index rather than micrograms are converted
    back on the way in. The round trip has to be tight or labels drift."""
    forward = aqi_from_concentration(concentration, Pollutant.PM25)
    assert forward is not None

    recovered = concentration_from_aqi(forward.value, Pollutant.PM25)

    assert recovered == pytest.approx(concentration, abs=0.6)


def test_the_inverse_clamps_above_the_published_scale():
    assert concentration_from_aqi(900, Pollutant.PM25) == 325.4


def test_the_inverse_rejects_a_negative_index():
    assert concentration_from_aqi(-1, Pollutant.PM25) is None
