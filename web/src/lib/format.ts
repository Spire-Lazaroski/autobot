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

const PALETTE = ["#4aa3ff", "#f5b942", "#c084fc", "#2dd4bf", "#fb7185", "#a3e635", "#f97316",
  "#60a5fa", "#e879f9", "#facc15", "#34d399", "#f472b6", "#38bdf8", "#fbbf24", "#818cf8", "#4ade80"];
const assigned = new Map<string, string>();
export function colorFor(name: string): string {
  if (name === "buy_and_hold") return "#9ca3af";
  if (!assigned.has(name)) assigned.set(name, PALETTE[assigned.size % PALETTE.length]);
  return assigned.get(name)!;
}

export const toDates = (t: number[]) => t.map((s) => new Date(s * 1000));

const nyFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
export const nyTime = (s: number) => nyFmt.format(new Date(s * 1000));

export const FAMILY_ICON: Record<string, string> = {
  benchmark: "◎", trend: "↗", mean_reversion: "⇄", breakout: "⇪", intraday: "◷", meta: "⎇",
};
