import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ErrorBox, Panel, Spinner } from "../components/ui";
import { api, useJob, type Job } from "../lib/api";
import { fmt } from "../lib/format";

interface Diff { days: number; mean_bps: number; ci95_bps: [number, number]; days_different?: number; significant: boolean }
interface SumRow { variant: string; "total_return_%": number; sharpe: number; "max_drawdown_%": number; trades: number; vs_base: Diff | null }
interface Compare { params: Record<string, number>; summary: SumRow[] }
interface WfM { "total_return_%": number; sharpe: number; "max_drawdown_%": number; trades: number }
interface Wf { walk_forward: WfM; base_alone: WfM; vs_base: Diff; windows: number; windows_guard_on: number; oos_start: string }
interface Check { published: number; exact: number; off_by_days: string[]; missed: string[]; false: string[]; news_only: string[] }
interface Study {
  generated: string; timeframe: string; start: string; end: string; costs_bps: number; stand_aside_min: number;
  materiality: Record<string, number>;
  earnings_check: Record<string, Check>;
  blocked: Record<string, Record<string, Record<string, { "stand_aside_%": number; "blackout_%": number; "either_%": number }>>>;
  groups: Record<string, { compare: Record<string, Compare>; compare_detected_dates?: Record<string, Compare>; walk_forward?: Record<string, Wf> }>;
}

const VLABEL: Record<string, string> = {
  base: "Strategy alone", aside_no_new: "Stand aside · no new trades", aside_flat: "Stand aside · go flat",
  blackout_no_new: "Earnings blackout · no new trades", blackout_flat: "Earnings blackout · go flat",
  both_no_new: "Both · no new trades", both_flat: "Both · go flat",
};
const GLABEL: Record<string, string> = { stocks: "Stocks (AAPL, NVDA, TSLA)", etfs: "ETFs (SPY, QQQ)" };
const th = "py-1 px-2 border-b border-line";

function DiffCell({ d }: { d: Diff | null }) {
  if (!d) return <td className="py-1 px-2 text-right text-muted">–</td>;
  const tone = d.significant ? (d.mean_bps > 0 ? "text-up font-semibold" : "text-down font-semibold") : "text-muted";
  return (
    <td className={`py-1 px-2 text-right num ${tone}`} title={`95% CI ${d.ci95_bps[0]} to ${d.ci95_bps[1]} bp per day over ${d.days} days`}>
      {d.mean_bps > 0 ? "+" : ""}{d.mean_bps.toFixed(2)} <span className="text-dim">[{d.ci95_bps[0]}, {d.ci95_bps[1]}]</span>{d.significant ? " *" : ""}
    </td>
  );
}

function CompareTable({ c }: { c: Compare }) {
  return (
    <table className="w-full text-[12px] border-collapse">
      <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
        <th className={`text-left ${th}`}>Variant</th><th className={`text-right ${th}`}>Return</th><th className={`text-right ${th}`}>Sharpe</th>
        <th className={`text-right ${th}`}>Max drawdown</th><th className={`text-right ${th}`}>Trades</th>
        <th className={`text-right ${th}`}>vs alone, bp/day [95% CI]</th>
      </tr></thead>
      <tbody>{c.summary.map((r) => (
        <tr key={r.variant} className="border-b border-line/60">
          <td className={`py-1 px-2 ${r.variant === "base" ? "font-semibold" : ""}`}>{VLABEL[r.variant] ?? r.variant}</td>
          <td className="py-1 px-2 text-right num">{fmt(r["total_return_%"], 2)}%</td>
          <td className="py-1 px-2 text-right num">{fmt(r.sharpe, 2)}</td>
          <td className="py-1 px-2 text-right num">{fmt(r["max_drawdown_%"], 1)}%</td>
          <td className="py-1 px-2 text-right num">{fmt(r.trades, 0)}</td>
          <DiffCell d={r.vs_base} />
        </tr>))}</tbody>
    </table>
  );
}

