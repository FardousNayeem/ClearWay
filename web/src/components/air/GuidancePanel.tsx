"use client";

import { CalendarCheck } from "@phosphor-icons/react";

import { AqiBadge } from "@/components/ui/AqiBadge";
import { Chip, Panel, PanelHeader } from "@/components/ui/primitives";
import { hour } from "@/lib/format";
import type { Guidance } from "@/lib/types";

const PROFILES = [
  { key: "general", label: "Most people" },
  { key: "sensitive", label: "Asthma or heart condition" },
  { key: "athlete", label: "Hard exercise" },
] as const;

/**
 * The layer that turns a chart into a decision. A 24-hour line is data;
 * "the air is clear between 04:00 and 07:00" is an answer.
 */
export function GuidancePanel({
  guidance,
  sensitivity,
  onSensitivityChange,
  timeZone,
  zone,
}: {
  guidance: Guidance;
  sensitivity: string;
  onSensitivityChange: (value: string) => void;
  /** IANA zone of the place being shown; undefined means the viewer's own. */
  timeZone?: string;
  /** Its short name, e.g. "GMT+6". This panel is where an hour becomes a
   *  plan, so the hours on it must not be ambiguous. */
  zone?: string;
}) {
  return (
    <Panel>
      <PanelHeader
        title="When to go outside"
        description={
          `Thresholds follow the US EPA categories. Yours is ` +
          `${guidance.threshold_pm25} µg/m³. Times in local time${zone ? ` (${zone})` : ""}.`
        }
      />

      <div className="flex flex-wrap gap-1.5 border-b border-line px-5 py-3">
        {PROFILES.map((profile) => (
          <Chip
            key={profile.key}
            active={sensitivity === profile.key}
            onClick={() => onSensitivityChange(profile.key)}
          >
            {profile.label}
          </Chip>
        ))}
      </div>

      <div className="px-5 py-4">
        <p className="max-w-[68ch] text-[14px] leading-relaxed text-ink-soft">
          {guidance.advice}
        </p>

        {guidance.clear_windows.length > 0 && (
          <div className="mt-4">
            <h3 className="text-[12.5px] font-medium text-muted">Clear windows</h3>
            <ul className="mt-2 grid gap-2 sm:grid-cols-2">
              {guidance.clear_windows.map((window) => (
                <li
                  key={window.starts_at}
                  className="flex items-center gap-3 rounded-md border border-line bg-sunk px-3 py-2.5"
                >
                  <CalendarCheck size={17} className="shrink-0 text-accent" weight="duotone" />
                  <div>
                    <p className="numeric text-[13.5px] text-ink">
                      {hour(window.starts_at, timeZone)} to {hour(window.ends_at, timeZone)}
                    </p>
                    <p className="numeric text-[11.5px] text-muted">
                      {window.hours} h, peaking at {window.peak_pm25} µg/m³
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {guidance.best_hours.length > 0 && (
          <div className="mt-4">
            <h3 className="text-[12.5px] font-medium text-muted">Cleanest hours ahead</h3>
            <ol className="mt-2 divide-y divide-line">
              {guidance.best_hours.map((entry) => (
                <li
                  key={entry.valid_at}
                  className="flex items-center justify-between gap-3 py-2.5"
                >
                  <span className="flex items-center gap-3">
                    <span className="numeric w-5 text-[12px] text-muted">{entry.rank}</span>
                    <span className="numeric text-[13.5px] text-ink">
                      {hour(entry.valid_at, timeZone)}
                    </span>
                  </span>
                  <span className="flex items-center gap-3">
                    <span className="numeric text-[13px] text-ink-soft">
                      {entry.pm25.toFixed(1)} µg/m³
                    </span>
                    <AqiBadge aqi={entry.aqi} />
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </Panel>
  );
}
