/**
 * Display formatting helpers. These are presentation-only and never do
 * arithmetic on governed figures — a governed figure is rendered exactly as the
 * backend provided it (Req 3.2, Req 11.4).
 */

import type { CaseState, GovernedNumber } from "@/lib/types";

/** Render a governed figure as-is (string decimals pass through untouched). */
export function formatFigure(value: GovernedNumber): string {
  if (typeof value === "string") return value;
  // Numbers: show up to 4 decimals without trailing-zero noise.
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
}

/** Human-friendly label for a case lifecycle state. */
export function formatState(state: CaseState): string {
  return state
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Format an ISO timestamp for display; falls back to the raw string. */
export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/** Format a KPI value with its unit (percent gets a %, seconds a suffix). */
export function formatKpi(value: number, unit: string): string {
  switch (unit) {
    case "percent":
      return `${Number(value.toFixed(1))}%`;
    case "seconds":
      return `${Number(value.toFixed(1))}s`;
    default:
      return String(value);
  }
}
