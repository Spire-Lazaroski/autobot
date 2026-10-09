/**
 * Live page overview (2026-10-09 layout): account split bar, three cards (core, rotation sleeve,
 * trading jobs), today's account-value chart, positions split into long-term and trading, and the
 * bot log with tracebacks folded. Display only: nothing here trades.
 */
import { useState } from "react";
import { C } from "../lib/chart";
import { brand, chart } from "../theme";
import { fmt, nyTime, pnl, signed } from "../lib/format";
import type { Row } from "../lib/api";
import { Plot } from "./Plot";
import { SortTable } from "./SortTable";
import { Panel } from "./ui";

export interface Split {
  core_usd: number; rotation_usd: number; jobs_usd: number; long_term_pl_today: number; jobs_pl_today: number; equity: number;
  "targets_%": { core: number; rotation: number };
}
export interface EquityRow { t: number; equity: number; long_term_usd: number; long_term_pl_today: number; jobs_pl_today: number }
export interface LogEntry { line: string; details: string[]; summary?: string }
export interface JobsVsHold { from?: string | null; to?: string | null; job_days?: number; strategy_usd?: number; hold_usd?: number; error?: string }
interface CoreLike {
  enabled: boolean; symbol: string; allocation_pct: number; rebalance_band_pct: number; qty: number; value?: number; "weight_%"?: number | null;
  vol_target?: { target_pct: number; vol_pct: number; normal_pct: number; ratio: number; as_of: string; reason: string };
  last_action?: { time: string; reason: string };
}
interface RotationLike {
  enabled: boolean; allocation_pct: number; top_n: number; held: Record<string, number>;
  target?: { as_of: string | null; "invested_%": number; weights: Record<string, number>; reasons: Record<string, string>;
    ranking: { rank: number; symbol: string; "momentum_%": number; above_trend: boolean; "volatility_%": number }[] };
  last_actions?: { symbol: string; qty: number }[];
}

const pct = (v: number, eq: number) => (eq > 0 ? (v / eq) * 100 : 0);
/** Unix seconds as a New York wall-clock string, so chart axes show New York time like the rest of the page. */
const nyStamp = (t: number) => new Date(t * 1000).toLocaleString("sv-SE", { timeZone: "America/New_York" });
const GREYS = { core: chart.ink, rotInv: chart.soft, rotCash: chart.faint, jobs: brand.amber };   // theme.ts only

