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

/** Charts: one grey professional template for every chart (web, reports, SOP), chosen 2026-10-09.
 *  The brand colours stay on the page chrome (header, buttons, badges); inside charts only greys are
 *  used, and profit / loss green and red only for gains, losses and actual fills. */
export const chart = {
  ink: "#2c2c2a", // the main line (the bot, the strategy, the price)
  mid: "#6b6a65", // the second line (fixed params, a comparison)
  soft: "#9c9a92", // third lines, indicators
  faint: "#c4c2ba", // light indicator lines, bands
  grid: "#e6e4de", // gridlines
  zero: "#cfccc4", // zero line, axes
  band: "#efeee9", // shaded areas
  candleUp: "#ffffff", // rising candle body (hollow), border = ink
  candleDown: "#5f5e5a", // falling candle body (filled)
} as const;

/** Chart series order: dark to light greys, alternating so neighbours stay distinguishable
 *  (charts with many lines add dashes). Buy & hold is always the dotted benchmark grey. */
export const series = ["#2c2c2a", "#8a8880", "#4f4e4a", "#adaba3", "#6b6a65", "#c4c2ba", "#3d3c39", "#9c9a92", "#5f5e5a", "#7a7973"] as const;
export const benchmarkGrey = "#9c9a92";

/** Regimes / states used in bands and pies (greys; long / short keep profit / loss). */
export const states = {
  trend: "#8a8880",
  range: "#c4c2ba",
  high_vol: "#4f4e4a",
  flat: "#e2e0d9",
  warmup: "#efeee9",
  long: "#1e7d4f",
  short: "#c23b3b",
} as const;

export const fonts = {
  heading: "'Manrope', sans-serif",
  ui: "'Inter', sans-serif",
} as const;
