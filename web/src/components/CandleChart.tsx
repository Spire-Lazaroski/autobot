import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart,
  createSeriesMarkers, type IChartApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Candles, Fill, Line, Pane } from "../lib/api";
import { colorFor } from "../lib/format";

const NY = "America/New_York";
const fmtCache = new Map<string, Intl.DateTimeFormat>();
function nyFormat(sec: number, opts: Intl.DateTimeFormatOptions) {
  const key = JSON.stringify(opts);
  if (!fmtCache.has(key)) fmtCache.set(key, new Intl.DateTimeFormat("en-US", { timeZone: NY, hour12: false, ...opts }));
  return fmtCache.get(key)!.format(new Date(sec * 1000));
}

export interface Band {
  names: (string | null)[]; // one label per bar (e.g. active strategy or regime)
  title: string;
}

interface Props {
  candles: Candles;
  overlays?: Line[];
  panes?: Pane[];
  fills?: Fill[];
  position?: number[];
  equity?: { strategy: number[]; benchmark?: number[] };
  band?: Band;
  height?: number;
}

const toLine = (t: number[], vals: (number | null)[]) =>
  t.map((time, i) => (vals[i] === null || vals[i] === undefined ? { time: time as UTCTimestamp } : { time: time as UTCTimestamp, value: vals[i] as number }));

/**
 * TradingView lightweight-chart: candles + indicator overlays + entry/exit markers on the
 * top pane, then one pane per indicator, the position, the active-strategy band and equity.
 * Times display in New York time.
 */
export function CandleChart({ candles, overlays = [], panes = [], fills = [], position, equity, band, height = 620 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#0a0d11" }, textColor: "#aab3c2", fontSize: 11,
        panes: { separatorColor: "#1f2733", separatorHoverColor: "#2a3444" } },
      grid: { vertLines: { color: "#141a22" }, horzLines: { color: "#141a22" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#1f2733" },
      timeScale: {
        borderColor: "#1f2733", timeVisible: true, secondsVisible: false,
        tickMarkFormatter: (time: Time, type: number) => {
          const s = time as number;
          if (type === 0) return nyFormat(s, { year: "numeric" });
          if (type === 1) return nyFormat(s, { month: "short" });
          if (type === 2) return nyFormat(s, { month: "short", day: "numeric" });
          return nyFormat(s, { hour: "2-digit", minute: "2-digit" });
        },
      },
      localization: { timeFormatter: (time: Time) => nyFormat(time as number, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) },
    });
    chartRef.current = chart;
    const t = candles.t;

    // --- price pane
    const price = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444",
    });
    price.setData(t.map((time, i) => ({ time: time as UTCTimestamp, open: candles.o[i], high: candles.h[i], low: candles.l[i], close: candles.c[i] })));
    for (const o of overlays) {
      const s = chart.addSeries(LineSeries, {
        color: o.color ?? "#f5b942", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        lineStyle: o.dash ? LineStyle.Dotted : LineStyle.Solid, title: o.name,
      });
      s.setData(toLine(t, o.values));
    }
    const markers: SeriesMarker<Time>[] = fills.map((f) => ({
      time: f.time as UTCTimestamp,
      position: f.side === "buy" ? "belowBar" : "aboveBar",
      shape: f.side === "buy" ? "arrowUp" : "arrowDown",
      color: f.kind === "exit" ? "#9ca3af" : f.side === "buy" ? "#22c55e" : "#ef4444",
      text: f.label ?? (f.kind === "exit" ? "x" : f.to > 0 ? "L" : "S"),
    }));
    if (markers.length) createSeriesMarkers(price, markers);

    let pane = 0;
    const stretch: number[] = [5];

    // --- indicator panes
    for (const p of panes) {
      pane += 1;
      stretch.push(1.6);
      p.series.forEach((l, k) => {
        const isHist = l.name === "hist";
        const s = isHist
          ? chart.addSeries(HistogramSeries, { color: "#4b5566", priceLineVisible: false, lastValueVisible: false }, pane)
          : chart.addSeries(LineSeries, { color: l.color ?? "#4aa3ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: k === 0, title: k === 0 ? p.name : "" }, pane);
        if (isHist) {
          s.setData(t.map((time, i) => (l.values[i] === null ? { time: time as UTCTimestamp }
            : { time: time as UTCTimestamp, value: l.values[i] as number, color: (l.values[i] as number) >= 0 ? "#22c55e66" : "#ef444466" })));
        } else {
          s.setData(toLine(t, l.values));
        }
        if (k === (isHist ? 1 : 0) || (p.series.length === 1 && k === 0)) {
          for (const lv of p.levels) s.createPriceLine({ price: lv, color: "#4b5566", lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
        }
      });
    }

    // --- active strategy / regime band
    if (band) {
      pane += 1;
      stretch.push(0.45);
      const s = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false, title: band.title, priceFormat: { type: "custom", formatter: () => "" } }, pane);
      s.setData(t.map((time, i) => (band.names[i] ? { time: time as UTCTimestamp, value: 1, color: colorFor(band.names[i]!) } : { time: time as UTCTimestamp })));
    }

    // --- position
    if (position) {
      pane += 1;
      stretch.push(0.6);
      const s = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false, title: "position" }, pane);
      s.setData(t.map((time, i) => ({ time: time as UTCTimestamp, value: position[i], color: position[i] > 0 ? "#22c55e99" : position[i] < 0 ? "#ef444499" : "#00000000" })));
    }

    // --- equity (percent)
    if (equity) {
      pane += 1;
      stretch.push(1.8);
      const pct = (v: number[]) => v.map((x) => (x === null ? null : (x - 1) * 100));
      if (equity.benchmark) {
        chart.addSeries(LineSeries, { color: "#9ca3af", lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, title: "B&H %" }, pane)
          .setData(toLine(t, pct(equity.benchmark)));
      }
      chart.addSeries(LineSeries, { color: "#4aa3ff", lineWidth: 2, priceLineVisible: false, title: "strategy %" }, pane)
        .setData(toLine(t, pct(equity.strategy)));
    }

    chart.panes().forEach((p, i) => p.setStretchFactor(stretch[i] ?? 1));
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [candles, overlays, panes, fills, position, equity, band]);

  return <div ref={box} style={{ height, width: "100%" }} />;
}
