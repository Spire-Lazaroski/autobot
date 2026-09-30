import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { CSSProperties } from "react";
import { AXIS, spireTemplate } from "../lib/chart";

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
}

/** Plotly chart with the Spire template (white, --line grid, --ink-soft labels); resizes with its container. */
export function Plot({ data, layout = {}, height = 320, style, onClick }: Props) {
  const merged = {
    ...BASE_LAYOUT, ...layout, height, autosize: true,
    xaxis: { ...AXIS, ...(layout.xaxis ?? {}) }, yaxis: { ...AXIS, ...(layout.yaxis ?? {}) },
    title: layout.title ? { text: layout.title, x: 0.01 } : undefined,
  };
  return (
    <PlotlyComponent
      data={data}
      layout={merged}
      config={{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] }}
      useResizeHandler
      style={{ width: "100%", height, ...style }}
      onClick={onClick}
    />
  );
}
