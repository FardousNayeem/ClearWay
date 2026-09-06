"use client";

import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";

import { AQI_TOKENS, categoryForPm25 } from "@/lib/aqi";
import type { Station } from "@/lib/types";

import "maplibre-gl/dist/maplibre-gl.css";

/**
 * Basemap tiles from OpenFreeMap: OpenStreetMap data, no API key, no request
 * limits. MapLibre adds the required attribution itself.
 *
 * Markers are coloured by the AQI band of the station's own reading, so the
 * map uses the same public-health scale as every other surface. A station with
 * no fresh reading is drawn hollow rather than green, because "we do not know"
 * and "the air is clean" must never look the same.
 */
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";

export function StationMap({
  centre,
  stations,
  onSelect,
  height = 380,
}: {
  centre: { latitude: number; longitude: number };
  stations: Station[];
  onSelect?: (station: Station) => void;
  height?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);

  useEffect(() => {
    if (!container.current || map.current) return;

    map.current = new maplibregl.Map({
      container: container.current,
      style: STYLE_URL,
      center: [centre.longitude, centre.latitude],
      zoom: 10,
      attributionControl: { compact: true },
    });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    return () => {
      map.current?.remove();
      map.current = null;
    };
    // Created once; the centre is moved by the effect below rather than by
    // rebuilding the map, which would drop the user's pan and zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    map.current?.easeTo({
      center: [centre.longitude, centre.latitude],
      duration: 700,
    });
  }, [centre.latitude, centre.longitude]);

  useEffect(() => {
    if (!map.current) return;

    markers.current.forEach((marker) => marker.remove());
    markers.current = [];

    for (const station of stations) {
      const element = document.createElement("button");
      element.type = "button";
      element.setAttribute(
        "aria-label",
        station.pm25 === null
          ? `${station.name}, no recent reading`
          : `${station.name}, ${station.pm25.toFixed(1)} micrograms per cubic metre`,
      );

      const known = station.pm25 !== null;
      const fill = known ? AQI_TOKENS[categoryForPm25(station.pm25!)].fill : "transparent";
      element.style.cssText = `
        width: 20px; height: 20px; border-radius: 999px; cursor: pointer;
        background: ${fill};
        border: 2px solid ${known ? "var(--surface)" : "var(--line-strong)"};
        box-shadow: 0 0 0 1px rgb(0 0 0 / 0.25);
      `;
      if (onSelect) element.addEventListener("click", () => onSelect(station));

      const marker = new maplibregl.Marker({ element })
        .setLngLat([station.longitude, station.latitude])
        .setPopup(
          new maplibregl.Popup({ offset: 14, closeButton: false }).setHTML(
            `<div style="font: 12px/1.4 system-ui; color:#111">
               <strong>${escapeHtml(station.name)}</strong><br/>
               ${
                 known
                   ? `PM2.5 ${station.pm25!.toFixed(1)} µg/m³`
                   : "No recent reading"
               }<br/>
               <span style="opacity:.7">${escapeHtml(station.provider)}</span>
             </div>`,
          ),
        )
        .addTo(map.current);
      markers.current.push(marker);
    }
  }, [stations, onSelect]);

  return (
    <div
      ref={container}
      style={{ height }}
      className="w-full overflow-hidden rounded-lg border border-line"
    />
  );
}

function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ] as string,
  );
}
