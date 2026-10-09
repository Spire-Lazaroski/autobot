/**
 * Chart colours and the Plotly "spire" template, all derived from theme.ts: the grey professional
 * template (theme.chart), white background, light grey gridlines. `navy` / `gold` / `amber` are kept
 * as names so existing charts keep their roles (main line / second line / third), now in greys.
 */
import { benchmarkGrey, brand, chart, extra, series } from "../theme";

export const C = {
  paper: brand.paper,
  sunk: chart.band,
  line: chart.grid,
  lineStrong: chart.zero,
  ink: brand.ink,
  inkSoft: brand["ink-soft"],
  inkFaint: chart.soft,
  navy: chart.ink,
  gold: chart.mid,
  amber: chart.soft,
  profit: extra.profit,
  loss: extra.loss,
  grey: benchmarkGrey,
  faint: chart.faint,
  candleUp: chart.candleUp,
  candleDown: chart.candleDown,
  series,
};

/** Diverging scale for returns / Sharpe: loss red - white - profit green. */
export const DIVERGING: [number, string][] = [[0, extra.loss], [0.5, "#ffffff"], [1, extra.profit]];
/** Sequential scale for counts: white - dark grey. */
export const SEQUENTIAL: [number, string][] = [[0, "#ffffff"], [1, chart.ink]];

const AXIS = {
  gridcolor: chart.grid,
  zerolinecolor: chart.zero,
  linecolor: chart.zero,
  tickfont: { size: 10.5, color: brand["ink-soft"] },
  title: { font: { color: brand["ink-soft"], size: 11.5 } },
};

/** Plotly has no template registry in JS, so this object is the "spire" template. */
export const spireTemplate = {
  layout: {
    paper_bgcolor: brand.paper,
    plot_bgcolor: brand.paper,
    colorway: [...series],
    font: { family: "Inter, sans-serif", color: brand.ink, size: 11.5 },
    title: { font: { family: "Manrope, sans-serif", size: 13, color: brand.ink } },
    xaxis: AXIS,
    yaxis: AXIS,
    legend: { font: { size: 10.5, color: brand["ink-soft"] } },
    hoverlabel: { bgcolor: brand.paper, bordercolor: chart.zero, font: { family: "Inter, sans-serif", size: 11.5, color: brand.ink } },
  },
};

export { AXIS };
