import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { C } from "../lib/chart";
import { fmt, pnl, signed } from "../lib/format";
import { Plot } from "./Plot";
import { Panel, Spinner } from "./ui";

interface Curve { "total_%": number; "max_drawdown_%": number; "worst_week_%": number }
interface Vs { "mean_%": number | null; ci95: [number | null, number | null]; "weeks_ahead_%": number | null; clearly_better: boolean; clearly_worse: boolean }
interface Sym {
  "with_job_%": number; changes: number; round_trips: number; "picks_%": number; "matched_%": number; "buy_hold_%": number;
  vs_matched: Vs; longest_negative_run: number; job_now: string;
}
interface Summ {
  from: string; to: string; weeks: number; symbol_weeks: number; with_job: number; no_job: number; "with_job_%": number;
  changes: number; changes_per_month: number; drops: number; round_trips: number; "cost_paid_%": number; "avg_invested_%": number;
  account: Record<"picks" | "buy_hold" | "matched" | "matched_day" | "no_trading", Curve>;
  vs: Record<"no_trading" | "buy_hold" | "matched" | "matched_day", Vs>;
  negative_score_jobs: { longest_run_weeks: number; negative_job_weeks: number };
  score_predicts: { mean_rank_corr: number | null; ci95: [number | null, number | null] };
  equity: [string, number][]; equity_refs: Record<string, [string, number][]>;
  per_symbol: Record<string, Sym>; etfs?: { symbols: string[]; "with_job_%": number; vs_matched: Vs; account: Record<string, Curve> };
  stocks?: { symbols: string[]; "with_job_%": number; vs_matched: Vs; account: Record<string, Curve> };
  criteria: { a: boolean | null; b: boolean | null };
}
interface Res {
  generated: string; split: string; versions: Record<string, { label: string; switches: Record<string, unknown> }>;
  results: Record<string, Record<string, Record<string, Summ>>>;
  passes: Record<string, Record<string, { pass: boolean; complete: boolean; periods: Record<string, { a: boolean; b: boolean }> }>>;
  criterion_c: { checked: number; equal: boolean; different?: number };
  tests: { comparisons: number; clearly_better: number; expected_by_luck: number };
  summary: string[]; design_frozen?: { frozen_at: string };
}

const th = "py-1 px-2 border-b border-line";
const pct = (v: number | null | undefined, d = 2) => (v == null ? "–" : `${signed(v, d)}%`);
const vsText = (v: Vs | undefined) => (v && v["mean_%"] != null
  ? `${signed(v["mean_%"], 3)} [${signed(v.ci95[0] ?? 0, 3)}, ${signed(v.ci95[1] ?? 0, 3)}]${v.clearly_better ? " ▲" : v.clearly_worse ? " ▼" : ""}` : "–");
const ok = (b: boolean | null | undefined) => (b == null ? <span className="text-muted">–</span> : b ? <span className="text-up">yes</span> : <span className="text-down">no</span>);
const COLORS = [C.inkFaint, C.navy, C.gold, C.series[2], C.series[4]];

/** Recalibrate page: the switch study (data/recal_fix_study.json): versions side by side, pass/fail, equity. */
export function RecalFixStudy() {
  const q = useQuery({ queryKey: ["recal-fix-study"], queryFn: () => api<Res | null>("/recal-fix-study"), staleTime: 60_000 });
  const [cost, setCost] = useState("base");
  const [period, setPeriod] = useState<string | null>(null);
  const [sym, setSym] = useState("options12");
  const d = q.data;
  return (
    <Panel title="Switch study: would options 1, 2 and the edge filter make recalibration honest? (study only, no switch is on)">
      {q.isLoading && <Spinner label="Loading…" />}
      {!q.isLoading && !d && <p className="text-[12px] text-muted">No study yet: python -m autobot.recal_fix_study --phase design, then --report.</p>}
      {d && <Body d={d} cost={cost} setCost={setCost} period={period} setPeriod={setPeriod} sym={sym} setSym={setSym} />}
    </Panel>
  );
}

