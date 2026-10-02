import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Confirm } from "../components/Confirm";
import { RecalFixStudy } from "../components/RecalFixStudy";
import { ErrorBox, Panel, Spinner } from "../components/ui";
import { api, useJob, type Job } from "../lib/api";
import { fmt } from "../lib/format";

interface Score { score?: number; sharpe?: number; "return_%"?: number; "max_drawdown_%"?: number; trades?: number; label: string; spec?: unknown }
interface Row {
  symbol: string; asset_class?: string; costs_bps?: number; windows?: number; challenger_kind?: string;
  incumbent: Score; challenger?: Score; proposed?: { label: string };
  decision: { outcome: string; change: boolean; reason: string; takeover?: boolean };
}
interface Proposal {
  date: string; generated: string; mode: string; universe_date: string | null; hand_made_symbols: string[];
  settings: Record<string, any>; symbols: Record<string, Row>; changes: string[];
}
interface UniBucket { selected: string[]; warning: string | null; rows: { symbol: string; rank?: number; outcome: string; reason: string }[] }
interface Universe { date: string; selected: string[]; added: string[]; dropped: string[]; warnings: string[]; buckets: Record<string, UniBucket> }
interface Hist { at: string; by?: string; applied?: string[]; pending?: { symbol: string; why: string }[]; skipped?: { symbol: string; why: string }[]; error?: string | null; backup?: string | null; rejected?: boolean }
interface Res {
  proposal: Proposal | null; mode: string; status: string | null; history: Hist[];
  pending: { date: string; symbols: string[] } | null; backups: string[];
  scheduler: { last_run: string | null; last_date: string | null; last_error: string | null; running: boolean };
  universe: Universe | null;
}

const OUTCOME: Record<string, [string, string]> = {
  new_job: ["new job", "chip-up"], replace: ["replace", "chip-up"], remove: ["remove", "chip-down"],
  keep_same: ["keep", ""], keep_edge: ["keep", ""], keep_better: ["keep", ""], keep_trades: ["keep", ""],
  keep_hold: ["keep (too soon)", ""], keep_changes: ["keep (changed this week)", ""],
  drop: ["drop (worse than nothing)", "chip-warn"],
  no_job_weak: ["no job", ""], no_job_trades: ["no job", ""], no_data: ["no data", "chip-warn"],
};
const STATUS: Record<string, string> = { applied: "chip-up", pending: "chip-warn", rejected: "", error: "chip-down" };
const n2 = (v?: number) => (v == null ? "–" : fmt(v, 2));
const th = "py-1 px-2 border-b border-line";

