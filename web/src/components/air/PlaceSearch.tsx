"use client";

import { MagnifyingGlass } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { get, query } from "@/lib/api";
import { cn } from "@/lib/format";
import type { City, Place } from "@/lib/types";

export type Location = {
  name: string;
  latitude: number;
  longitude: number;
  /**
   * IANA zone of the place itself, carried alongside the coordinates because
   * every hour the dashboard shows is an hour here, not an hour wherever the
   * browser is. Null when the upstream did not give one.
   */
  timezone: string | null;
};

export function PlaceSearch({
  cities,
  current,
  onSelect,
}: {
  cities: City[];
  current: Location;
  onSelect: (location: Location) => void;
}) {
  const [term, setTerm] = useState("");
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (term.trim().length < 2) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      get<Place[]>(`/places/search?${query({ q: term })}`, { signal: controller.signal })
        .then(setResults)
        .catch(() => setResults([]));
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [term]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  return (
    <div className="grid gap-3">
      <div ref={container} className="relative">
        <label className="relative flex items-center">
          <MagnifyingGlass size={16} className="absolute left-3 text-muted" aria-hidden />
          <span className="sr-only">Search for a place</span>
          <input
            type="search"
            value={term}
            placeholder="Search any city"
            onChange={(event) => {
              setTerm(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            className="h-10 w-full rounded-md border border-line-strong bg-surface pl-9 pr-3 text-[14px] text-ink placeholder:text-muted focus:border-accent focus:outline-none"
          />
        </label>

        {open && results.length > 0 && (
          <ul className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border border-line bg-surface shadow-[var(--shadow-md)]">
            {results.map((place) => (
              <li key={`${place.name}-${place.latitude}-${place.longitude}`}>
                <button
                  type="button"
                  onClick={() => {
                    onSelect({
                      name: place.country ? `${place.name}, ${place.country}` : place.name,
                      latitude: place.latitude,
                      longitude: place.longitude,
                      timezone: place.timezone,
                    });
                    setTerm("");
                    setOpen(false);
                  }}
                  className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left hover:bg-sunk"
                >
                  <span className="text-[13.5px] text-ink">{place.name}</span>
                  <span className="numeric text-[11.5px] text-muted">
                    {place.country ?? ""}{" "}
                    {place.population ? `· ${(place.population / 1000).toFixed(0)}k` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {cities.map((city) => {
          const active = city.name === current.name.split(",")[0];
          return (
            <button
              key={city.slug}
              type="button"
              aria-pressed={active}
              onClick={() =>
                onSelect({
                  name: `${city.name}, ${city.country}`,
                  latitude: city.latitude,
                  longitude: city.longitude,
                  timezone: city.timezone,
                })
              }
              className={cn(
                "rounded-sm border px-2.5 py-1.5 text-[12.5px] transition-colors",
                active
                  ? "border-accent bg-accent-quiet text-accent"
                  : "border-line bg-surface text-muted hover:text-ink",
              )}
            >
              {city.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}
