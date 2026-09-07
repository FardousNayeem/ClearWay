"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";

import { Alert, Panel, PanelHeader, SkeletonPanel } from "@/components/ui/primitives";
import { get, query } from "@/lib/api";
import { isApiError } from "@/lib/errors";
import { hour, zoneLabel } from "@/lib/format";
import type { City, Forecast, Guidance, Nowcast, Station } from "@/lib/types";

import { ForecastChart } from "./ForecastChart";
import { GuidancePanel } from "./GuidancePanel";
import { type Location, PlaceSearch } from "./PlaceSearch";
import { StationList } from "./StationList";

// MapLibre reaches for `window` at import time, so it must never be part of
// the server bundle.
const StationMap = dynamic(
  () => import("@/components/map/StationMap").then((module) => module.StationMap),
  {
    ssr: false,
    loading: () => <div className="skeleton h-[380px] w-full rounded-lg" />,
  },
);

const FALLBACK: Location = {
  name: "Dhaka, BD",
  latitude: 23.8103,
  longitude: 90.4125,
  timezone: "Asia/Dhaka",
};

export function AirDashboard({ cities }: { cities: City[] }) {
  const [location, setLocation] = useState<Location>(() =>
    cities.length > 0
      ? {
          name: `${cities[0].name}, ${cities[0].country}`,
          latitude: cities[0].latitude,
          longitude: cities[0].longitude,
          timezone: cities[0].timezone,
        }
      : FALLBACK,
  );
  const [sensitivity, setSensitivity] = useState("general");

  const [nowcast, setNowcast] = useState<Nowcast | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [guidance, setGuidance] = useState<Guidance | null>(null);
  const [stations, setStations] = useState<Station[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const coordinates = query({
    latitude: location.latitude,
    longitude: location.longitude,
  });

  // Undefined rather than null, because that is what `Intl` reads as "use the
  // viewer's own zone" - the only honest fallback for a place whose zone the
  // upstream did not give us.
  const timeZone = location.timezone ?? undefined;
  const zone = zoneLabel(timeZone);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [now, next, near] = await Promise.all([
        get<Nowcast>(`/air/now?${coordinates}`),
        get<Forecast>(`/air/forecast?${coordinates}`),
        get<Station[]>(
          `/stations?${query({
            near_lat: location.latitude,
            near_lon: location.longitude,
          })}`,
        ),
      ]);
      setNowcast(now);
      setForecast(next);
      setStations(near);
    } catch (caught) {
      setError(isApiError(caught) ? caught.friendly : "Something went wrong.");
      setNowcast(null);
      setForecast(null);
    } finally {
      setLoading(false);
    }
  }, [coordinates, location.latitude, location.longitude]);

  useEffect(() => {
    void load();
  }, [load]);

  // Guidance depends on the sensitivity profile, so it reloads on its own
  // rather than forcing the whole dashboard to refetch.
  useEffect(() => {
    let cancelled = false;
    get<Guidance>(`/air/guidance?${coordinates}&${query({ sensitivity })}`)
      .then((result) => !cancelled && setGuidance(result))
      .catch(() => !cancelled && setGuidance(null));
    return () => {
      cancelled = true;
    };
  }, [coordinates, sensitivity]);

  return (
    <div className="mt-8 grid gap-5">
      <PlaceSearch cities={cities} current={location} onSelect={setLocation} />

      {error && <Alert>{error}</Alert>}

      <div className="grid gap-5 lg:grid-cols-[1.15fr_1fr]">
        <div className="grid content-start gap-5">
          {loading && !nowcast ? (
            <Panel>
              <SkeletonPanel rows={4} />
            </Panel>
          ) : nowcast ? (
            <NowcastCardLazy nowcast={nowcast} place={location.name} />
          ) : null}

          <Panel>
            <PanelHeader
              title="Next 24 hours"
              description={
                forecast
                  ? forecast.estimator === "model"
                    ? `Corrected forecast from model v${forecast.model_version}, issued ${hour(forecast.issued_at, timeZone)}, anchored on ${forecast.station_name}. Times in local time${zone && ` (${zone})`}.`
                    : `Raw CAMS output. No corrected model is trained for this location yet. Times in local time${zone && ` (${zone})`}.`
                  : undefined
              }
            />
            <div className="p-5">
              {loading && !forecast ? (
                <SkeletonPanel rows={3} />
              ) : forecast && forecast.points.length > 0 ? (
                <ForecastChart points={forecast.points} timeZone={timeZone} />
              ) : (
                <p className="py-8 text-center text-[13.5px] text-muted">
                  No forecast covers this location yet.
                </p>
              )}
            </div>
          </Panel>
        </div>

        <div className="grid content-start gap-5">
          {guidance && (
            <GuidancePanel
              guidance={guidance}
              sensitivity={sensitivity}
              onSensitivityChange={setSensitivity}
              timeZone={timeZone}
              zone={zone}
            />
          )}

          <Panel className="overflow-hidden">
            <PanelHeader
              title="Monitoring stations"
              description={
                stations.length > 0
                  ? `${stations.length} within range`
                  : "None within range of this point"
              }
            />
            <div className="p-3">
              <StationMap
                centre={location}
                stations={stations}
                onSelect={(station) =>
                  setLocation({
                    name: station.name,
                    latitude: station.latitude,
                    longitude: station.longitude,
                    // A station a few km away is in the same zone as the place
                    // already selected, so keeping that beats losing the zone
                    // for a network that does not report one.
                    timezone: station.timezone ?? location.timezone,
                  })
                }
              />
            </div>
            <StationList stations={stations} />
          </Panel>
        </div>
      </div>
    </div>
  );
}

/** Split out so the heavy provenance table is not in the first paint path. */
const NowcastCardLazy = dynamic(
  () => import("./NowcastCard").then((module) => module.NowcastCard),
  { loading: () => <div className="skeleton h-[260px] w-full rounded-lg" /> },
);