export function NewsGuardStudy() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["news-guard-study"], queryFn: () => api<Study>("/news/guard-study"), retry: false });
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob<{ generated: string }>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");
  useEffect(() => { if (job.data?.status === "done") qc.invalidateQueries({ queryKey: ["news-guard-study"] }); }, [job.data?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const recompute = async () => setJobId((await api<Job>("/news/guard-study", {})).id);
  const d = q.data;

  const anyBetter = d ? Object.values(d.groups).some((g) =>
    [...Object.values(g.compare).flatMap((c) => c.summary.map((r) => r.vs_base)), ...Object.values(g.walk_forward ?? {}).map((w) => w.vs_base)]
      .some((v) => v && v.significant && v.mean_bps > 0)) : false;

  return (
    <Panel title="Does the news guard help? (backtest)" right={
      <button className="btn" onClick={recompute} disabled={!!busy}>{busy ? <Spinner /> : <i className="bi bi-arrow-repeat" />} Recompute</button>}>
      {busy && <p className="text-[12px] text-muted mb-2">{job.data?.message}…</p>}
      {job.data?.status === "error" && <p className="text-[12px] text-down mb-2">{job.data.error}</p>}
      {q.isLoading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {d && (
        <div className="flex flex-col gap-3">
          <p className="text-[12px] text-muted">
            {d.start} to {d.end} · {d.timeframe} bars, regular hours · costs {d.costs_bps} bp per trade · stand aside {d.stand_aside_min} min after a story
            with materiality ≥ {d.materiality.stocks} (stocks) or ≥ {d.materiality.etfs} (ETFs) · blackout = the session before and the session that reacts to results ·
            each strategy uses the live job's settings
          </p>
          <div className={`note-box rounded-lg border px-3 py-2 text-[12.5px] leading-relaxed ${anyBetter ? "border-up" : "border-line bg-panel-2"}`}>
            <b>{anyBetter ? "At least one variant did better than the strategy alone by more than chance." : "No variant did better than the strategy alone by more than chance."}</b>{" "}
            The last column is the average daily difference from the strategy alone, in basis points (0.01%). A result counts only when its whole
            95% interval is above zero (marked *).
          </div>

          {Object.entries(d.groups).map(([g, gr]) => (
            <section key={g} className="flex flex-col gap-2">
              <span className="panel-title">{GLABEL[g] ?? g}</span>
              <div className="grid grid-cols-1 2xl:grid-cols-2 gap-3">
                {Object.entries(gr.compare).map(([base, c]) => (
                  <div key={base} className="overflow-x-auto">
                    <p className="text-[12px] font-semibold mb-1">{base} <span className="text-muted font-normal num">{Object.entries(c.params).map(([k, v]) => `${k} ${v}`).join(", ")}</span></p>
                    <CompareTable c={c} />
                  </div>
                ))}
              </div>
              {gr.walk_forward && (
                <div className="overflow-x-auto">
                  <p className="text-[12px] font-semibold mb-1">Walk-forward: guard settings re-chosen every 20 sessions from the previous 60 ("off" allowed)</p>
                  <table className="w-full text-[12px] border-collapse">
                    <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
                      <th className={`text-left ${th}`}>Strategy</th><th className={`text-right ${th}`}>With guard</th><th className={`text-right ${th}`}>Alone</th>
                      <th className={`text-right ${th}`}>Sharpe with / alone</th><th className={`text-right ${th}`}>Guard on</th>
                      <th className={`text-right ${th}`}>vs alone, bp/day [95% CI]</th>
                    </tr></thead>
                    <tbody>{Object.entries(gr.walk_forward).map(([base, w]) => (
                      <tr key={base} className="border-b border-line/60">
                        <td className="py-1 px-2">{base} <span className="text-muted">from {w.oos_start}</span></td>
                        <td className="py-1 px-2 text-right num">{fmt(w.walk_forward["total_return_%"], 2)}%</td>
                        <td className="py-1 px-2 text-right num">{fmt(w.base_alone["total_return_%"], 2)}%</td>
                        <td className="py-1 px-2 text-right num">{fmt(w.walk_forward.sharpe, 2)} / {fmt(w.base_alone.sharpe, 2)}</td>
                        <td className="py-1 px-2 text-right num">{w.windows_guard_on} of {w.windows} windows</td>
                        <DiffCell d={w.vs_base} />
                      </tr>))}</tbody>
                  </table>
                </div>
              )}
            </section>
          ))}

          <section className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            <div>
              <span className="panel-title">Earnings dates found from news vs published</span>
              <table className="w-full text-[12px] border-collapse mt-1">
                <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
                  <th className={`text-left ${th}`}>Symbol</th><th className={`text-right ${th}`}>Found exactly</th>
                  <th className={`text-left ${th}`}>Off by a day or more</th><th className={`text-left ${th}`}>Missed</th><th className={`text-left ${th}`}>Wrong</th>
                </tr></thead>
                <tbody>{Object.entries(d.earnings_check).map(([s, c]) => (
                  <tr key={s} className="border-b border-line/60">
                    <td className="py-1 px-2 font-semibold">{s}</td>
                    <td className="py-1 px-2 text-right num">{c.exact} of {c.published}</td>
                    <td className="py-1 px-2 num">{c.off_by_days.join(", ") || "–"}</td>
                    <td className="py-1 px-2 num">{c.missed.join(", ") || "–"}</td>
                    <td className="py-1 px-2 num">{c.false.join(", ") || "–"}</td>
                  </tr>))}</tbody>
              </table>
              <p className="text-[11px] text-muted mt-1">The backtest uses the published dates. Dates after the published list come from the news only:{" "}
                {Object.entries(d.earnings_check).map(([s, c]) => `${s} ${c.news_only.join(", ") || "none"}`).join(" · ")}.</p>
            </div>
            <div>
              <span className="panel-title">How much of the time trading is blocked</span>
              <table className="w-full text-[12px] border-collapse mt-1">
                <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
                  <th className={`text-left ${th}`}>Symbol</th><th className={`text-right ${th}`}>Stand aside, mat. ≥ 3</th>
                  <th className={`text-right ${th}`}>Stand aside, mat. ≥ 4</th><th className={`text-right ${th}`}>Earnings blackout</th>
                </tr></thead>
                <tbody>{Object.values(d.blocked).flatMap((g) => Object.keys(g[">=3"]).map((s) => (
                  <tr key={s} className="border-b border-line/60">
                    <td className="py-1 px-2 font-semibold">{s}</td>
                    <td className="py-1 px-2 text-right num">{fmt(g[">=3"][s]["stand_aside_%"], 1)}%</td>
                    <td className="py-1 px-2 text-right num">{fmt(g[">=4"][s]["stand_aside_%"], 1)}%</td>
                    <td className="py-1 px-2 text-right num">{fmt(g[">=3"][s]["blackout_%"], 1)}%</td>
                  </tr>)))}</tbody>
              </table>
              <p className="text-[11px] text-muted mt-1">Share of 5-minute bars. The keyword rules rate much general market news as material for SPY, so the ETFs use ≥ 4.</p>
            </div>
          </section>
        </div>
      )}
    </Panel>
  );
}
