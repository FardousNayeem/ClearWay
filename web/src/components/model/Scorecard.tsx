"use client";

import { useEffect, useState } from "react";

import { Alert, Chip, EmptyState, Panel, PanelHeader, SkeletonPanel } from "@/components/ui/primitives";
import { get, query } from "@/lib/api";
import { isApiError } from "@/lib/errors";
import { dayShort } from "@/lib/format";
import type { ModelVersion, Scorecard as ScorecardData } from "@/lib/types";

import { HorizonChart } from "./HorizonChart";

const WINDOWS = [7, 30, 90] as const;
const METRICS = [
  { key: "mae", label: "Mean absolute error" },
  { key: "rmse", label: "Root mean squared error" },
] as const;

const BASELINE_NOTES: Record<string, string> = {
  persistence:
    "The last observed value, carried forward. Very strong at one to three hours, because pollution is highly autocorrelated.",
  climatology:
    "The station's own average for that hour of the day. Captures the daily cycle and nothing else.",
  cams: "The Copernicus physics model, unmodified. This is what Clearway is correcting.",
};

export function Scorecard() {
  const [days, setDays] = useState<number>(30);
  const [metric, setMetric] = useState<"mae" | "rmse">("mae");
  const [data, setData] = useState<ScorecardData | null>(null);
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      get<ScorecardData>(`/models/scorecard?${query({ days })}`),
      get<ModelVersion[]>(`/models?${query({ limit: 20 })}`),
    ])
      .then(([scorecard, history]) => {
        if (cancelled) return;
        setData(scorecard);
        setVersions(history);
        setError(null);
      })
      .catch((caught) => {
        if (cancelled) return;
        setError(isApiError(caught) ? caught.friendly : "The scorecard did not load.");
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [days]);

  const hasScores = data && Object.keys(data.by_horizon).length > 0;

  return (
    <div className="mt-8 grid gap-5">
      {error && <Alert>{error}</Alert>}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1.5" role="group" aria-label="Scoring window">
          {WINDOWS.map((window) => (
            <Chip key={window} active={days === window} onClick={() => setDays(window)}>
              Last {window} days
            </Chip>
          ))}
        </div>
        <div className="flex gap-1.5" role="group" aria-label="Metric">
          {METRICS.map((entry) => (
            <Chip
              key={entry.key}
              active={metric === entry.key}
              onClick={() => setMetric(entry.key)}
            >
              {entry.label}
            </Chip>
          ))}
        </div>
      </div>

      <Panel>
        <PanelHeader
          title="Error against forecast lead time"
          description="Lower is better. Averaged over the window and weighted by how many forecasts each day contributed."
        />
        <div className="p-5">
          {loading && !data ? (
            <SkeletonPanel rows={4} />
          ) : hasScores ? (
            <HorizonChart byHorizon={data.by_horizon} metric={metric} />
          ) : (
            <EmptyState
              title="Nothing scored yet"
              description="Scores appear after the first full day of forecasts has been matched against station observations. Run the ingestion and forecast jobs, then wait a day."
            />
          )}
        </div>
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel>
          <PanelHeader
            title="What it is measured against"
            description="A single error number means nothing on its own."
          />
          <dl className="divide-y divide-line">
            {Object.entries(BASELINE_NOTES).map(([key, note]) => (
              <div key={key} className="px-5 py-4">
                <dt className="text-[13.5px] font-medium capitalize text-ink">{key}</dt>
                <dd className="mt-1 max-w-[58ch] text-[13px] leading-relaxed text-muted">
                  {note}
                </dd>
              </div>
            ))}
          </dl>
        </Panel>

        <Panel className="overflow-hidden">
          <PanelHeader
            title="Model versions"
            description="A candidate is promoted only if it beats every baseline on held-out data."
          />
          {loading && versions.length === 0 ? (
            <SkeletonPanel rows={3} />
          ) : versions.length === 0 ? (
            <EmptyState
              title="No model trained yet"
              description="Until one is trained, forecasts fall back to raw CAMS output and are labelled as such."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-left text-[12.5px]">
                <thead className="bg-sunk text-muted">
                  <tr>
                    <th scope="col" className="px-5 py-2 font-medium">Version</th>
                    <th scope="col" className="px-3 py-2 font-medium">Trained</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Rows</th>
                    <th scope="col" className="px-5 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((version) => (
                    <tr key={version.id} className="border-t border-line">
                      <td className="numeric px-5 py-2.5 text-ink">v{version.version}</td>
                      <td className="numeric px-3 py-2.5 text-muted">
                        {dayShort(version.trained_at)}
                      </td>
                      <td className="numeric px-3 py-2.5 text-right text-muted">
                        {version.training_rows.toLocaleString()}
                      </td>
                      <td className="px-5 py-2.5">
                        {version.is_active ? (
                          <span className="rounded-sm bg-accent-quiet px-2 py-0.5 text-[11.5px] text-accent">
                            serving
                          </span>
                        ) : (
                          <span className="text-[11.5px] text-muted">
                            {version.notes || "superseded"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="Method" />
        <div className="grid gap-3 px-5 py-4 text-[13.5px] leading-relaxed text-muted">
          <p className="max-w-[76ch]">
            CAMS forecasts PM2.5 worldwide on a 40 km grid, 11 km over Europe. At that
            resolution it cannot see a specific junction or a street canyon, so its
            error at any one station is systematic rather than random. Clearway learns
            that local bias from the station&apos;s own history and corrects it. In
            operational meteorology this is called Model Output Statistics.
          </p>
          <p className="max-w-[76ch]">
            The estimator is a gradient-boosted regression on lagged observations, the
            CAMS forecast for the target hour, and weather. The train and test split is
            chronological, never random: neighbouring hours at one station are almost
            duplicates, and a random split would report an accuracy the model could
            never reproduce in production.
          </p>
          <p className="max-w-[76ch]">
            Expect persistence to win at one to three hours. Pollution is strongly
            autocorrelated, so &quot;it will be what it is now&quot; is genuinely hard
            to beat at short range. The value of a forecast is further out, and that is
            where the comparison above is worth reading.
          </p>
        </div>
      </Panel>
    </div>
  );
}
