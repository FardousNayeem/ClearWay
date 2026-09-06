import { AQI_TOKENS } from "@/lib/aqi";
import type { Aqi } from "@/lib/types";

export function AqiBadge({ aqi, size = "sm" }: { aqi: Aqi; size?: "sm" | "md" }) {
  const tokens = AQI_TOKENS[aqi.category];
  return (
    <span
      className={
        size === "md"
          ? "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-[13.5px] font-medium"
          : "inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-[11.5px] font-medium"
      }
      style={{
        background: `color-mix(in srgb, ${tokens.fill} 26%, transparent)`,
        color: tokens.ink,
      }}
    >
      <span
        aria-hidden
        className="size-2 rounded-full"
        style={{ background: tokens.fill }}
      />
      {aqi.label}
    </span>
  );
}
