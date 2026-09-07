"use client";

import { useId, useState } from "react";

import type { HorizonMetrics } from "@/lib/types";

/**
 * Error against forecast lead time, for our model and the three baselines.
 *
 * Only two hues are used, and they carry the actual claim: ours against the
 * physics model it corrects. Persistence and climatology are drawn as neutral
 * dashed lines with direct labels, because four categorical colours cannot all
 * be told apart under deuteranopia, and reference lines do not need a hue.
 */
const SERIES = [
  { key: "model", label: "Clearway", colour: "var(--series-model)", dash: undefined, width: 2.5 },
  { key: "cams", label: "CAMS", colour: "var(--series-cams)", dash: "5 4", width: 2 },
  { key: "persistence", label: "Persistence", colour: "var(--series-muted)", dash: "2 3", width: 1.5 },
  { key: "climatology", label: "Climatology", colour: "var(--series-muted)", dash: "8 3 2 3", width: 1.5 },
] as const;

export function HorizonChart({
  byHorizon,
  metric = "mae",
  height = 300,
}: {
  byHorizon: Record<string, Record<string, HorizonMetrics>>;
  metric?: "mae" | "rmse";
  height?: number;
}) {
  const clipId = useId();
  const [hovered, setHovered] = useState<number | null>(null);

  const horizons = [
    ...new Set(
      Object.values(byHorizon).flatMap((series) => Object.keys(series).map(Number)),
    ),
  ].sort((a, b) => a - b);

  if (horizons.length === 0) return null;

  const width = 760;
  const pad = { top: 16, right: 96, bottom: 34, left: 52 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;

  const values = SERIES.flatMap((series) =>
    horizons.map((h) => byHorizon[series.key]?.[h]?.[metric]).filter((v): v is number => v != null),
  );
  const ceiling = niceCeiling(Math.max(...values, 1) * 1.1);

  const x = (horizon: number) =>
    pad.left +
    ((horizon - horizons[0]) / Math.max(1, horizons.at(-1)! - horizons[0])) * plotWidth;
  const y = (value: number) => pad.top + plotHeight - (value / ceiling) * plotHeight;

  return (
    <figure className="m-0">
      <div className="mb-3 flex flex-wrap gap-4 text-[12.5px]">
        {SERIES.map((series) => (
          <span key={series.key} className="flex items-center gap-1.5 text-ink-soft">
            <svg width="20" height="8" aria-hidden>
              <line
                x1="0"
                y1="4"
                x2="20"
                y2="4"
                stroke={series.colour}
                strokeWidth={series.width}
                strokeDasharray={series.dash}
              />
            </svg>
            {series.label}
          </span>
        ))}
      </div>

      <div className="relative">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full"
          style={{ height }}
          role="img"
          aria-label={`${metric.toUpperCase()} by forecast horizon for Clearway and three baselines`}
          onMouseLeave={() => setHovered(null)}
        >
          <defs>
            <clipPath id={clipId}>
              <rect x={pad.left} y={pad.top} width={plotWidth} height={plotHeight} />
            </clipPath>
          </defs>

          {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
            <g key={fraction}>
              <line
                x1={pad.left}
                x2={pad.left + plotWidth}
                y1={y(fraction * ceiling)}
                y2={y(fraction * ceiling)}
                stroke="var(--grid)"
              />
              <text
                x={pad.left - 8}
                y={y(fraction * ceiling) + 4}
                textAnchor="end"
                className="fill-[var(--muted)] text-[10px]"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {(fraction * ceiling).toFixed(0)}
              </text>
            </g>
          ))}

          {horizons
            .filter((_, index) => index % Math.max(1, Math.ceil(horizons.length / 8)) === 0)
            .map((horizon) => (
              <text
                key={horizon}
                x={x(horizon)}
                y={height - 12}
                textAnchor="middle"
                className="fill-[var(--muted)] text-[10px]"
              >
                {horizon}h
              </text>
            ))}

          <text
            x={pad.left + plotWidth / 2}
            y={height - 1}
            textAnchor="middle"
            className="fill-[var(--muted)] text-[10px]"
          >
            hours ahead
          </text>

          <g clipPath={`url(#${clipId})`}>
            {SERIES.map((series) => {
              const path = horizons
                .map((horizon, index) => {
                  const value = byHorizon[series.key]?.[horizon]?.[metric];
                  if (value == null) return null;
                  return `${index === 0 ? "M" : "L"}${x(horizon)},${y(value)}`;
                })
                .filter(Boolean)
                .join(" ");
              return (
                <path
                  key={series.key}
                  d={path}
                  fill="none"
                  stroke={series.colour}
                  strokeWidth={series.width}
                  strokeDasharray={series.dash}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              );
            })}
          </g>

          {/* Direct labels at the line ends, so identity never rests on colour
              alone for the two neutral series. */}
          {SERIES.map((series) => {
            const last = horizons.at(-1)!;
            const value = byHorizon[series.key]?.[last]?.[metric];
            if (value == null) return null;
            return (
              <text
                key={series.key}
                x={pad.left + plotWidth + 8}
                y={y(value) + 3.5}
                className="text-[10.5px]"
                fill={series.colour}
              >
                {series.label}
              </text>
            );
          })}

          {hovered !== null && (
            <line
              x1={x(hovered)}
              x2={x(hovered)}
              y1={pad.top}
              y2={pad.top + plotHeight}
              stroke="var(--line-strong)"
            />
          )}

          {horizons.map((horizon) => (
            <rect
              key={horizon}
              x={x(horizon) - plotWidth / horizons.length / 2}
              y={pad.top}
              width={plotWidth / horizons.length}
              height={plotHeight}
              fill="transparent"
              onMouseEnter={() => setHovered(horizon)}
            />
          ))}
        </svg>

        {hovered !== null && (
          <div
            className="pointer-events-none absolute top-2 rounded-md border border-line bg-surface px-3 py-2 shadow-[var(--shadow-md)]"
            style={{
              left: `${((x(hovered) - pad.left) / plotWidth) * 100}%`,
              transform: `translateX(${hovered > (horizons.at(-1) ?? 24) / 2 ? "-108%" : "6%"})`,
            }}
          >
            <p className="text-[11.5px] text-muted">{hovered} hours ahead</p>
            {SERIES.map((series) => {
              const value = byHorizon[series.key]?.[hovered]?.[metric];
              if (value == null) return null;
              return (
                <p key={series.key} className="mt-0.5 flex items-center gap-2 text-[12px]">
                  <span className="text-muted">{series.label}</span>
                  <span className="numeric ml-auto pl-3 text-ink">{value.toFixed(2)}</span>
                </p>
              );
            })}
          </div>
        )}
      </div>
    </figure>
  );
}

function niceCeiling(peak: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(peak, 1)));
  return Math.ceil(peak / magnitude) * magnitude;
}
