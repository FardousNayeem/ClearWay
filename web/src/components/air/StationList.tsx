import { AqiBadge } from "@/components/ui/AqiBadge";
import { relativeMinutes } from "@/lib/format";
import type { Station } from "@/lib/types";

export function StationList({ stations }: { stations: Station[] }) {
  if (stations.length === 0) {
    return (
      <p className="border-t border-line px-5 py-6 text-center text-[13px] text-muted">
        No station is close enough to this point. The nowcast falls back to the
        physics model, which is why it is labelled as modelled rather than measured.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-line border-t border-line">
      {stations.slice(0, 8).map((station) => (
        <li key={station.id} className="flex items-center justify-between gap-3 px-5 py-3">
          <div className="min-w-0">
            <p className="truncate text-[13.5px] text-ink">{station.name}</p>
            <p className="numeric text-[11.5px] text-muted">
              {station.provider}
              {station.distance_km !== null && ` · ${station.distance_km.toFixed(1)} km`}
              {station.observed_at &&
                ` · ${relativeMinutes(
                  (Date.now() - new Date(station.observed_at).getTime()) / 60000,
                )}`}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {station.pm25 !== null ? (
              <span className="numeric text-[13px] text-ink-soft">
                {station.pm25.toFixed(1)}
              </span>
            ) : (
              <span className="text-[12px] text-muted">no reading</span>
            )}
            {station.aqi && <AqiBadge aqi={station.aqi} />}
          </div>
        </li>
      ))}
    </ul>
  );
}