function Body({ d, cost, setCost, period, setPeriod, sym, setSym }: {
  d: Res; cost: string; setCost: (c: string) => void; period: string | null; setPeriod: (p: string) => void; sym: string; setSym: (v: string) => void;
}) {
  const byV = d.results[cost] ?? {};
  const versions = Object.keys(d.versions).filter((v) => byV[v]);
  const phases = ["design", "confirm"].filter((p) => versions.some((v) => byV[v]?.[p]));
  const ph = period && phases.includes(period) ? period : phases[phases.length - 1];
  const ref = byV[versions[0]]?.[ph];
  const head = ["Version", "Passes", "a", "b", "Result", "Deepest dip", "Worst week", "vs time-matched B&H (%/week, 95%)", "Weeks ahead",
    "vs B&H", "vs matched (day only)", "Job in", "Changes / month", "Round trips", "Costs", "Score predicts"];
  const symRows = byV[sym]?.[ph]?.per_symbol ?? {};
  return (
    <div className="flex flex-col gap-3">
      <div className={`note-box rounded-lg border px-3 py-2 text-[12.5px] ${Object.values(d.passes.base ?? {}).some((p) => p.pass) ? "border-up" : "border-line bg-panel-2"}`}>
        <ul className="list-disc pl-5 flex flex-col gap-0.5">{d.summary.map((l, i) => <li key={i}>{l}</li>)}</ul>
      </div>
      <div className="flex flex-wrap gap-1.5 items-center text-[12px]">
        {phases.map((p) => <button key={p} className={`btn ${p === ph ? "btn-primary" : ""}`} onClick={() => setPeriod(p)}>
          {p === "design" ? "Design period" : "Confirmation period"}</button>)}
        <select className="input" value={cost} onChange={(e) => setCost(e.target.value)} aria-label="Costs">
          <option value="base">costs: 2 bps per side</option><option value="etf1bp">costs: 1 bp for SPY and QQQ</option>
        </select>
        {ref && <span className="text-muted">{ref.from} to {ref.to} · {ref.weeks} weeks · split {d.split}
          {d.design_frozen ? ` · design frozen ${d.design_frozen.frozen_at.slice(0, 16).replace("T", " ")} UTC` : ""}</span>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px] border-collapse">
          <thead><tr className="text-muted text-[10px] uppercase tracking-wider">{head.map((h, i) => <th key={h} className={`${i ? "text-right" : "text-left"} ${th}`}>{h}</th>)}</tr></thead>
          <tbody>
            {versions.map((v) => {
              const s = byV[v][ph];
              const p = d.passes[cost]?.[v];
              if (!s) return null;
              return (
                <tr key={v} className="border-b border-line/60">
                  <td className="py-1 px-2 whitespace-nowrap font-semibold" title={JSON.stringify(d.versions[v].switches)}>{d.versions[v].label}</td>
                  <td className="py-1 px-2 text-right">{p?.complete ? (p.pass ? <span className="text-up font-semibold">PASS</span> : <span className="text-down font-semibold">FAIL</span>) : <span className="text-muted">pending</span>}</td>
                  <td className="py-1 px-2 text-right">{ok(s.criteria.a)}</td>
                  <td className="py-1 px-2 text-right" title={`longest run ${s.negative_score_jobs.longest_run_weeks} weeks`}>{ok(s.criteria.b)}</td>
                  <td className={`py-1 px-2 text-right num ${pnl(s.account.picks["total_%"])}`}>{pct(s.account.picks["total_%"])}</td>
                  <td className="py-1 px-2 text-right num">{pct(s.account.picks["max_drawdown_%"])}</td>
                  <td className="py-1 px-2 text-right num">{pct(s.account.picks["worst_week_%"])}</td>
                  <td className="py-1 px-2 text-right num whitespace-nowrap">{vsText(s.vs.matched)}</td>
                  <td className="py-1 px-2 text-right num">{fmt(s.vs.matched["weeks_ahead_%"], 0)}%</td>
                  <td className="py-1 px-2 text-right num whitespace-nowrap">{vsText(s.vs.buy_hold)}</td>
                  <td className="py-1 px-2 text-right num whitespace-nowrap">{vsText(s.vs.matched_day)}</td>
                  <td className="py-1 px-2 text-right num">{fmt(s["with_job_%"], 0)}%</td>
                  <td className="py-1 px-2 text-right num">{fmt(s.changes_per_month, 1)}</td>
                  <td className="py-1 px-2 text-right num">{fmt(s.round_trips, 0)}</td>
                  <td className="py-1 px-2 text-right num">{fmt(s["cost_paid_%"], 2)}%</td>
                  <td className="py-1 px-2 text-right num whitespace-nowrap">{s.score_predicts.mean_rank_corr == null ? "–"
                    : `${fmt(s.score_predicts.mean_rank_corr, 3)} [${fmt(s.score_predicts.ci95[0], 3)}, ${fmt(s.score_predicts.ci95[1], 3)}]`}</td>
                </tr>);
            })}
            {ref && (["no_trading", "buy_hold", "matched", "matched_day"] as const).map((k) => (
              <tr key={k} className="border-b border-line/60 text-muted">
                <td className="py-1 px-2 whitespace-nowrap">{{ no_trading: "No trading (cash)", buy_hold: "Buy and hold", matched: "Time-matched B&H (today's jobs)",
                  matched_day: "Time-matched B&H, daytime only" }[k]}</td>
                <td colSpan={3} />
                <td className="py-1 px-2 text-right num">{pct(ref.account[k]["total_%"])}</td>
                <td className="py-1 px-2 text-right num">{pct(ref.account[k]["max_drawdown_%"])}</td>
                <td className="py-1 px-2 text-right num">{pct(ref.account[k]["worst_week_%"])}</td>
                <td colSpan={9} />
              </tr>))}
          </tbody>
        </table>
      </div>
      <p className="text-[11.5px] text-muted">
        Account: each symbol gets {"10"}% of the account while it has a job, the rest is cash. Pass = (a) the weekly difference to time-matched buy and hold is not clearly negative
        and (b) no job kept more than 2 weeks in a row with a negative score, in BOTH periods, and (c) with all switches off the replay equals today's
        ({d.criterion_c.equal ? `yes, ${d.criterion_c.checked} symbol-weeks` : "NO"}). A version can pass (a) by holding cash; compare the "Job in" and "vs B&H" columns.
        ▲/▼ = clearly better/worse. {d.tests.comparisons} comparisons; about {fmt(d.tests.expected_by_luck, 0)} would look clearly better by luck, {d.tests.clearly_better} did.
      </p>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Plot height={300} data={[
          ...versions.map((v, i) => {
            const pts = [...(byV[v].design?.equity ?? []), ...((byV[v].confirm?.equity ?? []).map(([f, e]) => [f, e * (byV[v].design?.equity.at(-1)?.[1] ?? 1)] as [string, number]))];
            return { x: pts.map((p) => p[0]), y: pts.map((p) => (p[1] - 1) * 100), name: d.versions[v].label, type: "scatter", mode: "lines",
              line: { color: COLORS[i % COLORS.length], width: v === "today" ? 1.4 : 2 } };
          }),
          (() => {
            const des = byV[versions[0]]?.design?.equity_refs.buy_hold ?? [], con = byV[versions[0]]?.confirm?.equity_refs.buy_hold ?? [];
            const pts = [...des, ...con.map(([f, e]) => [f, e * (des.at(-1)?.[1] ?? 1)] as [string, number])];
            return { x: pts.map((p) => p[0]), y: pts.map((p) => (p[1] - 1) * 100), name: "Buy and hold", type: "scatter", mode: "lines", line: { color: C.grey, dash: "dot", width: 1.5 } };
          })(),
        ]} layout={{ yaxis: { ticksuffix: "%" }, margin: { l: 48, r: 10, t: 8, b: 36 }, legend: { orientation: "h", y: -0.25 },
          shapes: [{ type: "line", x0: d.split, x1: d.split, yref: "paper", y0: 0, y1: 1, line: { color: C.lineStrong, dash: "dash", width: 1 } }],
          annotations: [{ x: d.split, y: 1, yref: "paper", text: "confirmation →", showarrow: false, xanchor: "left", font: { size: 10, color: C.inkSoft } }] }} />
        <div className="overflow-x-auto">
          <div className="flex items-center gap-1.5 text-[12px] mb-1">
            <span className="text-muted">Per symbol:</span>
            <select className="input" value={sym} onChange={(e) => setSym(e.target.value)} aria-label="Version">
              {versions.map((v) => <option key={v} value={v}>{d.versions[v].label}</option>)}
            </select>
          </div>
          <table className="w-full text-[12px] border-collapse">
            <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
              {["Symbol", "Job in", "Result", "Matched B&H", "B&H", "vs matched (%/wk)", "Neg. run", "Round trips", "Job at the end"].map((h, i) =>
                <th key={h} className={`${i && i < 8 ? "text-right" : "text-left"} ${th}`}>{h}</th>)}
            </tr></thead>
            <tbody>
              {Object.entries(symRows).map(([s, x]) => (
                <tr key={s} className="border-b border-line/60">
                  <td className="py-1 px-2 num font-semibold">{s}</td>
                  <td className="py-1 px-2 text-right num">{fmt(x["with_job_%"], 0)}%</td>
                  <td className={`py-1 px-2 text-right num ${pnl(x["picks_%"])}`}>{pct(x["picks_%"], 1)}</td>
                  <td className="py-1 px-2 text-right num">{pct(x["matched_%"], 1)}</td>
                  <td className="py-1 px-2 text-right num">{pct(x["buy_hold_%"], 1)}</td>
                  <td className="py-1 px-2 text-right num whitespace-nowrap">{vsText(x.vs_matched)}</td>
                  <td className={`py-1 px-2 text-right num ${x.longest_negative_run > 2 ? "text-down" : ""}`}>{x.longest_negative_run}</td>
                  <td className="py-1 px-2 text-right num">{x.round_trips}</td>
                  <td className="py-1 px-2 text-muted max-w-[22ch] truncate" title={x.job_now}>{x.job_now}</td>
                </tr>))}
              {(["etfs", "stocks"] as const).map((g) => { const x = byV[sym]?.[ph]?.[g]; return x && (
                <tr key={g} className="border-t-2 border-line font-semibold">
                  <td className="py-1 px-2">{g === "etfs" ? "ETFs" : "Single stocks"}</td>
                  <td className="py-1 px-2 text-right num">{fmt(x["with_job_%"], 0)}%</td>
                  <td className={`py-1 px-2 text-right num ${pnl(x.account.picks["total_%"])}`}>{pct(x.account.picks["total_%"], 1)}</td>
                  <td className="py-1 px-2 text-right num">{pct(x.account.matched["total_%"], 1)}</td>
                  <td className="py-1 px-2 text-right num">{pct(x.account.buy_hold["total_%"], 1)}</td>
                  <td className="py-1 px-2 text-right num whitespace-nowrap">{vsText(x.vs_matched)}</td>
                  <td colSpan={3} className="py-1 px-2 text-muted text-[11px]">{x.symbols.join(", ")} (account share)</td>
                </tr>); })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
