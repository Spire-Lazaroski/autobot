import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { CSSProperties } from "react";
import { AXIS, spireTemplate } from "../lib/chart";
import { chart } from "../theme";

const PlotlyComponent = createPlotlyComponent(Plotly);

export const BASE_LAYOUT = {
  template: spireTemplate,
  margin: { l: 48, r: 12, t: 28, b: 32 },
  legend: { orientation: "h", y: -0.18, font: { size: 10.5 } },
  hovermode: "x unified",
};

interface Props {
  data: unknown[];
  layout?: Record<string, any>;
  height?: number;
  style?: CSSProperties;
  onClick?: (e: any) => void;
  /** Grey area style (approved 2026-10-09, "style B"): "main" shades under the first line down to its own low
   *  and tags its last value; "zero" shades the first line to zero (drawdowns, returns from 0). */
  area?: "main" | "zero";
}

const AREA_FILL = `${chart.ink}1f`;   // about 12% of the main grey

/** Adds the grey area under the first line trace, plus a dark tag with its last value. */
function withArea(data: any[], area: "main" | "zero" | undefined, layout: Record<string, any>) {
  if (!area) return { data, annotations: layout.annotations };
  const k = data.findIndex((t) => (t.type ?? "scatter") === "scatter" && String(t.mode ?? "lines").includes("lines") && !t.fill);
  if (k < 0) return { data, annotations: layout.annotations };
  const main = data[k];
  const ys: number[] = (main.y ?? []).filter((v: unknown) => typeof v === "number" && isFinite(v as number));
  if (!ys.length) return { data, annotations: layout.annotations };
  const out = [...data];
  if (area === "zero") {
    out[k] = { ...main, fill: "tozeroy", fillcolor: AREA_FILL };
  } else {
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const floor = lo - (hi - lo) * 0.05;
    const base = { type: "scatter", mode: "lines", x: main.x, y: (main.x ?? []).map(() => floor), line: { width: 0 },
      hoverinfo: "skip", showlegend: false, yaxis: main.yaxis, xaxis: main.xaxis };
    out.splice(k, 1, base, { ...main, fill: "tonexty", fillcolor: AREA_FILL });
  }
  let lastI = (main.y ?? []).length - 1;
  while (lastI >= 0 && !(typeof main.y[lastI] === "number" && isFinite(main.y[lastI]))) lastI -= 1;
  const tag = lastI >= 0 ? [{
    x: main.x?.[lastI], y: main.y[lastI], xref: main.xaxis ?? "x", yref: main.yaxis ?? "y", showarrow: false, xanchor: "left", xshift: 4,
    text: Math.abs(main.y[lastI]) >= 1000 ? Number(main.y[lastI]).toLocaleString("en-US", { maximumFractionDigits: 0 }) : Number(main.y[lastI]).toFixed(2),
    bgcolor: chart.ink, bordercolor: chart.ink, borderpad: 3, font: { color: "#ffffff", size: 10.5 },
  }] : [];
  return { data: out, annotations: [...(layout.annotations ?? []), ...tag] };
}

/** Plotly chart with the Spire grey template (white, light grey grid); resizes with its container. */
export function Plot({ data, layout = {}, height = 320, style, onClick, area }: Props) {
  const shaded = withArea(data as any[], area, layout);
  const merged = {
    ...BASE_LAYOUT, ...layout, height, autosize: true, annotations: shaded.annotations,
    margin: area === "main" ? { ...(BASE_LAYOUT.margin), ...(layout.margin ?? {}), r: Math.max(64, layout.margin?.r ?? 12) } : layout.margin ?? BASE_LAYOUT.margin,
    xaxis: { ...AXIS, ...(layout.xaxis ?? {}) }, yaxis: { ...AXIS, ...(layout.yaxis ?? {}) },
    title: layout.title ? { text: layout.title, x: 0.01 } : undefined,
  };
  return (
    <PlotlyComponent
      data={shaded.data}
      layout={merged}
      config={{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] }}
      useResizeHandler
      style={{ width: "100%", height, ...style }}
      onClick={onClick}
    />
  );
}
