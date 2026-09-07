import type { Metadata } from "next";

import { Scorecard } from "@/components/model/Scorecard";

export const metadata: Metadata = {
  title: "How accurate is it",
  description:
    "ClearWay scores itself every day against persistence, climatology and the raw CAMS physics model, on forecasts made before the outcome was known.",
};

export default function ModelPage() {
  return (
    <div className="mx-auto max-w-[1180px] px-4 py-8 sm:px-6 lg:py-10">
      <header className="max-w-[68ch]">
        <h1 className="text-[30px] font-semibold tracking-tight text-ink sm:text-[36px]">
          How accurate is it
        </h1>
        <p className="mt-3 text-[15px] leading-relaxed text-muted">
          Every forecast is written to the database before the outcome exists. Once
          the monitoring stations report, the prediction is scored against what
          actually happened. Nothing here is recomputed with hindsight, which is why
          the numbers can be trusted and why some of them are unflattering.
        </p>
      </header>

      <Scorecard />
    </div>
  );
}