/** Where the money is now, against the targets. */
export function SplitBar({ split, cash }: { split?: Split; cash?: number }) {
  if (!split) return null;
  const eq = split.equity;
  const core = pct(split.core_usd, eq), rot = pct(split.rotation_usd, eq), jobs = pct(split.jobs_usd, eq);
  const rotCash = Math.max(0, split["targets_%"].rotation - rot);
  const free = Math.max(0, 100 - core - rot - rotCash - jobs);
  const parts = [
    { w: core, c: GREYS.core, label: `Core ${fmt(core, 1)}% (target ${fmt(split["targets_%"].core, 1)}%)` },
    { w: rot, c: GREYS.rotInv, label: `Rotation invested ${fmt(rot, 1)}%` },
    { w: rotCash, c: GREYS.rotCash, label: `Rotation cash ${fmt(rotCash, 1)}% (of ${fmt(split["targets_%"].rotation, 0)}%)` },
    { w: jobs, c: GREYS.jobs, label: `Jobs ${fmt(jobs, 1)}%` },
  ];
  return (
    <Panel title="Account split · now vs target">
      <div className="flex h-3 rounded overflow-hidden bg-sunk">
        {parts.map((p, i) => <div key={i} style={{ width: `${p.w}%`, background: p.c }} title={p.label} />)}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1.5 text-[11.5px] text-muted">
        {parts.map((p, i) => <span key={i}><span style={{ color: p.c }}>■</span> {p.label}</span>)}
        <span>Free cash {fmt(free, 1)}%{cash !== undefined ? ` ($${fmt(cash, 0)})` : ""}</span>
      </div>
    </Panel>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return <Panel title={title}><div className="text-[12px] flex flex-col gap-0.5">{children}</div></Panel>;
}

export function CoreCard({ core }: { core?: CoreLike }) {
  if (!core) return null;
  if (!core.enabled) return <Card title="Core holding"><span className="text-muted">Off (live.core.enabled in config.yaml).</span></Card>;
  const tgt = core.vol_target?.target_pct ?? core.allocation_pct;
  const v = core.vol_target;
  return (
    <Card title={`Core · ${core.symbol}`}>
      <span className="num">{fmt(core.qty, 0)} shares · {core.value !== undefined ? `$${fmt(core.value, 0)}` : "–"}</span>
      <span className="num">{core["weight_%"] !== undefined && core["weight_%"] !== null ? `${fmt(core["weight_%"], 1)}%` : "–"}
        <span className="text-muted"> · target {fmt(tgt, 1)}% · band {fmt(tgt - core.rebalance_band_pct, 1)}–{fmt(tgt + core.rebalance_band_pct, 1)}%</span></span>
      {v && <span className="text-muted">{v.ratio < 0.9 ? "Calmer" : v.ratio > 1.1 ? "Jumpier" : "About as jumpy"} than normal: volatility {fmt(v.vol_pct, 1)}% vs {fmt(v.normal_pct, 1)}%</span>}
      <span className="text-muted">{core.last_action ? `Last: ${core.last_action.reason}` : "No core order since the bot started"}</span>
    </Card>
  );
}

export function RotationCard({ rot }: { rot?: RotationLike }) {
  const [open, setOpen] = useState(false);
  if (!rot || !rot.enabled) return <Card title="Rotation sleeve"><span className="text-muted">Off (live.rotation.enabled in config.yaml).</span></Card>;
  const t = rot.target;
  const below = t ? t.ranking.filter((r) => r.rank <= rot.top_n && !r.above_trend).map((r) => r.symbol) : [];
  return (
    <Card title={`Rotation · ${rot.allocation_pct}% sleeve, top ${rot.top_n} monthly`}>
      {!t ? <span className="text-muted">Decides at the first tick after the next open.</span> : (<>
        <span className="num">{Object.keys(t.weights).length ? Object.entries(t.weights).map(([k, v]) => `${k} ${fmt(v, 1)}%`).join(" · ") : "All in cash"}</span>
        <span className="text-muted">{below.length ? `${below.join(", ")} in the top ${rot.top_n} but below trend: cash` : `Invested ${fmt(t["invested_%"], 1)}% of the account`}</span>
        <span className="text-muted">As of {t.as_of} month-end closes ·{" "}
          <button className="underline" onClick={() => setOpen(!open)}>{open ? "hide ranking" : "show ranking"}</button></span>
        {open && (
          <table className="w-full text-[11.5px] mt-1 table-fixed">
            <thead><tr className="text-muted"><th className="text-left w-[10%]">#</th><th className="text-left">Fund</th><th className="text-right">12 m</th>
              <th className="text-center">Trend</th><th className="text-right">Vol</th><th className="text-right">Held</th></tr></thead>
            <tbody>{t.ranking.map((r) => (
              <tr key={r.symbol} className={t.weights[r.symbol] ? "font-semibold" : "text-muted"}>
                <td>{r.rank}</td><td>{r.symbol}</td><td className="num text-right">{fmt(r["momentum_%"], 1)}%</td>
                <td className="text-center">{r.above_trend ? "above" : "below"}</td><td className="num text-right">{fmt(r["volatility_%"], 0)}%</td>
                <td className="num text-right">{rot.held[r.symbol] ? fmt(rot.held[r.symbol], 0) : "–"}</td></tr>))}</tbody>
          </table>)}
      </>)}
    </Card>
  );
}

export function JobsCard({ split, jobs, positions, jvh, rules }: { split?: Split; jobs: number; positions: number; jvh?: JobsVsHold | null; rules?: string }) {
  return (
    <Card title="Trading jobs · live check">
      <span className="num">{jobs} jobs · {positions} position{positions === 1 ? "" : "s"} open</span>
      <span className="num">Today <span className={pnl(split?.jobs_pl_today)}>{split ? signed(split.jobs_pl_today) : "–"}</span></span>
      <span className="text-muted">{jvh && !jvh.error && jvh.job_days
        ? <>Since {jvh.from}: jobs <span className={pnl(jvh.strategy_usd)}>{signed(jvh.strategy_usd ?? 0)}</span> vs holding the same <span className={pnl(jvh.hold_usd)}>{signed(jvh.hold_usd ?? 0)}</span></>
        : jvh?.error ? "Comparison with holding unavailable" : "Comparing with holding the same symbols…"}</span>
      {rules && <span className="text-muted">{rules}</span>}
    </Card>
  );
}

/** Today's account value, with the long-term holdings' value and fills as markers. */
export function EquityChart({ rows, fills }: { rows: EquityRow[]; fills: { t: number; side: string }[] }) {
  if (rows.length < 2) return <Panel title="Account value today"><p className="text-muted text-[12px]">Builds up while the market is open (one point per bar).</p></Panel>;
  const x = rows.map((r) => nyStamp(r.t));
  const near = (t: number) => rows.reduce((b, r) => (Math.abs(r.t - t) < Math.abs(b.t - t) ? r : b), rows[0]);
  const mk = (side: string) => {
    const f = fills.filter((q) => q.side === side && q.t >= rows[0].t && q.t <= rows[rows.length - 1].t);
    return { type: "scatter", mode: "markers", name: side, x: f.map((q) => nyStamp(q.t)), y: f.map((q) => near(q.t).equity),
      marker: { color: side === "buy" ? C.profit : C.loss, size: 6 }, hovertemplate: `${side}<extra></extra>` };
  };
  return (
    <Panel title="Account value today (New York time) · equity, long-term holdings, fills">
      <Plot height={220} layout={{ margin: { l: 56, r: 56, t: 8, b: 28 }, xaxis: { type: "date" },
        yaxis: { title: { text: "equity $" } }, yaxis2: { overlaying: "y", side: "right", showgrid: false, title: { text: "long-term $" } } }}
        data={[
          { type: "scatter", mode: "lines", name: "equity", x, y: rows.map((r) => r.equity), line: { color: C.navy, width: 2 } },
          { type: "scatter", mode: "lines", name: "long-term holdings", x, y: rows.map((r) => r.long_term_usd), yaxis: "y2",
            line: { color: C.amber, width: 1.2, dash: "dash" } },
          mk("buy"), mk("sell"),
        ]} />
    </Panel>
  );
}

const POS_COLS = [
  { key: "symbol", label: "Symbol", render: (r: Row) => <span className="num font-semibold">{r.symbol}</span> },
  { key: "qty", label: "Qty", digits: 0 },
  { key: "market_value", label: "Value", digits: 0 },
  { key: "unrealized_intraday_pl", label: "Today $", tone: true },
  { key: "unrealized_plpc", label: "P&L %", tone: true },
];

/** Positions in two groups: long-term (core and sleeve) and trading jobs. */
export function PositionsSplit({ positions, longTerm, extra }: { positions: Row[]; longTerm: Set<string>; extra?: (r: Row) => string | null }) {
  const lt = positions.filter((p) => longTerm.has(p.symbol));
  const jobs = positions.filter((p) => !longTerm.has(p.symbol));
  const jobCols = extra ? [...POS_COLS, { key: "note", label: "Note", align: "left" as const, render: (r: Row) => <span className="text-muted whitespace-nowrap">{extra(r) ?? "–"}</span> }] : POS_COLS;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
      <Panel title="Long-term positions · core and rotation">
        {lt.length ? <SortTable rows={lt} rowKey={(r) => r.symbol} initialSort={{ key: "market_value", desc: true }} cols={POS_COLS} />
          : <p className="text-muted text-[12px]">None.</p>}
      </Panel>
      <Panel title="Trading job positions · closed by 15:55">
        {jobs.length ? <SortTable rows={jobs} rowKey={(r) => r.symbol} initialSort={{ key: "market_value", desc: true }} cols={jobCols} />
          : <p className="text-muted text-[12px]">No open job positions.</p>}
      </Panel>
    </div>
  );
}

