import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { CSSProperties } from "react";

const PlotlyComponent = createPlotlyComponent(Plotly);

const AXIS = { gridcolor: "#1c2430", zerolinecolor: "#2a3444", linecolor: "#2a3444", tickfont: { size: 10 } };

export const BASE_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#aab3c2", size: 11, family: "ui-sans-serif, system-ui, Segoe UI" },
  margin: { l: 48, r: 12, t: 28, b: 32 },
  xaxis: AXIS,
  yaxis: AXIS,
  legend: { orientation: "h", y: -0.18, font: { size: 10 } },
  hoverlabel: { bgcolor: "#161c25", bordercolor: "#2a3444", font: { family: "Consolas, monospace", size: 11 } },
  hovermode: "x unified",
};

interface Props {
  data: unknown[];
  layout?: Record<string, any>;
  height?: number;
  style?: CSSProperties;
  onClick?: (e: any) => void;
}

/** Plotly chart with the dark terminal defaults; resizes with its container. */
export function Plot({ data, layout = {}, height = 320, style, onClick }: Props) {
  const merged = {
    ...BASE_LAYOUT, ...layout, height, autosize: true,
    xaxis: { ...AXIS, ...(layout.xaxis ?? {}) }, yaxis: { ...AXIS, ...(layout.yaxis ?? {}) },
    title: layout.title ? { text: layout.title, font: { size: 12, color: "#d4d9e1" }, x: 0.01 } : undefined,
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
