import { useMemo, useState } from "react";
import { Plot } from "../components/Plot";
import { SettingsBar } from "../components/SettingsBar";
import { SortTable } from "../components/SortTable";
import { pickedOf } from "../components/StrategyCards";
import { Empty, ErrorBox, Kpi, LowTrades, Panel, Progress, Spinner } from "../components/ui";
import { api, useJob, useStrategies, type Frame, type Job, type Params, type Row } from "../lib/api";
import { C } from "../lib/chart";
import { fmt, nyTime, pnl, signed, toDates } from "../lib/format";
import { useSettings } from "../lib/settings";
import { usePageState } from "../lib/usePageState";

interface WFResult {
  windows: Row[];
  equity: Frame;
  metrics: Record<"walk_forward" | "fixed" | "buy_and_hold", Row>;
  fixed_params: Params;
  combos: number;
  oos_start: number;
  grid: Record<string, unknown[]>;
  param_changes: number;
  low_trades_threshold: number;
}

const TF_MIN: Record<string, number> = { "1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 390 };
const RANKS = ["sharpe", "sortino", "calmar", "total_return_%"];
const LINES = {
  walk_forward: { label: "walk-forward (re-optimized)", color: C.navy, dash: "solid", width: 2.2 },
  fixed: { label: "fixed params", color: C.gold, dash: "solid", width: 1.4 },
  buy_and_hold: { label: "buy & hold", color: C.grey, dash: "dot", width: 1.2 },
} as const;

export function WalkForward() {
  const { run, s, picked } = useSettings();
  const strategies = useStrategies();
  const searchable = (strategies.data ?? []).filter((x) => Object.keys(x.grid).length);
  const [cfg, setCfg] = usePageState("wf.cfg", { strategy: "ema_cross", train_days: 20, test_days: 5, rank_by: "sharpe" });
  const [jobId, setJobId] = usePageState<string | null>("wf.job", null);
  const [err, setErr] = useState<unknown>(null);
  const job = useJob<WFResult>(jobId);

  // windows are set in trading days and sent as bars
  const barsPerDay = s.timeframe === "1Day" ? 1 : Math.max(1, Math.floor((s.rth_only ? 390 : 1440) / TF_MIN[s.timeframe]));
  const trainBars = Math.max(50, Math.round(cfg.train_days * barsPerDay));
  const testBars = Math.max(10, Math.round(cfg.test_days * barsPerDay));
  const spec = searchable.find((x) => x.name === cfg.strategy);
  const fixedParams = spec ? pickedOf(spec, picked).params : undefined;

  const go = async () => {
    setErr(null);
    try {
      const j = await api<Job>("/walkforward", { ...run, strategy: cfg.strategy, train_bars: trainBars, test_bars: testBars, rank_by: cfg.rank_by, fixed_params: fixedParams });
      setJobId(j.id);
    } catch (e) {
      setErr(e);
    }
  };
  const running = job.data && (job.data.status === "queued" || job.data.status === "running");
  const res = job.data?.status === "done" ? job.data.result : undefined;
  const numIn = (key: "train_days" | "test_days", label: string) => (
    <label className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-muted">{label}</span>
      <input className="input w-20" type="number" min={1} value={cfg[key]}
        onChange={(e) => setCfg({ ...cfg, [key]: Math.max(1, Number(e.target.value) || 1) })} />
    </label>
  );

  return (
    <div className="flex flex-col gap-3">
      <SettingsBar action={<button className="btn btn-primary" onClick={go} disabled={!spec || !!running}>{running ? <Spinner /> : "⏩"} Run walk-forward</button>} />
      <Panel title="Walk-forward setup">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted">Strategy</span>
            <select className="input" value={cfg.strategy} onChange={(e) => setCfg({ ...cfg, strategy: e.target.value })}>
              {searchable.map((x) => <option key={x.name}>{x.name}</option>)}
            </select>
          </label>
          {numIn("train_days", "Train days")}
          {numIn("test_days", "Test days")}
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted">Rank by</span>
            <select className="input" value={cfg.rank_by} onChange={(e) => setCfg({ ...cfg, rank_by: e.target.value })}>
              {RANKS.map((r) => <option key={r}>{r}</option>)}
            </select>
          </label>
          <div className="text-[11px] text-muted num leading-relaxed">
            = train {trainBars} bars · test {testBars} bars · step {testBars} bars
            {spec && <><br />grid: {Object.entries(spec.grid).map(([k, v]) => `${k}∈[${v.join(",")}]`).join(" ")}</>}
            {fixedParams && <><br />fixed comparison uses the Lab params: {Object.entries(fixedParams).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" ")}</>}
          </div>
        </div>
      </Panel>
      <ErrorBox error={err ?? job.error ?? (job.data?.status === "error" ? job.data.error : null)} />
      {running && <Panel><Progress value={job.data!.progress} label={job.data!.message} /></Panel>}
      {!jobId && <Empty>Each window re-optimizes on the last <b>{cfg.train_days}</b> days, then trades the next <b>{cfg.test_days}</b> days with
        what it picked. Only those unseen test slices make up the curve, so it's an honest estimate of running the optimizer live.</Empty>}
      {res && <Results res={res} />}
    </div>
  );
}

function Results({ res }: { res: WFResult }) {
  const x = useMemo(() => toDates(res.equity.t), [res]);
  const m = res.metrics;
  const low = res.low_trades_threshold;
  const keys = Object.keys(res.grid).filter((k) => res.grid[k].length > 1);
  const edge = (m.walk_forward["total_return_%"] ?? 0) - (m.fixed["total_return_%"] ?? 0);
  const bands = res.windows.map((w, i) => ({
    type: "rect", xref: "x", yref: "paper", x0: new Date(w.test_start * 1000), x1: new Date(w.test_end * 1000), y0: 0, y1: 1,
    fillcolor: i % 2 ? C.sunk : "rgba(0,0,0,0)", line: { width: 0 }, layer: "below",
  }));

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <Kpi label="Walk-forward return" value={`${signed(m.walk_forward["total_return_%"])}%`} tone={pnl(m.walk_forward["total_return_%"])}
          sub={`Sharpe ${fmt(m.walk_forward.sharpe)}`} />
        <Kpi label="Fixed params return" value={`${signed(m.fixed["total_return_%"])}%`} tone={pnl(m.fixed["total_return_%"])}
          sub={`Sharpe ${fmt(m.fixed.sharpe)}`} />
        <Kpi label="Re-optimizing adds" value={`${signed(edge)}%`} tone={pnl(edge)}
          sub={`B&H ${signed(m.buy_and_hold["total_return_%"])}%`} />
        <Kpi label="Windows · param changes" value={`${res.windows.length} · ${res.param_changes}`}
          sub={<span className="flex items-center gap-2">{m.walk_forward.trades} trades <LowTrades n={m.walk_forward.trades} min={low} /></span>} />
      </div>

      <Panel title={`Out-of-sample equity from ${nyTime(res.oos_start)} · shaded = alternate test windows`}>
        <Plot height={380}
          data={(Object.keys(LINES) as (keyof typeof LINES)[]).map((k) => ({
            x, y: res.equity.series[k].map((v) => (v === null ? null : (v - 1) * 100)), name: LINES[k].label, type: "scatter", mode: "lines",
            line: { color: LINES[k].color, dash: LINES[k].dash, width: LINES[k].width }, hovertemplate: "%{y:.2f}%",
          }))}
          layout={{ shapes: bands, yaxis: { ticksuffix: "%" }, margin: { l: 48, r: 12, t: 8, b: 32 } }} />
      </Panel>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Metrics over the out-of-sample span">
          <SortTable rows={(Object.keys(LINES) as (keyof typeof LINES)[]).map((k) => ({ curve: LINES[k].label, ...m[k] }))}
            rowKey={(r) => r.curve}
            cols={[
              { key: "curve", label: "Curve", render: (r) => <span className="flex items-center gap-1.5">{r.curve}<LowTrades n={r.trades} min={low} /></span> },
              { key: "total_return_%", label: "Ret %", tone: true },
              { key: "sharpe", label: "Sharpe", tone: true },
              { key: "sortino", label: "Sortino", tone: true },
              { key: "max_drawdown_%", label: "MaxDD %" },
              { key: "calmar", label: "Calmar", tone: true },
              { key: "trades", label: "Trades", digits: 0 },
            ]} />
        </Panel>
        <Panel title="Chosen parameters per window (stable = trustworthy)">
          {keys.length ? (
            // one small stacked panel per parameter, sharing the time axis
            <Plot height={70 * keys.length + 60}
              data={keys.map((k, i) => ({
                x: res.windows.map((w) => new Date(w.test_start * 1000)), y: res.windows.map((w) => String(w.params[k])), name: k,
                type: "scatter", mode: "lines+markers", line: { shape: "hv", width: 1.5, color: C.series[i % C.series.length] },
                yaxis: i ? `y${i + 1}` : "y", showlegend: false, hovertemplate: `${k}=%{y}<extra></extra>`,
              }))}
              layout={{
                hovermode: "x unified", margin: { l: 90, r: 12, t: 8, b: 32 },
                xaxis: { anchor: keys.length > 1 ? `y${keys.length}` : "y" },
                ...Object.fromEntries(keys.map((k, i) => {
                  const h = 1 / keys.length;
                  return [i ? `yaxis${i + 1}` : "yaxis", {
                    domain: [1 - (i + 1) * h + 0.06, 1 - i * h], title: { text: k, font: { size: 10 } },
                    type: "category", categoryorder: "array", categoryarray: (res.grid[k] as unknown[]).map(String),
                    gridcolor: C.line, tickfont: { size: 10 },
                  }];
                })),
              }} />
          ) : <p className="text-muted">One-point grid.</p>}
        </Panel>
      </div>

      <Panel title="Windows">
        <div className="max-h-96 overflow-y-auto">
          <SortTable rows={res.windows} rowKey={(r) => String(r.window)} initialSort={{ key: "window", desc: false }}
            cols={[
              { key: "window", label: "#", digits: 0 },
              { key: "test_start", label: "Test from (NY)", render: (r) => <span className="num">{nyTime(r.test_start)}</span> },
              { key: "test_end", label: "to", render: (r) => <span className="num">{nyTime(r.test_end)}</span> },
              { key: "params", label: "Params (chosen on train)", render: (r) => (
                <span className="num text-muted">{keys.map((k) => `${k}=${r.params[k]}`).join(" ")}</span>) },
              { key: "train_score", label: "Train score", tone: true },
              { key: "test_sharpe", label: "Test Sh", tone: true },
              { key: "test_return_%", label: "Test %", tone: true },
              { key: "test_bars", label: "Bars", digits: 0, render: (r) => (
                <span className={r.partial ? "text-warn" : ""} title={r.partial ? "Partial window: too short to judge" : undefined}>{r.test_bars}{r.partial ? "*" : ""}</span>) },
            ]} />
        </div>
      </Panel>
    </>
  );
}
