import { useEffect, useMemo, useState } from "react";
import { DetailView } from "../components/DetailView";
import { Plot } from "../components/Plot";
import { SettingsBar } from "../components/SettingsBar";
import { SortTable } from "../components/SortTable";
import { pickedOf } from "../components/StrategyCards";
import { Empty, ErrorBox, LowTrades, Panel, Progress, Spinner } from "../components/ui";
import { api, useJob, useStrategies, type Job, type Params, type Row, type RunReq } from "../lib/api";
import { C, DIVERGING, SEQUENTIAL } from "../lib/chart";
import { colorFor, fmt, pnl } from "../lib/format";
import { useSettings } from "../lib/settings";
import { usePageState } from "../lib/usePageState";

interface SearchResult {
  results: Row[];
  best: Row[];
  grids: Record<string, Record<string, unknown[]>>;
  low_trades_threshold: number;
}

const paramLabel = (p: Params, keys: string[]) => keys.map((k) => `${k}=${JSON.stringify(p[k])}`).join(" ");

export function Search() {
  const { run, s, set, picked, setPicked } = useSettings();
  const strategies = useStrategies();
  const searchable = (strategies.data ?? []).filter((x) => Object.keys(x.grid).length);
  const [names, setNames] = usePageState<string[] | null>("search.names", null);
  const [jobId, setJobId] = usePageState<string | null>("search.job", null);
  const [runReq, setRunReq] = usePageState<RunReq | null>("search.run", null);
  const [open, setOpen] = useState<{ strategy: string; params: Params } | null>(null);

  // default selection: what's switched on in the Lab
  const selected = names ?? searchable.filter((x) => pickedOf(x, picked).on).map((x) => x.name);
  const job = useJob<SearchResult>(jobId);
  const [startErr, setStartErr] = useState<unknown>(null);

  const go = async () => {
    setStartErr(null);
    try {
      const j = await api<Job>("/search", { ...run, names: selected, train_frac: s.train_frac });
      setRunReq(run);
      setJobId(j.id);
    } catch (e) {
      setStartErr(e);
    }
  };
  const running = job.data && (job.data.status === "queued" || job.data.status === "running");
  const res = job.data?.status === "done" ? job.data.result : undefined;

  return (
    <div className="flex flex-col gap-3">
      <SettingsBar
        extra={
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted">Train share</span>
            <input className="input w-20" type="number" min={0.5} max={0.9} step={0.05} value={s.train_frac}
              onChange={(e) => set({ train_frac: Math.min(0.9, Math.max(0.5, Number(e.target.value) || 0.7)) })} />
          </label>
        }
        action={<button className="btn btn-primary" onClick={go} disabled={!selected.length || !!running}>
          {running ? <Spinner /> : "🔎"} Run search</button>} />

      <Panel title="Strategies to search (each one's full grid)">
        <div className="flex flex-wrap gap-1.5">
          {searchable.map((x) => {
            const on = selected.includes(x.name);
            const n = Object.values(x.grid).reduce((a, v) => a * v.length, 1);
            return (
              <button key={x.name} onClick={() => setNames(on ? selected.filter((y) => y !== x.name) : [...selected, x.name])}
                className={`chip border num normal-case ${on ? "border-line-2 text-fg bg-panel-2" : "border-line text-dim"}`}
                style={on ? { borderLeft: `3px solid ${colorFor(x.name)}` } : undefined}>
                {x.name} <span className="text-dim ml-1">×{n}</span>
              </button>
            );
          })}
        </div>
      </Panel>

      <ErrorBox error={startErr ?? job.error ?? (job.data?.status === "error" ? job.data.error : null)} />
      {running && <Panel><Progress value={job.data!.progress} label={job.data!.message} /></Panel>}
      {!jobId && <Empty>Parameters are <b>chosen on the first {Math.round(s.train_frac * 100)}%</b> of the history and
        <b> scored on the rest</b>, which the search never saw. Points far below the diagonal are overfit.</Empty>}
      {res && runReq && <Results res={res} onOpen={setOpen}
        onUse={(name, params) => setPicked({ ...picked, [name]: { on: true, params } })} />}

      {open && runReq && (
        <DetailView run={runReq} strategy={open.strategy} params={open.params} symbols={runReq.data.symbols} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}

function Results({ res, onOpen, onUse }: {
  res: SearchResult;
  onOpen: (o: { strategy: string; params: Params }) => void;
  onUse: (name: string, params: Params) => void;
}) {
  const low = res.low_trades_threshold;
  const byStrat = useMemo(() => {
    const m: Record<string, Row[]> = {};
    for (const r of res.results) (m[r.strategy] ??= []).push(r);
    return m;
  }, [res]);
  const all = res.results.flatMap((r) => [r.train_sharpe, r.test_sharpe]).filter((v) => v !== null);
  const lo = Math.min(...all), hi = Math.max(...all);
  const [used, setUsed] = useState<string | null>(null);

  return (
    <>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Train vs test Sharpe · every parameter set · click a point">
          <Plot height={420}
            onClick={(e) => { const p = e.points?.[0]?.customdata; if (p) onOpen(p); }}
            data={[
              { x: [lo, hi], y: [lo, hi], mode: "lines", line: { dash: "dot", color: C.inkFaint, width: 1 }, hoverinfo: "skip", showlegend: false },
              ...Object.entries(byStrat).map(([name, rows]) => ({
                x: rows.map((r) => r.train_sharpe), y: rows.map((r) => r.test_sharpe), name, type: "scatter", mode: "markers",
                marker: { color: colorFor(name), size: 8, opacity: 0.85, symbol: rows.map((r) => (r.test_trades_total < low ? "circle-open" : "circle")) },
                text: rows.map((r) => r.params), customdata: rows.map((r) => ({ strategy: name, params: r.param_values })),
                hovertemplate: `${name}<br>%{text}<br>train %{x:.2f} · test %{y:.2f}<extra></extra>`,
              })),
            ]}
            layout={{ hovermode: "closest", xaxis: { title: { text: "train Sharpe" } }, yaxis: { title: { text: "test Sharpe (unseen)" } }, margin: { l: 52, r: 12, t: 8, b: 40 },
              annotations: [{ x: hi, y: lo, xanchor: "right", yanchor: "bottom", showarrow: false, text: "overfit ↘", font: { color: C.loss, size: 10.5 } }] }} />
          <p className="text-[11px] text-dim mt-1">Open circles: fewer than {low} test trades.</p>
        </Panel>
        <ParamHeatmap res={res} onOpen={onOpen} />
      </div>

      <Panel title="Best parameters per strategy · chosen on train, sorted by test Sharpe">
        <SortTable rows={res.best} rowKey={(r) => r.strategy} initialSort={{ key: "test_sharpe", desc: true }}
          onRowClick={(r) => onOpen({ strategy: r.strategy, params: r.param_values })}
          cols={[
            { key: "strategy", label: "Strategy", render: (r) => (
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-full" style={{ background: colorFor(r.strategy) }} />
                <span className="num font-semibold">{r.strategy}</span>
                <LowTrades n={r.test_trades_total} min={low} />
              </span>) },
            { key: "params", label: "Params", render: (r) => <span className="num text-muted">{r.params}</span> },
            { key: "train_sharpe", label: "Train Sh", tone: true },
            { key: "test_sharpe", label: "Test Sh", tone: true },
            { key: "train_return_%", label: "Train %", tone: true },
            { key: "test_return_%", label: "Test %", tone: true },
            { key: "test_max_dd_%", label: "Test DD %" },
            { key: "test_trades_total", label: "Test tr", digits: 0 },
            { key: "use", label: "", render: (r) => (
              <button className="text-[11px] text-fg font-semibold underline" onClick={(e) => {
                e.stopPropagation(); onUse(r.strategy, r.param_values); setUsed(r.strategy);
              }}>{used === r.strategy ? "✓ in Lab" : "use in Lab"}</button>) },
          ]} />
      </Panel>

      <details className="panel p-3">
        <summary className="panel-title cursor-pointer">All {res.results.length} parameter sets</summary>
        <div className="max-h-[480px] overflow-y-auto mt-2">
          <SortTable rows={res.results} rowKey={(r) => `${r.strategy}|${r.params}`} initialSort={{ key: "test_sharpe", desc: true }}
            onRowClick={(r) => onOpen({ strategy: r.strategy, params: r.param_values })}
            cols={[
              { key: "strategy", label: "Strategy", render: (r) => <span className="num">{r.strategy}</span> },
              { key: "params", label: "Params", render: (r) => <span className="num text-muted">{r.params}</span> },
              { key: "train_sharpe", label: "Train Sh", tone: true },
              { key: "test_sharpe", label: "Test Sh", tone: true },
              { key: "test_return_%", label: "Test %", tone: true },
              { key: "test_max_dd_%", label: "Test DD %" },
              { key: "test_trades_total", label: "Test tr", digits: 0, render: (r) => (
                <span className={r.test_trades_total < low ? "text-warn" : ""}>{r.test_trades_total}</span>) },
            ]} />
        </div>
      </details>
    </>
  );
}

const METRICS = [
  { key: "test_sharpe", label: "test Sharpe" },
  { key: "train_sharpe", label: "train Sharpe" },
  { key: "test_return_%", label: "test return %" },
  { key: "test_trades_total", label: "test trades" },
];

/** Two parameters on the axes, a metric as color, the rest fixed at the best set's values.
 *  A broad warm region is a robust parameter choice; a lone bright cell is luck. */
function ParamHeatmap({ res, onOpen }: {
  res: SearchResult;
  onOpen: (o: { strategy: string; params: Params }) => void;
}) {
  const names = Object.keys(res.grids).filter((n) => res.results.some((r) => r.strategy === n));
  const [name, setName] = useState(() => names.find((n) => Object.keys(res.grids[n]).length >= 2) ?? names[0]);
  const keys = Object.keys(res.grids[name] ?? {}).filter((k) => (res.grids[name][k] ?? []).length > 1);
  const [xk, setXk] = useState(keys[0]);
  const [yk, setYk] = useState(keys[1] ?? keys[0]);
  const [metric, setMetric] = useState("test_sharpe");
  useEffect(() => { setXk(keys[0]); setYk(keys[1] ?? keys[0]); }, [name]);

  const rows = res.results.filter((r) => r.strategy === name);
  const best = res.best.find((r) => r.strategy === name);
  const fixed = Object.fromEntries(Object.entries(best?.param_values ?? {}).filter(([k]) => k !== xk && k !== yk));
  const matches = rows.filter((r) => Object.entries(fixed).every(([k, v]) => JSON.stringify(r.param_values[k]) === JSON.stringify(v)));
  const xs = (res.grids[name]?.[xk] ?? []) as (number | string)[];
  const ys = (res.grids[name]?.[yk] ?? []) as (number | string)[];
  const cell = (xv: unknown, yv: unknown) =>
    matches.find((r) => JSON.stringify(r.param_values[xk]) === JSON.stringify(xv) && JSON.stringify(r.param_values[yk]) === JSON.stringify(yv));

  const oneD = !yk || yk === xk;
  return (
    <Panel title="Parameter map" right={
      <div className="flex gap-1.5 flex-wrap justify-end">
        <select className="input" value={name} onChange={(e) => setName(e.target.value)}>{names.map((n) => <option key={n}>{n}</option>)}</select>
        {keys.length > 0 && <select className="input" value={xk} onChange={(e) => setXk(e.target.value)} aria-label="x parameter">{keys.map((k) => <option key={k}>{k}</option>)}</select>}
        {keys.length > 1 && <select className="input" value={yk} onChange={(e) => setYk(e.target.value)} aria-label="y parameter">{keys.map((k) => <option key={k}>{k}</option>)}</select>}
        <select className="input" value={metric} onChange={(e) => setMetric(e.target.value)}>{METRICS.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</select>
      </div>}>
      {keys.length === 0 ? <p className="text-muted text-[12px]">No parameters to vary.</p> : oneD ? (
        <Plot height={380} data={["train_sharpe", "test_sharpe"].map((k) => ({
          x: xs.map(String), y: xs.map((v) => rows.find((r) => JSON.stringify(r.param_values[xk]) === JSON.stringify(v))?.[k] ?? null),
          name: k, type: "bar", marker: { color: k === "test_sharpe" ? C.navy : C.lineStrong },
        }))} layout={{ xaxis: { title: { text: xk }, type: "category" }, barmode: "group", margin: { l: 44, r: 12, t: 8, b: 40 } }} />
      ) : (
        <Plot height={380}
          onClick={(e) => { const p = e.points?.[0]; const r = p && cell(xs[p.pointNumber[1]], ys[p.pointNumber[0]]); if (r) onOpen({ strategy: name, params: r.param_values }); }}
          data={[{
            type: "heatmap", x: xs.map(String), y: ys.map(String),
            z: ys.map((yv) => xs.map((xv) => cell(xv, yv)?.[metric] ?? null)),
            text: ys.map((yv) => xs.map((xv) => { const r = cell(xv, yv); return r ? `${fmt(r[metric], metric.includes("trades") ? 0 : 2)}${r.test_trades_total < res.low_trades_threshold ? "*" : ""}` : "invalid"; })),
            texttemplate: "%{text}", textfont: { size: 11, family: "Inter, sans-serif", color: C.ink },
            colorscale: metric.includes("trades") ? SEQUENTIAL : DIVERGING,
            zmid: metric.includes("trades") ? undefined : 0, xgap: 2, ygap: 2, colorbar: { thickness: 8 },
            hovertemplate: `${xk}=%{x} ${yk}=%{y}<br>%{z:.2f}<extra></extra>`,
          }]}
          layout={{ hovermode: "closest", xaxis: { title: { text: xk }, type: "category" }, yaxis: { title: { text: yk }, type: "category" }, margin: { l: 56, r: 12, t: 8, b: 40 } }} />
      )}
      <p className="text-[11px] text-dim mt-1 num">
        {Object.keys(fixed).length ? `Fixed at best: ${paramLabel(fixed, Object.keys(fixed))}. ` : ""}
        * = fewer than {res.low_trades_threshold} test trades. Look for broad green regions, not one bright cell.
        {best && <> Best on train: <span className={pnl(best.test_sharpe)}>{paramLabel(best.param_values, keys)}</span></>}
      </p>
    </Panel>
  );
}
