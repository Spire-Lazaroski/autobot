import { useState } from "react";
import { api, useJob, type Job } from "../lib/api";
import { fmt } from "../lib/format";
import { Panel, Spinner } from "./ui";

interface M { "return_%": number; sharpe: number; "max_drawdown_%": number; trades: number; "avg_win_%": number }
interface Res {
  as_of: string; days: number; symbols: string[]; timeframe: string; train_days: number; test_days: number;
  rows: { setting: string; all_days: M; test_days: M }[];
  walk_forward: M & { vs_none: { days: number; mean_bps: number; ci95_bps: [number, number]; significant: boolean }; choices: Record<string, number> };
  none_test_days: M; beats_none_oos: boolean;
}
const th = "py-1 px-2 border-b border-line";
const cells = (m: M) => [
  `${fmt(m["return_%"], 2)}%`, fmt(m.sharpe, 2), `${fmt(m["max_drawdown_%"], 2)}%`, String(m.trades), `${fmt(m["avg_win_%"], 3)}%`,
];

/** Lab: the enabled jobs with no exit layer vs every trailing-exit setting, in-sample and walk-forward. */
export function ExitRules() {
  const [days, setDays] = useState(90);
  const [source, setSource] = useState<"alpaca" | "demo">("alpaca");
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob<Res>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");
  const d = job.data?.status === "done" ? job.data.result : null;
  const head = ["Return", "Sharpe", "Deepest dip", "Trades", "Avg win kept"];
  return (
    <Panel title="Exit rules: trailing exit and re-entry (the live jobs)" right={
      <div className="flex flex-wrap gap-1.5 items-center text-[12px]">
        <label className="flex items-center gap-1">days <input className="input w-16" type="number" min={30} max={365} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
        <select className="input" value={source} onChange={(e) => setSource(e.target.value as "alpaca" | "demo")} aria-label="Data"><option value="alpaca">Alpaca data</option><option value="demo">demo data</option></select>
        <button className="btn btn-primary" disabled={!!busy} onClick={async () => setJobId((await api<Job>("/exit-study", { days, source })).id)}>{busy ? <Spinner /> : "▶"} Compare</button>
      </div>}>
      <p className="text-[12px] text-muted mb-2">
        Each enabled job's strategy with no exit layer (none) and with every trailing-exit setting: sell when a bar closes the given % below the best close since entry,
        then re-enter by the chosen rule. Costs per asset class; stocks flat before the close, like the bot.
      </p>
      {busy && <p className="text-[12px] text-muted">{job.data?.message}…</p>}
      {job.data?.status === "error" && <p className="text-[12px] text-down">{job.data.error}</p>}
      {d && (
        <div className="flex flex-col gap-2">
          <div className={`note-box rounded-lg border px-3 py-2 text-[12.5px] ${d.beats_none_oos ? "border-up" : "border-line bg-panel-2"}`}>
            <b>{d.beats_none_oos ? "The walk-forward exit choice beat 'none' out of sample after costs." : "No exit setting beat 'none' out of sample after costs."}</b>{" "}
            Walk-forward (tuned on {d.train_days} days, used on the next {d.test_days}, repeated): {fmt(d.walk_forward["return_%"], 2)}% against {fmt(d.none_test_days["return_%"], 2)}% for none on the same days;
            daily difference {d.walk_forward.vs_none.mean_bps > 0 ? "+" : ""}{fmt(d.walk_forward.vs_none.mean_bps, 2)} bp [{d.walk_forward.vs_none.ci95_bps[0]}, {d.walk_forward.vs_none.ci95_bps[1]}]
            {d.walk_forward.vs_none.significant ? " (beyond chance)" : " (within chance)"}.
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] border-collapse">
              <thead>
                <tr className="text-muted text-[10px] uppercase tracking-wider">
                  <th className={`text-left ${th}`} rowSpan={2}>Setting</th>
                  <th className={`text-center ${th}`} colSpan={5}>All {d.days} days (not tuned, but picking the best afterwards would be)</th>
                  <th className={`text-center ${th}`} colSpan={5}>Test days only</th>
                </tr>
                <tr className="text-muted text-[10px] uppercase tracking-wider">{[...head, ...head].map((h, i) => <th key={i} className={`text-right ${th}`}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={r.setting} className={`border-b border-line/60 ${r.setting === "none" ? "font-semibold" : ""}`}>
                    <td className="py-1 px-2 whitespace-nowrap">{r.setting}</td>
                    {[...cells(r.all_days), ...cells(r.test_days)].map((c, i) => <td key={i} className="py-1 px-2 text-right num">{c}</td>)}
                  </tr>))}
                <tr className="border-t-2 border-line font-semibold">
                  <td className="py-1 px-2">walk-forward choice</td>
                  {Array.from({ length: 5 }).map((_, i) => <td key={i} className="py-1 px-2 text-right text-muted">–</td>)}
                  {cells(d.walk_forward).map((c, i) => <td key={i} className="py-1 px-2 text-right num">{c}</td>)}
                </tr>
              </tbody>
            </table>
          </div>
          <p className="text-[11.5px] text-muted">{d.symbols.join(", ")} · {d.timeframe} · as of {d.as_of}. Walk-forward picks:{" "}
            {Object.entries(d.walk_forward.choices).map(([k, v]) => `${k} ×${v}`).join(", ")}. If the jobs were chosen by recalibration on these days, "none" over all days is flattered: compare on the test days.</p>
        </div>
      )}
    </Panel>
  );
}
