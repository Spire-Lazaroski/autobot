import { useState } from "react";
import { api, useJob, type Job } from "../lib/api";
import { C } from "../lib/chart";
import { fmt } from "../lib/format";
import { Plot } from "./Plot";
import { Panel, Spinner } from "./ui";

interface V { "total_return_%": number; sharpe: number; "max_drawdown_%": number; halts_loss_limit: number; halts_profit_lock: number; daily_equity: Record<string, number> }
interface Res { symbols: string[]; days: number; trigger_pct: number; giveback_pct: number; loss_limit_pct: number; sizing: string; results: Record<string, V> }

const LABEL: Record<string, string> = { off: "No account rules", loss_limit: "Daily loss limit only", profit_lock: "Loss limit + profit lock" };
const th = "py-1 px-2 border-b border-line";

/** Lab: the enabled jobs backtested as one account, with and without the account rules. */
export function AccountRules() {
  const [days, setDays] = useState(60);
  const [trigger, setTrigger] = useState(1.5);
  const [giveback, setGiveback] = useState(50);
  const [source, setSource] = useState<"alpaca" | "demo">("alpaca");
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob<Res>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");
  const d = job.data?.status === "done" ? job.data.result : null;
  const run = async () => setJobId((await api<Job>("/account-rules", { days, trigger_pct: trigger, giveback_pct: giveback, source })).id);

  return (
    <Panel title="Account rules: daily loss limit and profit lock (the live jobs as one account)" right={
      <div className="flex flex-wrap gap-1.5 items-center text-[12px]">
        <label className="flex items-center gap-1">days <input className="input w-16" type="number" min={5} max={365} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
        <label className="flex items-center gap-1">lock from <input className="input w-16" type="number" step={0.1} min={0.1} value={trigger} onChange={(e) => setTrigger(Number(e.target.value))} />%</label>
        <label className="flex items-center gap-1">give back <input className="input w-16" type="number" min={1} max={99} value={giveback} onChange={(e) => setGiveback(Number(e.target.value))} />%</label>
        <select className="input" value={source} onChange={(e) => setSource(e.target.value as "alpaca" | "demo")} aria-label="Data"><option value="alpaca">Alpaca data</option><option value="demo">demo data</option></select>
        <button className="btn btn-primary" onClick={run} disabled={!!busy}>{busy ? <Spinner /> : "▶"} Compare</button>
      </div>}>
      <p className="text-[12px] text-muted mb-2">
        The enabled jobs in config.yaml, sized like the live bot, with each asset class's costs and stocks flat before the close.
        Profit lock: once the day's gain reaches the "lock from" level, the bot sells everything and stops for the day if the gain then falls by more than the "give back" share of the day's best.
        The rules reset every New York day.
      </p>
      {busy && <p className="text-[12px] text-muted">{job.data?.message}…</p>}
      {job.data?.status === "error" && <p className="text-[12px] text-down">{job.data.error}</p>}
      {d && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] border-collapse">
              <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
                <th className={`text-left ${th}`}>Rules</th><th className={`text-right ${th}`}>Return</th><th className={`text-right ${th}`}>Sharpe</th>
                <th className={`text-right ${th}`}>Deepest dip</th><th className={`text-right ${th}`}>Days stopped: loss</th><th className={`text-right ${th}`}>Days stopped: lock</th>
              </tr></thead>
              <tbody>{Object.entries(d.results).map(([k, v]) => (
                <tr key={k} className="border-b border-line/60">
                  <td className="py-1 px-2 font-semibold">{LABEL[k] ?? k}</td>
                  <td className="py-1 px-2 text-right num">{fmt(v["total_return_%"], 2)}%</td>
                  <td className="py-1 px-2 text-right num">{fmt(v.sharpe, 2)}</td>
                  <td className="py-1 px-2 text-right num">{fmt(v["max_drawdown_%"], 2)}%</td>
                  <td className="py-1 px-2 text-right num">{v.halts_loss_limit}</td>
                  <td className="py-1 px-2 text-right num">{v.halts_profit_lock}</td>
                </tr>))}</tbody>
            </table>
            <p className="text-[11.5px] text-muted mt-1">{d.symbols.join(", ")} · {d.days} days · sizing {d.sizing} · loss limit {d.loss_limit_pct}% ·
              lock from {d.trigger_pct}%, give back {d.giveback_pct}%. A single period is a small sample: compare a few windows before switching it on.</p>
          </div>
          <Plot height={260} area="main" data={Object.entries(d.results).map(([k, v], i) => ({
            x: Object.keys(v.daily_equity), y: Object.values(v.daily_equity).map((e) => (e - 1) * 100), name: LABEL[k] ?? k,
            type: "scatter", mode: "lines", line: { color: [C.inkFaint, C.navy, C.gold][i % 3], width: 2 },
          }))} layout={{ yaxis: { ticksuffix: "%" }, margin: { l: 48, r: 10, t: 8, b: 36 }, legend: { orientation: "h" } }} />
        </div>
      )}
    </Panel>
  );
}
