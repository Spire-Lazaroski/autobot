import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart,
  createSeriesMarkers, type IChartApi, type IPriceLine, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import type { Candles, Fill, Line, Pane } from "../lib/api";
import { C } from "../lib/chart";
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

/** A horizontal price line on the price pane (display only). */
export interface PriceMark {
  price: number;
  color: string;
  style: "dashed" | "dotted";
  title: string;
}

interface Props {
  candles: Candles;
  priceLines?: PriceMark[];
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
// shared defaults: a fresh [] per render would re-run the chart effect (and its setReady) forever
const NO_OVERLAYS: Line[] = [], NO_PANES: Pane[] = [], NO_FILLS: Fill[] = [];

export function CandleChart({ candles, priceLines, overlays = NO_OVERLAYS, panes = NO_PANES, fills = NO_FILLS, position, equity, band, height = 620 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const [ready, setReady] = useState(0);

  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: C.paper }, textColor: C.inkSoft, fontSize: 11, fontFamily: "Inter, sans-serif",
        panes: { separatorColor: C.line, separatorHoverColor: C.lineStrong } },
      grid: { vertLines: { color: C.line }, horzLines: { color: C.line } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: C.line },
      timeScale: {
        borderColor: C.line, timeVisible: true, secondsVisible: false,
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
      upColor: C.profit, downColor: C.loss, borderVisible: false, wickUpColor: C.profit, wickDownColor: C.loss,
    });
    price.setData(t.map((time, i) => ({ time: time as UTCTimestamp, open: candles.o[i], high: candles.h[i], low: candles.l[i], close: candles.c[i] })));
    priceRef.current = price;
    linesRef.current = [];
    setReady((n) => n + 1);
    for (const o of overlays) {
      const s = chart.addSeries(LineSeries, {
        color: o.color ?? C.navy, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        lineStyle: o.dash ? LineStyle.Dotted : LineStyle.Solid, title: o.name,
      });
      s.setData(toLine(t, o.values));
    }
    const markers: SeriesMarker<Time>[] = fills.map((f) => ({
      time: f.time as UTCTimestamp,
      position: f.side === "buy" ? "belowBar" : "aboveBar",
      shape: f.side === "buy" ? "arrowUp" : "arrowDown",
      color: f.kind === "exit" ? C.grey : f.side === "buy" ? C.profit : C.loss,
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
          ? chart.addSeries(HistogramSeries, { color: C.lineStrong, priceLineVisible: false, lastValueVisible: false }, pane)
          : chart.addSeries(LineSeries, { color: l.color ?? C.navy, lineWidth: 1, priceLineVisible: false, lastValueVisible: k === 0, title: k === 0 ? p.name : "" }, pane);
        if (isHist) {
          s.setData(t.map((time, i) => (l.values[i] === null ? { time: time as UTCTimestamp }
            : { time: time as UTCTimestamp, value: l.values[i] as number, color: (l.values[i] as number) >= 0 ? `${C.profit}66` : `${C.loss}66` })));
        } else {
          s.setData(toLine(t, l.values));
        }
        if (k === (isHist ? 1 : 0) || (p.series.length === 1 && k === 0)) {
          for (const lv of p.levels) s.createPriceLine({ price: lv, color: C.inkFaint, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
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
      s.setData(t.map((time, i) => ({ time: time as UTCTimestamp, value: position[i], color: position[i] > 0 ? `${C.profit}99` : position[i] < 0 ? `${C.loss}99` : "#00000000" })));
    }

    // --- equity (percent)
    if (equity) {
      pane += 1;
      stretch.push(1.8);
      const pct = (v: number[]) => v.map((x) => (x === null ? null : (x - 1) * 100));
      if (equity.benchmark) {
        chart.addSeries(LineSeries, { color: C.grey, lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, title: "B&H %" }, pane)
          .setData(toLine(t, pct(equity.benchmark)));
      }
      chart.addSeries(LineSeries, { color: C.navy, lineWidth: 2, priceLineVisible: false, title: "strategy %" }, pane)
        .setData(toLine(t, pct(equity.strategy)));
    }

    chart.panes().forEach((p, i) => p.setStretchFactor(stretch[i] ?? 1));
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
      priceRef.current = null;
    };
  }, [candles, overlays, panes, fills, position, equity, band]);

  // price lines change every few seconds (live status): update them without rebuilding the chart
  useEffect(() => {
    const s = priceRef.current;
    if (!s) return;
    for (const l of linesRef.current) s.removePriceLine(l);
    linesRef.current = (priceLines ?? []).map((m) => s.createPriceLine({
      price: m.price, color: m.color, lineWidth: 1, lineStyle: m.style === "dotted" ? LineStyle.Dotted : LineStyle.Dashed,
      axisLabelVisible: true, title: m.title,
    }));
  }, [priceLines, ready]);

  return <div ref={box} style={{ height, width: "100%" }} />;
}