export function Recalibration() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["recal"], queryFn: () => api<Res>("/recalibration"), refetchInterval: 30000 });
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob<{ date: string; changes: string[] }>(jobId);
  const running = job.data && (job.data.status === "queued" || job.data.status === "running");
  useEffect(() => { if (job.data?.status === "done") qc.invalidateQueries({ queryKey: ["recal"] }); }, [job.data?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const d = q.data;
  const p = d?.proposal;
  const call = async (path: string, body: unknown, ok: (r: any) => string) => {
    setBusy(true); setMsg(null);
    try { const r = await api<any>(path, body); setMsg({ ok: true, text: ok(r) }); }
    catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
    finally { setBusy(false); setConfirm(false); qc.invalidateQueries({ queryKey: ["recal"] }); }
  };
  const applied = (r: Hist) => [
    r.applied?.length ? `Applied: ${r.applied.join(", ")} (the bot uses it from its next bar; backup kept).` : "",
    r.pending?.length ? `Waiting: ${r.pending.map((x) => `${x.symbol} (${x.why})`).join("; ")}.` : "",
    r.skipped?.length ? `Skipped: ${r.skipped.map((x) => `${x.symbol} (${x.why})`).join("; ")}.` : "",
    r.error ?? "",
  ].filter(Boolean).join(" ") || "Nothing to apply.";
  const rows = p ? Object.values(p.symbols).sort((a, b) => Number(b.decision.change) - Number(a.decision.change) || a.symbol.localeCompare(b.symbol)) : [];
  const decided = d?.status === "applied" || d?.status === "rejected";

  return (
    <div className="flex flex-col gap-3">
      <Panel title="Recalibration proposal" right={
        <div className="flex gap-1.5 flex-wrap">
          <button className="btn" disabled={!!running || d?.scheduler.running}
            onClick={async () => setJobId((await api<Job>("/recalibration/run", {})).id)}>
            {running ? <Spinner /> : <i className="bi bi-arrow-repeat" />} Run now</button>
          <button className="btn" disabled={!p || busy || decided} onClick={() => call("/recalibration/reject", { date: p!.date }, () => "Proposal rejected. Nothing changed.")}>
            <i className="bi bi-x-lg" /> Reject</button>
          <button className="btn btn-primary" disabled={!p || busy || !p.changes.length || decided} onClick={() => setConfirm(true)}>
            <i className="bi bi-check2" /> Apply…</button>
        </div>}>
        {q.isLoading && <Spinner />}
        <ErrorBox error={q.error} />
        {running && <p className="text-[12px] text-muted mb-2">{job.data?.message}…</p>}
        {job.data?.status === "error" && <p className="text-[12px] text-down mb-2">{job.data.error}</p>}
        {msg && <p className={`text-[12.5px] mb-2 ${msg.ok ? "text-up" : "text-down"}`}>{msg.text}</p>}
        {d && !p && <p className="text-muted text-[12.5px]">No proposal yet. It runs by itself after the daily report, or press Run now.</p>}
        {d && p && (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
              <span>As of <b className="text-fg">{p.date}</b> · universe of {p.universe_date ?? "–"} · {p.settings.lookback_days} days, tuned on {p.settings.train_days}, tested on the next {p.settings.test_days}, repeatedly</span>
              <span className={`chip ${d.mode === "apply" ? "chip-warn" : ""}`}>{d.mode === "apply" ? "apply mode: changes are applied automatically" : "propose mode: nothing changes until you press Apply"}</span>
              {d.status && <span className={`chip ${STATUS[d.status] ?? ""}`}>{d.status}</span>}
            </div>
            {d.pending && <p className="note-box rounded-lg border border-line bg-panel-2 px-3 py-2 text-[12.5px]">
              <b>Waiting to apply:</b> {d.pending.symbols.join(", ")}. Stocks change only while the market is closed, crypto only when the bot holds none of that coin; the lab retries every minute.</p>}
            {d.history.length > 0 && <p className="text-[12px] text-muted">Last action ({new Date(d.history[d.history.length - 1].at).toLocaleString()}): {d.history[d.history.length - 1].rejected ? "rejected" : applied(d.history[d.history.length - 1])}</p>}
            {d.scheduler.last_error && <p className="text-[12px] text-down">Last automatic run failed: {d.scheduler.last_error}</p>}
            {p.hand_made_symbols.length > 0 && <p className="text-[12px] text-muted">Left alone (your own jobs trade them): {p.hand_made_symbols.join(", ")}.</p>}
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] border-collapse">
                <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
                  <th className={`text-left ${th}`}>Symbol</th><th className={`text-left ${th}`}>Now</th><th className={`text-left ${th}`}>Proposed</th>
                  <th className={`text-right ${th}`} title="On test days, doing nothing = 0">Score now → best</th>
                  <th className={`text-right ${th}`}>Return</th><th className={`text-right ${th}`}>Sharpe</th><th className={`text-right ${th}`}>Max dip</th>
                  <th className={`text-right ${th}`}>Trades</th><th className={`text-left ${th}`}>Decision</th>
                </tr></thead>
                <tbody>{rows.map((r) => {
                  const c = r.challenger;
                  const [lab, cls] = OUTCOME[r.decision.outcome] ?? [r.decision.outcome, ""];
                  return (
                    <tr key={r.symbol} className={`border-b border-line/60 align-top ${r.decision.change ? "bg-panel-2" : ""}`}>
                      <td className="py-1.5 px-2 font-semibold whitespace-nowrap">{r.symbol}<div className="text-[10.5px] text-muted font-normal">{r.asset_class} · {r.costs_bps ?? "–"} bp</div></td>
                      <td className="py-1.5 px-2 max-w-[22ch]">{r.incumbent.label}</td>
                      <td className="py-1.5 px-2 max-w-[28ch]">{r.decision.change || r.decision.takeover ? <b>{r.proposed?.label}</b> : <span className="text-muted">{c?.label ?? "–"}</span>}</td>
                      <td className="py-1.5 px-2 text-right num whitespace-nowrap">{n2(r.incumbent.score)} → {n2(c?.score)}</td>
                      <td className="py-1.5 px-2 text-right num">{c ? `${n2(c["return_%"])}%` : "–"}</td>
                      <td className="py-1.5 px-2 text-right num">{n2(c?.sharpe)}</td>
                      <td className="py-1.5 px-2 text-right num">{c ? `${n2(c["max_drawdown_%"])}%` : "–"}</td>
                      <td className="py-1.5 px-2 text-right num">{c?.trades ?? "–"}</td>
                      <td className="py-1.5 px-2 max-w-[48ch]"><span className={`chip ${cls}`}>{lab}</span> <span className="text-muted text-[11.5px]">{r.decision.reason}</span></td>
                    </tr>);
                })}</tbody>
              </table>
            </div>
            <p className="text-[11.5px] text-muted">Scores come only from days the strategy had not been tuned on (Sharpe, return, dip and trading, combined; doing nothing scores 0).
              Return, Sharpe, dip and trades are for the best alternative on those days. A change needs a score at least {p.settings.min_edge} better, {p.settings.min_trades}+ trades,
              the current job in place {p.settings.min_hold_days}+ days and at most {p.settings.max_changes_per_week} change per week per symbol.</p>
          </div>
        )}
      </Panel>

      {d?.universe && <UniversePanel u={d.universe} />}

      {confirm && p && (
        <Confirm title={`Apply ${p.changes.length} change(s) to the bot's jobs?`} confirmLabel="Apply" busy={busy}
          onCancel={() => setConfirm(false)}
          onConfirm={() => call("/recalibration/apply", { confirm: true, date: p.date }, (r: Hist) => applied(r))}>
          <p>Changes: {p.changes.join(", ")}. The settings are checked first; nothing is written if they fail, and a backup of config.yaml is kept.</p>
          <p className="mt-2">Stock changes wait until the market is closed and crypto changes until the bot holds none of that coin, so no open position is switched mid-trade.
            A symbol whose job is removed has its position closed by the bot.</p>
        </Confirm>
      )}
      <RecalFixStudy />
    </div>
  );
}

