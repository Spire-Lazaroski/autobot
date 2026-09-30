import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { DetailView } from "../components/DetailView";
import { GenerateReport } from "../components/GenerateReport";
import { ParamInput } from "../components/ParamInput";
import { Plot } from "../components/Plot";
import { SettingsBar } from "../components/SettingsBar";
import { SortTable } from "../components/SortTable";
import { pickedOf } from "../components/StrategyCards";
import { Empty, ErrorBox, Kpi, LowTrades, Panel, Spinner } from "../components/ui";
import { api, useStrategies, type Frame, type Params, type Row, type RunReq } from "../lib/api";
import { C } from "../lib/chart";
import { colorFor, fmt, pnl, signed, toDates } from "../lib/format";
import { useSettings } from "../lib/settings";
import { setPageState, usePageState } from "../lib/usePageState";

type Meta = "regime_switch" | "perf_rotation" | "ensemble_vote";
const METHODS: { id: Meta; label: string; blurb: string }[] = [
  { id: "regime_switch", label: "a) Regime switch", blurb: "Classify each bar as trending / ranging / high-vol, and let the strategy mapped to that regime hold the position." },
  { id: "perf_rotation", label: "b) Performance rotation", blurb: "Every K bars, hand the position to whichever selected strategy has the best trailing Sharpe. Optionally stay flat when all are negative." },
  { id: "ensemble_vote", label: "c) Ensemble vote", blurb: "Hold the majority (or average) position of the selected strategies." },
];

interface Seg { name: string; start: number; end: number }
interface ShiftResult {
  strategy: Meta;
  params: Params;
  underlying: string[];
  summary: Row[];
  per_symbol: Row[];
  equity: Frame;
  timelines: Record<string, { active: Seg[]; regimes: Seg[] | null }>;
  switches: Record<string, { switches: number; switches_that_traded: number }>;
  time_in: Record<string, number>;
  low_trades_threshold: number;
}
type ShiftReq = RunReq & { strategy: Meta; params: Params };

// regime_switch has settings for three trend measures; only show the chosen one's
const MEASURE_KEYS: Record<string, string[]> = { adx: ["adx_period", "adx_trend"], er: ["er_period", "er_trend"], vr: ["vr_period", "vr_trend"] };
function visibleKeys(meta: Meta, params: Params, defaults: Params) {
  const keys = Object.keys(defaults);
  if (meta !== "regime_switch") return keys;
  const hide = Object.entries(MEASURE_KEYS).filter(([m]) => m !== params.trend_measure).flatMap(([, k]) => k);
  return keys.filter((k) => !hide.includes(k));
}