/** Bot log: one line per entry; a traceback folds into its summary, details on click. */
export function LogPanel({ entries }: { entries: LogEntry[] }) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const shown = entries.slice(-40);
  return (
    <Panel title="Bot log">
      <div className="num text-[10.5px] leading-snug max-h-60 overflow-y-auto text-muted">
        {shown.map((e, i) => {
          const tone = /ERROR|CRITICAL/.test(e.line) ? "text-down" : /WARNING|CONFIG|FLATTEN/.test(e.line) ? "text-warn" : /ORDER|ROTATION|CORE/.test(e.line) ? "text-fg" : "";
          return (
            <div key={i} className={tone}>
              <span className="whitespace-pre-wrap break-words">{e.line}</span>
              {e.details.length > 0 && (<>
                {" "}<button className="underline text-muted" onClick={() => setOpenIdx(openIdx === i ? null : i)}>
                  {openIdx === i ? "hide details" : `details (${e.summary ?? "traceback"})`}</button>
                {openIdx === i && <pre className="whitespace-pre-wrap text-muted pl-3">{e.details.join("\n")}</pre>}
              </>)}
            </div>);
        })}
      </div>
    </Panel>
  );
}

export const tsNy = (s: string | number) => nyTime(typeof s === "number" ? s : Date.parse(s) / 1000);
