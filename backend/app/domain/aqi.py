"""US Air Quality Index maths.

Pure functions. No I/O, no framework, no database, so every rule here is unit
testable in microseconds.

The PM2.5 breakpoints are the **May 2024 EPA revision**, which lowered the
"Good" ceiling from 12.0 to 9.0 ug/m3 and tightened the upper categories. A lot
of code in the wild still uses the pre-2024 table and silently reports air as
cleaner than the standard now says it is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class Pollutant(StrEnum):
    PM25 = "pm25"
    PM10 = "pm10"


class AqiCategory(StrEnum):
    GOOD = "good"
    MODERATE = "moderate"
    UNHEALTHY_SENSITIVE = "unhealthy_sensitive"
    UNHEALTHY = "unhealthy"
    VERY_UNHEALTHY = "very_unhealthy"
    HAZARDOUS = "hazardous"


@dataclass(frozen=True, slots=True)
class Breakpoint:
    conc_low: float
    conc_high: float
    aqi_low: int
    aqi_high: int
    category: AqiCategory


#: PM2.5, 24-hour average, ug/m3. US EPA, as revised May 2024.
PM25_BREAKPOINTS: tuple[Breakpoint, ...] = (
    Breakpoint(0.0, 9.0, 0, 50, AqiCategory.GOOD),
    Breakpoint(9.1, 35.4, 51, 100, AqiCategory.MODERATE),
    Breakpoint(35.5, 55.4, 101, 150, AqiCategory.UNHEALTHY_SENSITIVE),
    Breakpoint(55.5, 125.4, 151, 200, AqiCategory.UNHEALTHY),
    Breakpoint(125.5, 225.4, 201, 300, AqiCategory.VERY_UNHEALTHY),
    Breakpoint(225.5, 325.4, 301, 500, AqiCategory.HAZARDOUS),
)

#: PM10, 24-hour average, ug/m3. Unchanged by the 2024 revision.
PM10_BREAKPOINTS: tuple[Breakpoint, ...] = (
    Breakpoint(0, 54, 0, 50, AqiCategory.GOOD),
    Breakpoint(55, 154, 51, 100, AqiCategory.MODERATE),
    Breakpoint(155, 254, 101, 150, AqiCategory.UNHEALTHY_SENSITIVE),
    Breakpoint(255, 354, 151, 200, AqiCategory.UNHEALTHY),
    Breakpoint(355, 424, 201, 300, AqiCategory.VERY_UNHEALTHY),
    Breakpoint(425, 604, 301, 500, AqiCategory.HAZARDOUS),
)

_BREAKPOINTS: dict[Pollutant, tuple[Breakpoint, ...]] = {
    Pollutant.PM25: PM25_BREAKPOINTS,
    Pollutant.PM10: PM10_BREAKPOINTS,
}

#: EPA truncates the concentration before applying the formula. PM2.5 keeps one
#: decimal place, PM10 is truncated to a whole number.
_TRUNCATION: dict[Pollutant, int] = {Pollutant.PM25: 1, Pollutant.PM10: 0}

CATEGORY_LABELS: dict[AqiCategory, str] = {
    AqiCategory.GOOD: "Good",
    AqiCategory.MODERATE: "Moderate",
    AqiCategory.UNHEALTHY_SENSITIVE: "Unhealthy for sensitive groups",
    AqiCategory.UNHEALTHY: "Unhealthy",
    AqiCategory.VERY_UNHEALTHY: "Very unhealthy",
    AqiCategory.HAZARDOUS: "Hazardous",
}

CATEGORY_ADVICE: dict[AqiCategory, str] = {
    AqiCategory.GOOD: "Air quality is fine. No precautions needed.",
    AqiCategory.MODERATE: (
        "Acceptable for most people. If you are unusually sensitive to particle "
        "pollution, consider a shorter or easier outdoor session."
    ),
    AqiCategory.UNHEALTHY_SENSITIVE: (
        "People with asthma or heart conditions, older adults and children should "
        "cut back on prolonged effort outdoors."
    ),
    AqiCategory.UNHEALTHY: (
        "Everyone should reduce prolonged effort outdoors. Sensitive groups should "
        "stay in where possible."
    ),
    AqiCategory.VERY_UNHEALTHY: (
        "Avoid outdoor effort. Keep windows shut and run filtration if you have it."
    ),
    AqiCategory.HAZARDOUS: (
        "Stay indoors. This is an emergency-level reading for the whole population."
    ),
}


def _truncate(value: float, places: int) -> float:
    factor = 10**places
    return math.floor(value * factor) / factor


@dataclass(frozen=True, slots=True)
class AqiResult:
    value: int
    category: AqiCategory
    pollutant: Pollutant

    @property
    def label(self) -> str:
        return CATEGORY_LABELS[self.category]

    @property
    def advice(self) -> str:
        return CATEGORY_ADVICE[self.category]


def aqi_from_concentration(concentration: float, pollutant: Pollutant) -> AqiResult | None:
    """Convert a concentration in ug/m3 to a US AQI value.

    Returns ``None`` for a negative reading, which is how several stations
    report a sensor fault. Concentrations above the top breakpoint are clamped
    to 500, the top of the published scale.
    """

    if concentration < 0 or math.isnan(concentration):
        return None

    breakpoints = _BREAKPOINTS[pollutant]
    value = _truncate(concentration, _TRUNCATION[pollutant])

    if value > breakpoints[-1].conc_high:
        return AqiResult(500, AqiCategory.HAZARDOUS, pollutant)

    for bp in breakpoints:
        if value <= bp.conc_high:
            span_conc = bp.conc_high - bp.conc_low
            span_aqi = bp.aqi_high - bp.aqi_low
            # A zero-width band would only arise from a malformed table.
            fraction = 0.0 if span_conc == 0 else (value - bp.conc_low) / span_conc
            return AqiResult(round(bp.aqi_low + fraction * span_aqi), bp.category, pollutant)

    return AqiResult(500, AqiCategory.HAZARDOUS, pollutant)


def overall_aqi(pm25: float | None = None, pm10: float | None = None) -> AqiResult | None:
    """The overall AQI is the worst of the individual pollutant indices."""

    candidates = [
        result
        for value, pollutant in ((pm25, Pollutant.PM25), (pm10, Pollutant.PM10))
        if value is not None
        for result in (aqi_from_concentration(value, pollutant),)
        if result is not None
    ]
    return max(candidates, key=lambda r: r.value) if candidates else None


def concentration_from_aqi(aqi_value: float, pollutant: Pollutant) -> float | None:
    """Invert the AQI formula back to a concentration in ug/m3.

    Needed because not every provider publishes raw concentrations. WAQI, for
    one, reports ``iaqi.pm25`` as an *index* value. Storing that number as if it
    were micrograms would silently corrupt every training label, so anything
    arriving as an index is converted here first.

    The inverse is lossy at the edges, since AQI is a piecewise linear map onto
    integers, but it is the correct reading of the published number.
    """

    if aqi_value < 0 or math.isnan(aqi_value):
        return None

    breakpoints = _BREAKPOINTS[pollutant]
    if aqi_value > breakpoints[-1].aqi_high:
        return breakpoints[-1].conc_high

    for bp in breakpoints:
        if aqi_value <= bp.aqi_high:
            span_aqi = bp.aqi_high - bp.aqi_low
            span_conc = bp.conc_high - bp.conc_low
            fraction = 0.0 if span_aqi == 0 else (aqi_value - bp.aqi_low) / span_aqi
            return round(bp.conc_low + fraction * span_conc, 2)

    return breakpoints[-1].conc_high


def category_for_aqi(value: int) -> AqiCategory:
    """Bucket an already-computed AQI number. Used when charting a series."""

    thresholds = (
        (50, AqiCategory.GOOD),
        (100, AqiCategory.MODERATE),
        (150, AqiCategory.UNHEALTHY_SENSITIVE),
        (200, AqiCategory.UNHEALTHY),
        (300, AqiCategory.VERY_UNHEALTHY),
    )
    for ceiling, category in thresholds:
        if value <= ceiling:
            return category
    return AqiCategory.HAZARDOUS
