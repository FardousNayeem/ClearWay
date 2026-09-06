"use client";

import { useId, useState } from "react";

import { AQI_BANDS, AQI_TOKENS, categoryForPm25 } from "@/lib/aqi";
import { hour, micrograms } from "@/lib/format";
import type { ForecastPoint } from "@/lib/types";

/**
 * PM2.5 over the next day.
 *
 * One measure, so no categorical palette is needed: the AQI bands behind the
 * plot carry the meaning, the model line is the accent, and CAMS sits
 * alongside as the thing being improved on. Hover gives a crosshair and a
 * readout; the same numbers are available as a table underneath.
 */
export function ForecastChart({
  points,
  showCams = true,
  height = 260,
}: {
  points: ForecastPoint[];
  showCams?: boolean;
  height?: number;
}) {
  const clipId = useId();
  const [hovered, setHovered] = useState<number | null>(null);

  if (points.length === 0) return null;

  const width = 760;
  const pad = { top: 14, right: 18, bottom: 28, left: 46 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;

  const values = points.flatMap((p) => [
    p.pm25,
    p.pm25_high ?? p.pm25,
    showCams && p.cams_pm25 !== null ? p.cams_pm25 : p.pm25,
  ]);
  const peak = Math.max(...values, 12);
  const ceiling = niceCeiling(peak * 1.12);

  const x = (index: number) =>
    pad.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
  const y = (value: number) => pad.top + plotHeight - (value / ceiling) * plotHeight;

  const hasBand = points.some((p) => p.pm25_low !== null && p.pm25_high !== null);
  const bandPath = hasBand
    ? [
        ...points.map(
          (p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.pm25_high ?? p.pm25)}`,
        ),
        ...points
          .map((p, i) => `L${x(points.length - 1 - i)},${y(points[points.length - 1 - i].pm25_low ?? p.pm25)}`)
          .slice(1),
        "Z",
      ].join(" ")
    : "";

  const stride = Math.max(1, Math.ceil(points.length / 8));
  const active = hovered === null ? null : points[hovered];

  return (
    <figure className="m-0">
      <div className="mb-3 flex flex-wrap items-center gap-4 text-[12.5px]">
        <Legend colour="var(--series-model)" label="ClearWay forecast" />
        {showCams && points.some((p) => p.cams_pm25 !== null) && (
          <Legend colour="var(--series-cams)" label="CAMS, uncorrected" dashed />
        )}
        {hasBand && (
          <span className="flex items-center gap-1.5 text-muted">
            <span
              aria-hidden
              className="h-2.5 w-4 rounded-[2px]"
              style={{ background: "color-mix(in srgb, var(--series-model) 22%, transparent)" }}
            />
            80% range
          </span>
        )}
      </div>

      <div className="relative">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full"
          style={{ height }}
          role="img"
          aria-label={`PM2.5 forecast for the next ${points.length} hours`}
          onMouseLeave={() => setHovered(null)}
        >
          <defs>
            <clipPath id={clipId}>
              <rect x={pad.left} y={pad.top} width={plotWidth} height={plotHeight} />
            </clipPath>
          </defs>

          {/* AQI bands sit behind the data so a reader can see which category
              the line is in without consulting a legend. */}
          <g clipPath={`url(#${clipId})`}>
            {AQI_BANDS.map((band, index) => {
              const lower = index === 0 ? 0 : AQI_BANDS[index - 1].pm25;
              if (lower >= ceiling) return null;
              const top = y(Math.min(band.pm25, ceiling));
              return (
                <rect
                  key={band.category}
                  x={pad.left}
                  y={top}
                  width={plotWidth}
                  height={Math.max(0, y(lower) - top)}
                  fill={AQI_TOKENS[band.category].fill}
                  opacity={0.1}
                />
              );
            })}
          </g>

          {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
            const value = fraction * ceiling;
            return (
              <g key={fraction}>
                <line
                  x1={pad.left}
                  x2={width - pad.right}
                  y1={y(value)}
                  y2={y(value)}
                  stroke="var(--grid)"
                  strokeWidth={1}
                />
                <text
                  x={pad.left - 8}
                  y={y(value) + 4}
                  textAnchor="end"
                  className="fill-[var(--muted)] text-[10px]"
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {Math.round(value)}
                </text>
              </g>
            );
          })}

          {points.map((point, index) =>
            index % stride === 0 ? (
              <text
                key={point.valid_at}
                x={x(index)}
                y={height - 8}
                textAnchor="middle"
                className="fill-[var(--muted)] text-[10px]"
              >
                {hour(point.valid_at)}
              </text>
            ) : null,
          )}

          <g clipPath={`url(#${clipId})`}>
            {hasBand && (
              <path d={bandPath} fill="var(--series-model)" opacity={0.16} stroke="none" />
            )}

            {showCams && (
              <path
                d={linePath(points.map((p) => p.cams_pm25), x, y)}
                fill="none"
                stroke="var(--series-cams)"
                strokeWidth={1.75}
                strokeDasharray="5 4"
                strokeLinecap="round"
              />
            )}

            <path
              d={linePath(points.map((p) => p.pm25), x, y)}
              fill="none"
              stroke="var(--series-model)"
              strokeWidth={2.25}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </g>

          {active && hovered !== null && (
            <g>
              <line
                x1={x(hovered)}
                x2={x(hovered)}
                y1={pad.top}
                y2={pad.top + plotHeight}
                stroke="var(--line-strong)"
                strokeWidth={1}
              />
              <circle
                cx={x(hovered)}
                cy={y(active.pm25)}
                r={4.5}
                fill="var(--series-model)"
                stroke="var(--surface)"
                strokeWidth={2}
              />
            </g>
          )}

          {points.map((point, index) => (
            <rect
              key={point.valid_at}
              x={x(index) - plotWidth / points.length / 2}
              y={pad.top}
              width={plotWidth / points.length}
              height={plotHeight}
              fill="transparent"
              onMouseEnter={() => setHovered(index)}
            />
          ))}
        </svg>

        {active && hovered !== null && (
          <div
            className="pointer-events-none absolute top-1 min-w-[168px] rounded-md border border-line bg-surface px-3 py-2 shadow-[var(--shadow-md)]"
            style={{
              left: `${((x(hovered) - pad.left) / plotWidth) * 100}%`,
              transform: `translateX(${hovered > points.length / 2 ? "-105%" : "5%"})`,
            }}
          >
            <p className="text-[11.5px] text-muted">{hour(active.valid_at)}</p>
            <p className="numeric mt-0.5 text-[14px] font-medium text-ink">
              {micrograms(active.pm25)}
            </p>
            <p className="text-[11.5px]" style={{ color: AQI_TOKENS[active.aqi.category].ink }}>
              {active.aqi.label}
            </p>
            {active.pm25_low !== null && active.pm25_high !== null && (
              <p className="numeric mt-1 text-[11px] text-muted">
                80% range {active.pm25_low.toFixed(0)} to {active.pm25_high.toFixed(0)}
              </p>
            )}
            {showCams && active.cams_pm25 !== null && (
              <p className="numeric text-[11px]" style={{ color: "var(--series-cams)" }}>
                CAMS {active.cams_pm25.toFixed(1)}
              </p>
            )}
          </div>
        )}
      </div>

      <details className="mt-3">
        <summary className="cursor-pointer text-[12.5px] text-muted hover:text-ink">
          View as a table
        </summary>
        <div className="mt-2 max-h-64 overflow-auto rounded-sm border border-line">
          <table className="w-full text-left text-[12.5px]">
            <thead className="sticky top-0 bg-sunk text-muted">
              <tr>
                <th scope="col" className="px-3 py-1.5 font-medium">Hour</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">PM2.5</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">80% range</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">CAMS</th>
                <th scope="col" className="px-3 py-1.5 font-medium">Category</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.valid_at} className="border-t border-line">
                  <td className="numeric px-3 py-1.5 text-ink-soft">{hour(point.valid_at)}</td>
                  <td className="numeric px-3 py-1.5 text-right text-ink">
                    {point.pm25.toFixed(1)}
                  </td>
                  <td className="numeric px-3 py-1.5 text-right text-muted">
                    {point.pm25_low !== null && point.pm25_high !== null
                      ? `${point.pm25_low.toFixed(0)} to ${point.pm25_high.toFixed(0)}`
                      : "not modelled"}
                  </td>
                  <td className="numeric px-3 py-1.5 text-right text-muted">
                    {point.cams_pm25 !== null ? point.cams_pm25.toFixed(1) : "-"}
                  </td>
                  <td
                    className="px-3 py-1.5"
                    style={{ color: AQI_TOKENS[categoryForPm25(point.pm25)].ink }}
                  >
                    {point.aqi.label}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

function Legend({ colour, label, dashed }: { colour: string; label: string; dashed?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-ink-soft">
      <svg width="18" height="8" aria-hidden>
        <line
          x1="0"
          y1="4"
          x2="18"
          y2="4"
          stroke={colour}
          strokeWidth="2.25"
          strokeDasharray={dashed ? "5 4" : undefined}
        />
      </svg>
      {label}
    </span>
  );
}

function linePath(
  values: (number | null)[],
  x: (i: number) => number,
  y: (v: number) => number,
): string {
  let path = "";
  let pen = false;
  values.forEach((value, index) => {
    if (value === null) {
      pen = false;
      return;
    }
    path += `${pen ? "L" : "M"}${x(index)},${y(value)} `;
    pen = true;
  });
  return path.trim();
}

function niceCeiling(peak: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(peak, 1)));
  return Math.ceil(peak / magnitude) * magnitude;
}
