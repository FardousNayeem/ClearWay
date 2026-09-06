import type { AqiCategory } from "./types";

/**
 * The AQI scale is a published public-health standard, so its colours are
 * fixed here and never reused for anything else in the interface.
 */
export const AQI_TOKENS: Record<AqiCategory, { fill: string; ink: string }> = {
  good: { fill: "var(--aqi-good)", ink: "var(--aqi-good-ink)" },
  moderate: { fill: "var(--aqi-moderate)", ink: "var(--aqi-moderate-ink)" },
  unhealthy_sensitive: { fill: "var(--aqi-usg)", ink: "var(--aqi-usg-ink)" },
  unhealthy: { fill: "var(--aqi-unhealthy)", ink: "var(--aqi-unhealthy-ink)" },
  very_unhealthy: { fill: "var(--aqi-very)", ink: "var(--aqi-very-ink)" },
  hazardous: { fill: "var(--aqi-hazardous)", ink: "var(--aqi-hazardous-ink)" },
};

/** Upper AQI bound of each band, for drawing reference lines on a chart. */
export const AQI_BANDS: { category: AqiCategory; label: string; pm25: number }[] = [
  { category: "good", label: "Good", pm25: 9.0 },
  { category: "moderate", label: "Moderate", pm25: 35.4 },
  { category: "unhealthy_sensitive", label: "Sensitive groups", pm25: 55.4 },
  { category: "unhealthy", label: "Unhealthy", pm25: 125.4 },
  { category: "very_unhealthy", label: "Very unhealthy", pm25: 225.4 },
];

export function categoryForPm25(value: number): AqiCategory {
  for (const band of AQI_BANDS) {
    if (value <= band.pm25) return band.category;
  }
  return "hazardous";
}