export function Shifting() {
  const { run, picked, setPicked } = useSettings();
  const strategies = useStrategies();
  const [meta, setMeta] = usePageState<Meta>("shift.meta", "regime_switch");
  const [paramsBy, setParamsBy] = usePageState<Record<string, Params>>("shift.params", {});
  const [req, setReq] = usePageState<ShiftReq | null>("shift.req", null);
  const [open, setOpen] = useState<string | null>(null);

  const spec = strategies.data?.find((s) => s.name === meta);
  const params: Params = paramsBy[meta] ?? (spec ? pickedOf(spec, picked).params : {});
  const setParam = (k: string, v: unknown) => setParamsBy({ ...paramsBy, [meta]: { ...params, [k]: v } });

  const q = useQuery({ queryKey: ["shift", req], queryFn: () => api<ShiftResult>("/shift", req), enabled: !!req, staleTime: Infinity, gcTime: 30 * 60_000 });
  const go = () => setReq({ ...run, strategy: meta, params });

  const walkForward = () => {
    if (!spec) return;
    setPicked({ ...picked, [meta]: { ...pickedOf(spec, picked), params } });
    setPageState("wf.cfg", { strategy: meta, train_days: 20, test_days: 5, rank_by: "sharpe" });
    setPageState("wf.job", null);
    window.location.hash = "#/walkforward";
  };

  return (
    <div className="flex flex-col gap-3">
      <SettingsBar action={<>
        <button className="btn" onClick={walkForward} disabled={!spec} title="Re-optimize this meta-strategy's grid in rolling windows">⏩ Walk-forward this</button>
        <button className="btn btn-primary" onClick={go} disabled={!spec || q.isFetching}>{q.isFetching ? <Spinner /> : "⎇"} Run shifting</button>
      </>} />

      <Panel title="Method">
        <div className="flex flex-wrap gap-1.5 mb-2">
          {METHODS.map((m) => (
            <button key={m.id} onClick={() => setMeta(m.id)}
              className={`btn ${meta === m.id ? "btn-primary" : ""}`}>{m.label}</button>
          ))}
        </div>
        <p className="text-[12px] text-muted mb-3">{METHODS.find((m) => m.id === meta)?.blurb}</p>
        {spec && (
          <div className="grid gap-2 grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
            {visibleKeys(meta, params, spec.defaults).map((k) => (
              <ParamInput key={`${meta}.${k}`} name={k} value={params[k] ?? spec.defaults[k]} onChange={(v) => setParam(k, v)} />
            ))}
          </div>
        )}
        {spec && JSON.stringify(params) !== JSON.stringify(spec.defaults) && (
          <button className="text-[11px] text-muted underline mt-2" onClick={() => setParamsBy({ ...paramsBy, [meta]: spec.defaults })}>reset to defaults</button>
        )}
        <p className="text-[11px] text-dim mt-2">
          Decisions at bar t use only bars up to t and are traded on bar t+1. Each underlying strategy runs continuously, so it takes over in its
          current state. Switches pay costs only when the position actually changes.
          {meta === "regime_switch" && " confirm_bars: a new regime must persist that many bars before the switch (less flip-flopping)."}
        </p>
      </Panel>

      <ErrorBox error={q.error} />
      {!req && <Empty>Pick a method and press <b>Run shifting</b>.</Empty>}
      {q.data && req && (
        <Panel title="Plain-English report" right={<GenerateReport run={req} strategy={q.data.strategy} params={q.data.params} />}>
          <p className="text-[12px] text-muted">Every regime change, strategy switch and trade of this run, explained in plain words.</p>
        </Panel>
      )}
      {q.data && req && <Results res={q.data} onOpen={setOpen} />}
      {open && req && q.data && (
        <DetailView run={req} strategy={q.data.strategy} params={q.data.params} symbols={req.data.symbols}
          initialSymbol={open} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}

function Results({ res, onOpen }: { res: ShiftResult; onOpen: (symbol: string) => void }) {
  const syms = Object.keys(res.timelines);
  const [sym, setSym] = useState(syms[0]);
  const x = useMemo(() => toDates(res.equity.t), [res]);
  const low = res.low_trades_threshold;
  const metaRow = res.summary.find((r) => r.strategy === res.strategy)!;
  const bh = res.summary.find((r) => r.strategy === "buy_and_hold");
  const bestUnder = res.summary.filter((r) => r.role === "underlying").sort((a, b) => b.sharpe - a.sharpe)[0];
  const sw = res.switches[sym] ?? { switches: 0, switches_that_traded: 0 };
  const totalSw = Object.values(res.switches).reduce((a, s) => a + s.switches, 0);
  const tl = res.timelines[sym];

  // timeline band rows under the equity chart (horizontal bars on a date axis)
  const bandRows = [{ key: "active", segs: tl.active }, ...(tl.regimes ? [{ key: "regime", segs: tl.regimes }] : [])];
  const bandTraces = bandRows.flatMap((row) => {
    const byName: Record<string, Seg[]> = {};
    for (const s of row.segs) (byName[s.name] ??= []).push(s);
    return Object.entries(byName).map(([name, segs]) => ({
      type: "bar", orientation: "h", yaxis: "y2", name, showlegend: false,
      y: segs.map(() => row.key), base: segs.map((s) => new Date(s.start * 1000)),
      x: segs.map((s) => Math.max(1, s.end - s.start) * 1000), marker: { color: colorFor(name), line: { width: 0 } },
      hovertemplate: `${row.key}: ${name}<extra></extra>`, width: 0.8,
    }));
  });

  const lineTraces = Object.keys(res.equity.series).map((n) => ({
    x, y: res.equity.series[n].map((v) => (v === null ? null : (v - 1) * 100)), name: n, type: "scatter", mode: "lines",
    line: { color: n === res.strategy ? C.navy : colorFor(n), width: n === res.strategy ? 2.8 : 1.2, dash: n === "buy_and_hold" ? "dot" : "solid" },
    hovertemplate: "%{y:.2f}%",
  }));

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <Kpi label={res.strategy} value={`${signed(metaRow["total_return_%"])}%`} tone={pnl(metaRow["total_return_%"])}
          sub={<span className="flex items-center gap-2">Sharpe {fmt(metaRow.sharpe)} <LowTrades n={metaRow.total_trades} min={low} /></span>} />
        <Kpi label={`Best underlying: ${bestUnder?.strategy ?? "–"}`} value={`${signed(bestUnder?.["total_return_%"])}%`}
          tone={pnl(bestUnder?.["total_return_%"])} sub={`Sharpe ${fmt(bestUnder?.sharpe)}`} />
        <Kpi label="Buy & hold" value={`${signed(bh?.["total_return_%"])}%`} tone={pnl(bh?.["total_return_%"])} sub={`Sharpe ${fmt(bh?.sharpe)}`} />
        <Kpi label="Switches (all symbols)" value={totalSw}
          sub={`${sym}: ${sw.switches}, ${sw.switches_that_traded} changed the position`} />
      </div>

      <Panel title="Equity vs underlying strategies vs buy & hold · band = what was active"
        right={<div className="flex gap-1 flex-wrap">{syms.map((s) => (
          <button key={s} onClick={() => setSym(s)}
            className={`chip border num ${s === sym ? "tab-on" : "border-line-2 text-muted"}`}>{s}</button>
        ))}</div>}>
        <Plot height={440} data={[...lineTraces, ...bandTraces]}
          layout={{
            barmode: "overlay", hovermode: "closest", margin: { l: 56, r: 12, t: 28, b: 32 },
            legend: { orientation: "h", x: 0, y: 1.01, yanchor: "bottom", font: { size: 10 } },
            yaxis: { domain: [tl.regimes ? 0.2 : 0.13, 1], ticksuffix: "%" },
            yaxis2: { domain: [0, tl.regimes ? 0.15 : 0.08], type: "category", fixedrange: true, tickfont: { size: 10 } },
            xaxis: { anchor: "y2", type: "date" },
          }} />
        <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1 text-[11px]">
          {[...new Set([...tl.active.map((s) => s.name), ...(tl.regimes ?? []).map((s) => s.name)])].map((n) => (
            <span key={n} className="flex items-center gap-1 text-muted"><span className="inline-block w-3 h-2 rounded-sm" style={{ background: colorFor(n) }} />{n}</span>
          ))}
          <button className="ml-auto text-fg font-semibold underline" onClick={() => onOpen(sym)}>open {sym} candles ↗</button>
        </div>
        <p className="text-[11px] text-dim mt-1">Equity lines are equal-weight across all symbols; the band shows {sym}.</p>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Panel title="Time in each (all symbols, % of bars)">
          <Plot height={280} data={[{
            type: "pie", labels: Object.keys(res.time_in), values: Object.values(res.time_in), hole: 0.55, sort: false,
            marker: { colors: Object.keys(res.time_in).map(colorFor), line: { color: C.paper, width: 2 } },
            textinfo: "percent", textfont: { size: 10 }, hovertemplate: "%{label}: %{value:.1f}%<extra></extra>",
          }]} layout={{ margin: { l: 8, r: 8, t: 8, b: 8 }, legend: { orientation: "v", x: 1, y: 0.5, font: { size: 10 } } }} />
        </Panel>
        <Panel title="Switches per symbol" className="lg:col-span-2">
          <SortTable rows={Object.entries(res.switches).map(([s, v]) => ({ symbol: s, ...v, free: v.switches - v.switches_that_traded }))}
            rowKey={(r) => r.symbol} onRowClick={(r) => onOpen(r.symbol)}
            cols={[
              { key: "symbol", label: "Symbol", render: (r) => <span className="num font-semibold">{r.symbol}</span> },
              { key: "switches", label: "Switches", digits: 0 },
              { key: "switches_that_traded", label: "Changed position", digits: 0, title: "These paid trading costs" },
              { key: "free", label: "Free (same position)", digits: 0 },
            ]} />
        </Panel>
      </div>

      <Panel title="Metrics (portfolio across symbols)">
        <SortTable rows={res.summary} rowKey={(r) => r.strategy} initialSort={{ key: "sharpe", desc: true }}
          highlight={(r) => (r.role === "meta" ? "bg-panel-2 font-semibold" : r.role === "benchmark" ? "text-muted" : undefined)}
          cols={[
            { key: "strategy", label: "Strategy", render: (r) => (
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-full" style={{ background: r.role === "meta" ? C.navy : colorFor(r.strategy) }} />
                <span className="num font-semibold">{r.strategy}</span>
                <span className="chip bg-line text-muted">{r.role}</span>
                <LowTrades n={r.total_trades} min={low} />
              </span>) },
            { key: "total_return_%", label: "Ret %", tone: true },
            { key: "sharpe", label: "Sharpe", tone: true },
            { key: "sortino", label: "Sortino", tone: true },
            { key: "max_drawdown_%", label: "MaxDD %" },
            { key: "calmar", label: "Calmar", tone: true },
            { key: "total_trades", label: "Trades", digits: 0 },
            { key: "win_rate_%", label: "Win %", digits: 1 },
            { key: "exposure_%", label: "Exp %", digits: 1 },
          ]} />
      </Panel>
    </>
  );
}
