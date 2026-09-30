/**
 * Chart colours and the Plotly "spire" template, all derived from theme.ts.
 * White paper and plot background, --line gridlines, --ink-soft axis labels.
 */
import { benchmarkGrey, brand, extra, series } from "../theme";

export const C = {
  paper: brand.paper,
  sunk: brand.sunk,
  line: brand.line,
  lineStrong: extra["line-strong"],
  ink: brand.ink,
  inkSoft: brand["ink-soft"],
  inkFaint: extra["ink-faint"],
  navy: brand.navy,
  gold: brand.gold,
  amber: brand.amber,
  profit: extra.profit,
  loss: extra.loss,
  grey: benchmarkGrey,
  series,
};

/** Diverging scale for returns / Sharpe: loss red - white - profit green. */
export const DIVERGING: [number, string][] = [[0, extra.loss], [0.5, "#ffffff"], [1, extra.profit]];
/** Sequential scale for counts: white - navy. */
export const SEQUENTIAL: [number, string][] = [[0, "#ffffff"], [1, brand.navy]];

const AXIS = {
  gridcolor: brand.line,
  zerolinecolor: extra["line-strong"],
  linecolor: brand.line,
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
    hoverlabel: { bgcolor: brand.paper, bordercolor: brand.line, font: { family: "Inter, sans-serif", size: 11.5, color: brand.ink } },
  },
};

export { AXIS };
