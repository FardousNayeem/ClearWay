/** Mirrors the Clearway API schemas. Kept in step with backend/app/schemas. */

export type AqiCategory =
  | "good"
  | "moderate"
  | "unhealthy_sensitive"
  | "unhealthy"
  | "very_unhealthy"
  | "hazardous";

export type Aqi = {
  value: number;
  category: AqiCategory;
  label: string;
  advice: string;
  pollutant: string;
};

export type City = {
  slug: string;
  name: string;
  country: string;
  latitude: number;
  longitude: number;
};

export type Place = {
  name: string;
  country: string | null;
  latitude: number;
  longitude: number;
  timezone: string | null;
  population: number | null;
};

export type Contribution = {
  station_id: number;
  name: string;
  provider: string;
  distance_km: number;
  age_minutes: number;
  weight: number;
  pm25: number;
};

export type Nowcast = {
  latitude: number;
  longitude: number;
  observed_at: string;
  pm25: number;
  pm10: number | null;
  aqi: Aqi;
  /** "stations" when interpolated from real readings, "cams" when modelled. */
  source: "stations" | "cams";
  station_count: number;
  nearest_km: number | null;
  max_age_minutes: number | null;
  contributions: Contribution[];
};

export type ForecastPoint = {
  valid_at: string;
  pm25: number;
  pm25_low: number | null;
  pm25_high: number | null;
  cams_pm25: number | null;
  aqi: Aqi;
};

export type Forecast = {
  latitude: number;
  longitude: number;
  issued_at: string;
  model_version: number | null;
  estimator: string;
  horizon_hours: number;
  station_id: number | null;
  station_name: string | null;
  points: ForecastPoint[];
};

export type Station = {
  id: number;
  name: string;
  provider: string;
  city_slug: string;
  latitude: number;
  longitude: number;
  elevation_m: number | null;
  distance_km: number | null;
  pm25: number | null;
  pm10: number | null;
  observed_at: string | null;
  aqi: Aqi | null;
};

export type BestHour = {
  valid_at: string;
  pm25: number;
  aqi: Aqi;
  rank: number;
};

export type ClearWindow = {
  starts_at: string;
  ends_at: string;
  hours: number;
  peak_pm25: number;
};

export type Guidance = {
  latitude: number;
  longitude: number;
  sensitivity: "general" | "sensitive" | "athlete";
  threshold_pm25: number;
  issued_at: string;
  best_hours: BestHour[];
  clear_windows: ClearWindow[];
  advice: string;
};

export type ModelVersion = {
  id: number;
  name: string;
  version: number;
  algorithm: string;
  trained_at: string;
  training_rows: number;
  window_start: string;
  window_end: string;
  is_active: boolean;
  metrics: Record<string, unknown>;
  notes: string;
};

export type HorizonMetrics = {
  mae: number;
  rmse: number;
  bias: number;
  sample_size: number;
  coverage_80: number | null;
};

export type Scorecard = {
  window_days: number;
  active_version: number | null;
  estimators: string[];
  by_horizon: Record<string, Record<string, HorizonMetrics>>;
};

export type Health = {
  status: string;
  database: string;
  ground_truth_providers: string[];
  active_model_version: number | null;
};