function UniversePanel({ u }: { u: Universe }) {
  return (
    <Panel title={`Trading list (universe) of ${u.date}`}>
      <p className="text-[12px] text-muted mb-2">{u.selected.length} symbols · added: {u.added.join(", ") || "–"} · dropped: {u.dropped.join(", ") || "–"}.
        Ranked on how much each trades and how much it moves (IEX volume, part of the market, so for comparing only). A proposal; it changes nothing by itself.</p>
      {u.warnings.map((w) => <p key={w} className="text-[12px] text-warn">{w}</p>)}
      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-4 gap-2">
        {Object.entries(u.buckets).map(([name, b]) => (
          <div key={name} className="rounded-lg border border-line p-2">
            <div className="panel-title mb-1">{name.replace("_", " ")}</div>
            <ul className="text-[12px]">{[...b.rows].sort((x, y) => (x.rank ?? 99) - (y.rank ?? 99)).map((r) => (
              <li key={r.symbol} title={r.reason} className={r.outcome === "new" || r.outcome.startsWith("kept") ? "font-semibold" : "text-muted"}>
                {r.rank ?? "–"}. {r.symbol} <span className="text-[11px] font-normal text-muted">{r.outcome.replace("_", " ")}</span></li>))}</ul>
          </div>
        ))}
      </div>
    </Panel>
  );
}
