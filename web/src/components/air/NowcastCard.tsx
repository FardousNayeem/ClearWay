"use client";

import { Info, MapPin } from "@phosphor-icons/react";
import { useState } from "react";

import { AqiBadge } from "@/components/ui/AqiBadge";
import { Panel } from "@/components/ui/primitives";
import { AQI_TOKENS } from "@/lib/aqi";
import { micrograms, relativeMinutes } from "@/lib/format";
import type { Nowcast } from "@/lib/types";

/**
 * The headline number, and immediately underneath it, where the number came
 * from. Provenance is not an advanced feature tucked behind a menu: an
 * estimate a person makes a health decision on has to be auditable.
 */
export function NowcastCard({ nowcast, place }: { nowcast: Nowcast; place: string }) {
  const [showSources, setShowSources] = useState(false);
  const tokens = AQI_TOKENS[nowcast.aqi.category];

  return (
    <Panel className="overflow-hidden">
      <div
        className="px-6 py-6"
        style={{ background: `color-mix(in srgb, ${tokens.fill} 14%, transparent)` }}
      >
        <div className="flex items-center gap-1.5 text-[13px] text-ink-soft">
          <MapPin size={14} weight="fill" />
          {place}
        </div>

        <div className="mt-3 flex flex-wrap items-end gap-x-5 gap-y-2">
          <div>
            <p className="numeric text-[56px] font-semibold leading-none" style={{ color: tokens.ink }}>
              {nowcast.aqi.value}
            </p>
            <p className="mt-1 text-[12px] uppercase tracking-wide text-muted">US AQI</p>
          </div>
          <div className="pb-1">
            <AqiBadge aqi={nowcast.aqi} size="md" />
            <p className="numeric mt-2 text-[13.5px] text-ink-soft">
              PM2.5 {micrograms(nowcast.pm25)}
              {nowcast.pm10 !== null && ` · PM10 ${micrograms(nowcast.pm10)}`}
            </p>
          </div>
        </div>

        <p className="mt-4 max-w-[62ch] text-[14px] leading-relaxed text-ink-soft">
          {nowcast.aqi.advice}
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-6 py-3">
        <p className="text-[12.5px] text-muted">
          {nowcast.source === "stations" ? (
            <>
              Measured by {nowcast.station_count}{" "}
              {nowcast.station_count === 1 ? "station" : "stations"}, nearest{" "}
              <span className="numeric">{nowcast.nearest_km?.toFixed(1)} km</span> away
            </>
          ) : (
            <>
              Modelled from CAMS. No monitoring station is close enough or recent
              enough to measure this point.
            </>
          )}
        </p>
        {nowcast.contributions.length > 0 && (
          <button
            type="button"
            onClick={() => setShowSources((open) => !open)}
            className="inline-flex items-center gap-1.5 text-[12.5px] text-accent hover:underline"
          >
            <Info size={14} />
            {showSources ? "Hide sources" : "Show sources"}
          </button>
        )}
      </div>

      {showSources && nowcast.contributions.length > 0 && (
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full min-w-[520px] text-left text-[12.5px]">
            <thead className="bg-sunk text-muted">
              <tr>
                <th scope="col" className="px-6 py-2 font-medium">Station</th>
                <th scope="col" className="px-3 py-2 font-medium">Network</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Distance</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Age</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">PM2.5</th>
                <th scope="col" className="px-6 py-2 text-right font-medium">Weight</th>
              </tr>
            </thead>
            <tbody>
              {nowcast.contributions.map((row) => (
                <tr key={row.station_id} className="border-t border-line">
                  <td className="px-6 py-2 text-ink">{row.name}</td>
                  <td className="px-3 py-2 text-muted">{row.provider}</td>
                  <td className="numeric px-3 py-2 text-right text-ink-soft">
                    {row.distance_km.toFixed(1)} km
                  </td>
                  <td className="numeric px-3 py-2 text-right text-ink-soft">
                    {relativeMinutes(row.age_minutes)}
                  </td>
                  <td className="numeric px-3 py-2 text-right text-ink-soft">
                    {row.pm25.toFixed(1)}
                  </td>
                  <td className="numeric px-6 py-2 text-right text-ink">
                    {(row.weight * 100).toFixed(0)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
