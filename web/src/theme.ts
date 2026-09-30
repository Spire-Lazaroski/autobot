/**
 * Spire Analytics brand tokens: the ONE place colours are defined.
 *
 * `brand` is copied verbatim from branding/BRAND.md (source: the pricing engine's
 * partials/spire_header.html). A near-miss palette reads as a rendering bug, so these
 * must never be "tweaked"; tests/test_brand.py checks them against BRAND.md.
 *
 * `web/scripts/gen-brand.mjs` turns this file into:
 *   web/src/brand.css            CSS variables (--navy, --gold, ...) for the app
 *   autobot/assets/brand.css     the same variables + base styles for reports and the SOP
 *   autobot/assets/brand.json    the values for Python (matplotlib charts)
 * Run `npm run brand` after editing.
 */

export const brand = {
  navy: "#1a1a2e", // mark, primary, text on gold
  gold: "#e8b23d", // accent: active tab, highlights. NEVER text on white (contrast too low)
  amber: "#d98c2b", // gradient end, hover
  paper: "#ffffff", // page + chart background
  sunk: "#fbfaf6", // hover / subtle panels
  line: "#e7e2d6", // borders, gridlines
  ink: "#201d2b", // main text
  "ink-soft": "#52514e", // secondary text
} as const;

/** Not brand colours: trading semantics and UI states. Kept clearly apart from gold/amber
 *  and checked for WCAG AA (4.5:1) as text on white by tests/test_brand.py. */
export const extra = {
  profit: "#1e7d4f",
  loss: "#c23b3b",
  "warn-ink": "#8a5a12", // warning TEXT (amber itself fails AA on white)
  "warn-bg": "#fdf3e1", // warning background
  "line-strong": "#d6cfbf", // input borders (a step darker than --line)
  "ink-faint": "#6b6964", // tertiary text, still AA on white
  mark2: "#3a3a52", // second stop of the mark gradient (from the header source)
} as const;

/** Chart series order: navy, gold, then distinguishable extras. Buy & hold is always dotted grey. */
export const series = ["#1a1a2e", "#e8b23d", "#1b9aaa", "#3f6fd8", "#8e5bd0", "#d6457a", "#5b8c3a", "#b86b2b", "#2f4858", "#7a7a7a"] as const;
export const benchmarkGrey = "#8d8a83";

/** Regimes / states used in bands and pies. */
export const states = {
  trend: "#1b9aaa",
  range: "#3f6fd8",
  high_vol: "#c23b3b",
  flat: "#cfc9bb",
  warmup: "#ece8de",
  long: "#1e7d4f",
  short: "#c23b3b",
} as const;

export const fonts = {
  heading: "'Manrope', sans-serif",
  ui: "'Inter', sans-serif",
} as const;
