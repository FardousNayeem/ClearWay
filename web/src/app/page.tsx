import { AirDashboard } from "@/components/air/AirDashboard";
import { get } from "@/lib/api";
import type { City } from "@/lib/types";

export const revalidate = 3600;

async function getCities(): Promise<City[]> {
  try {
    return await get<City[]>("/cities", { revalidate: 3600 });
  } catch {
    // The city list is a convenience; search still works without it.
    return [];
  }
}

export default async function HomePage() {
  const cities = await getCities();

  return (
    <div className="mx-auto max-w-[1180px] px-4 py-8 sm:px-6 lg:py-10">
      <header className="max-w-[60ch]">
        <h1 className="text-[30px] font-semibold tracking-tight text-ink sm:text-[36px]">
          Air quality you can act on
        </h1>
        <p className="mt-2 text-[15px] leading-relaxed text-muted">
          A nowcast built from the monitoring stations nearest you, and a 24-hour
          forecast that corrects the global physics model using what those stations
          actually recorded.
        </p>
      </header>

      <AirDashboard cities={cities} />
    </div>
  );
}
