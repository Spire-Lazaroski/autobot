import { benchmarkGrey, series, states } from "../theme";

export const fmt = (v: unknown, d = 2): string => {
  if (v === null || v === undefined || (typeof v === "number" && !isFinite(v))) return "–";
  if (typeof v === "number") return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  return String(v);
};

export const signed = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${fmt(v, d)}`;

/** Tailwind text color for a P&L-like number. */
export const pnl = (v: number | null | undefined) =>
  v === null || v === undefined || v === 0 ? "text-fg" : v > 0 ? "text-up" : "text-down";

// Series order from the brand (navy, gold, then distinguishable extras); fixed colours for
// the benchmark (always grey, drawn dotted) and for regimes / states.
const FIXED: Record<string, string> = { buy_and_hold: benchmarkGrey, ...states };
const assigned = new Map<string, string>();
export function colorFor(name: string): string {
  if (FIXED[name]) return FIXED[name];
  if (!assigned.has(name)) assigned.set(name, series[assigned.size % series.length]);
  return assigned.get(name)!;
}

export const toDates = (t: number[]) => t.map((s) => new Date(s * 1000));

const nyFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
export const nyTime = (s: number) => nyFmt.format(new Date(s * 1000));

/** Bootstrap Icons class per strategy family. */
export const FAMILY_ICON: Record<string, string> = {
  benchmark: "bi-flag", trend: "bi-graph-up-arrow", mean_reversion: "bi-arrow-left-right",
  breakout: "bi-rocket-takeoff", intraday: "bi-clock", meta: "bi-diagram-3",
};
