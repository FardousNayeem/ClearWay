"""Turning a forecast into a decision.

A 24-hour chart is data. "Go for your run between 6 and 8 tonight" is an
answer. This service is the layer that converts one into the other, and it is
the reason the project drops turn-by-turn routing without losing the point:
*when* to go out matters more than *which street* to take.

Thresholds follow the US EPA categories, chosen per sensitivity profile.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from enum import StrEnum

from app.domain.aqi import AqiCategory, Pollutant, aqi_from_concentration
from app.domain.geo import Point
from app.schemas.common import BestHourSchema, ForecastSchema, GuidanceSchema

logger = logging.getLogger(__name__)


class Sensitivity(StrEnum):
    GENERAL = "general"
    SENSITIVE = "sensitive"
    ATHLETE = "athlete"


@dataclass(frozen=True, slots=True)
class Profile:
    #: The PM2.5 ceiling this group should stay under, in ug/m3.
    threshold: float
    advice: str


#: Thresholds are the upper bound of an EPA category, not invented numbers.
PROFILES: dict[Sensitivity, Profile] = {
    # Top of "Moderate".
    Sensitivity.GENERAL: Profile(
        35.4, "Fine for most people. Anything under this is comfortable outdoors."
    ),
    # Top of "Good": asthma, heart conditions, children, older adults.
    Sensitivity.SENSITIVE: Profile(
        9.0, "You react earlier than most, so the bar is the strictest category."
    ),
    # Hard effort means a much higher breathing rate, so the same air does more.
    Sensitivity.ATHLETE: Profile(
        20.0, "Hard effort multiplies your intake, so aim well below the general limit."
    ),
}

MIN_WINDOW_HOURS = 2


class GuidanceService:
    def best_hours(
        self,
        point: Point,
        forecast: ForecastSchema,
        sensitivity: Sensitivity = Sensitivity.GENERAL,
        limit: int = 5,
    ) -> GuidanceSchema:
        profile = PROFILES[sensitivity]
        points = sorted(forecast.points, key=lambda p: p.valid_at)

        ranked = sorted(points, key=lambda p: p.pm25)[:limit]
        best = [
            BestHourSchema(
                valid_at=entry.valid_at,
                pm25=entry.pm25,
                aqi=entry.aqi,
                rank=index + 1,
            )
            for index, entry in enumerate(ranked)
        ]

        return GuidanceSchema(
            latitude=point.latitude,
            longitude=point.longitude,
            sensitivity=sensitivity.value,
            threshold_pm25=profile.threshold,
            issued_at=forecast.issued_at,
            best_hours=best,
            clear_windows=self._windows(points, profile.threshold),
            advice=self._advice(points, profile, ranked),
        )

    @staticmethod
    def _windows(points: list, threshold: float) -> list[dict]:
        """Runs of consecutive hours under the threshold.

        A run matters more than a single clean hour: nobody schedules a walk
        around one 60-minute dip.
        """

        windows: list[dict] = []
        run: list = []

        def close(run: list) -> None:
            if len(run) >= MIN_WINDOW_HOURS:
                windows.append(
                    {
                        "starts_at": run[0].valid_at,
                        "ends_at": run[-1].valid_at + dt.timedelta(hours=1),
                        "hours": len(run),
                        "peak_pm25": round(max(p.pm25 for p in run), 1),
                    }
                )

        for entry in points:
            if entry.pm25 <= threshold:
                run.append(entry)
            else:
                close(run)
                run = []
        close(run)
        return windows

    @staticmethod
    def _advice(points: list, profile: Profile, ranked: list) -> str:
        if not points:
            return "No forecast is available for this location yet."

        clean = [p for p in points if p.pm25 <= profile.threshold]
        if not clean:
            floor = min(points, key=lambda p: p.pm25)
            result = aqi_from_concentration(floor.pm25, Pollutant.PM25)
            band = result.label.lower() if result else "elevated"
            return (
                f"Nothing in the next {len(points)} hours drops below your limit of "
                f"{profile.threshold:g} ug/m3. The cleanest hour is still {band}, at "
                f"{floor.pm25:g} ug/m3. If it can wait, wait."
            )

        best = ranked[0]
        share = round(100 * len(clean) / len(points))
        return (
            f"{share}% of the next {len(points)} hours sit under your "
            f"{profile.threshold:g} ug/m3 limit. The cleanest is "
            f"{best.valid_at:%H:%M} UTC at {best.pm25:g} ug/m3. {profile.advice}"
        )


def worst_category(points: list) -> AqiCategory | None:
    """The worst band in a forecast, for a headline warning."""

    categories = [
        result.category
        for point in points
        if (result := aqi_from_concentration(point.pm25, Pollutant.PM25)) is not None
    ]
    if not categories:
        return None
    order = list(AqiCategory)
    return max(categories, key=order.index)
