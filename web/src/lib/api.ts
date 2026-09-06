import { ApiError, type ApiErrorBody } from "./errors";

const SERVER_BASE = process.env.API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";
const BROWSER_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1";

export const apiBase = () =>
  typeof window === "undefined" ? SERVER_BASE : BROWSER_BASE;

type Options = { revalidate?: number; signal?: AbortSignal };

export async function get<T>(path: string, options: Options = {}): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`, {
    signal: options.signal,
    headers: { Accept: "application/json" },
    ...(options.revalidate === undefined
      ? { cache: "no-store" as const }
      : { next: { revalidate: options.revalidate } }),
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw ApiError.from(response.status, payload as ApiErrorBody | null);
  }
  return payload as T;
}

/** A query string builder that drops undefined rather than sending "undefined". */
export function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.toString();
}
