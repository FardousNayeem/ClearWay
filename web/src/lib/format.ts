export const micrograms = (value: number, digits = 1) =>
  `${value.toFixed(digits)} µg/m³`;

/**
 * Every timestamp the API returns is UTC. Every timestamp a person reads has
 * to be in the time of the place they picked: "clear at 04:00" is only
 * actionable if 04:00 means 04:00 there. So `timeZone` is threaded down from
 * the selected location rather than left to `Intl`'s default, which is the
 * zone of whatever machine happens to be rendering.
 *
 * An undefined `timeZone` still falls back to that default. That is the
 * honest behaviour for a place whose zone we genuinely do not know, and
 * `zoneLabel` below is what stops the result from being ambiguous.
 */
export const hour = (iso: string, timeZone?: string) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  }).format(new Date(iso));

export const dayAndHour = (iso: string, timeZone?: string) =>
  new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  }).format(new Date(iso));

export const dayShort = (iso: string, timeZone?: string) =>
  new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    timeZone,
  }).format(new Date(iso));

/**
 * A short name for the zone the times on screen are in, e.g. "GMT+6".
 *
 * Shown next to them, because a bare "04:00" against a city on the other side
 * of the world is the exact ambiguity this whole parameter exists to remove.
 * Resolved against a real instant so that daylight saving is accounted for.
 */
export function zoneLabel(timeZone?: string, at: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      timeZoneName: "shortOffset",
    }).formatToParts(at);
    return parts.find((part) => part.type === "timeZoneName")?.value ?? "";
  } catch {
    // An unknown or malformed zone must not take the page down with it.
    return "";
  }
}

export function relativeMinutes(minutes: number): string {
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export const cn = (...values: (string | false | null | undefined)[]) =>
  values.filter(Boolean).join(" ");
