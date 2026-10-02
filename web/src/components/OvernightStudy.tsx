import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, useJob, type Job } from "../lib/api";
import { C } from "../lib/chart";
import { fmt, pnl, signed } from "../lib/format";
import { Plot } from "./Plot";
import { Panel, Spinner } from "./ui";

interface Kind { nights: number; "helped_%": number | null; "avg_%": number | null; "worst_%": number | null; worst_date: string | null; "sum_%": number }
interface S {
  "return_%": number; "return_before_costs_%"?: number; sharpe: number; "max_drawdown_%": number; round_trips?: number; "cost_%"?: number;
  "intraday_%"?: number; "overnight_%"?: number; "weekend_%"?: number; nights?: number; "helped_%"?: number | null;
  "worst_night_%"?: number | null; worst_night_date?: string | null; by_kind?: Record<string, Kind>;
}
interface Paired { days: number; mean_bps: number; ci95_bps: [number, number]; significant: boolean }
interface Pol { name: string; mode: string; flatten_min: number; require: string[]; total: S; per_symbol: Record<string, S> }
interface Night { symbol: string; date: string; next_date: string; kind: string; position: number; "gap_%": number; "result_%": number }
interface Worth { group: string; conditions: string[]; met: boolean; nights: number; "avg_%": number | null; "gained_%": number | null; "worst_%": number | null }
interface Block { condition: string; nights: number; blocked: number; "blocks_%": number | null; informative: boolean }
interface WF extends S {
  vs_flatten: Paired; choices: Record<string, number>; switches: { windows: number; changes: number; "changes_%": number };
  compare: Record<string, S>; per_symbol: Record<string, S & { vs_flatten: Paired; fixed_flatten: S; fixed_hold: S; buy_hold: S }>;
  from: string; to: string;
}
interface Period {
  from: string; to: string; days: number; policies: Pol[]; buy_hold: { total: S; per_symbol: Record<string, S> };
  test_days: (S & { name: string; vs_flatten: Paired | null })[]; test_days_buy_hold?: S; walk_forward: WF | null;
  worst_nights_by_kind: Record<string, Record<string, Night[]>>; worth_holding: Worth[]; worth_holding_by_symbol: Record<string, Worth[]>;
  blocking: { total: Block[]; per_symbol: Record<string, Block[]> };
  equity: Record<string, [string, number][]>; equity_test: Record<string, [string, number][]>;
}
interface Res {
  as_of: string; generated: string; timeframe: string; base_policy: string; calendar: string; source?: string;
  settings: { edge_mult: number; profit_x_pct: number; bad_news_materiality: number; bad_news_alt_materiality: number; bad_news_hours: number };
  symbols: { symbol: string; job: string; strategy: string }[]; policy_text: Record<string, string>; condition_text: Record<string, string>;
  periods: Record<string, Period>; cost_sensitivity: Record<string, { policies: (S & { name: string })[]; test_days: (S & { name: string; vs_flatten: Paired | null })[];
    walk_forward: { "return_%": number; vs_flatten: Paired } | null }>;
  summary: string[]; beats_flatten_oos: boolean;
}

const SHORT: Record<string, string> = {
  signal_on: "strategy still in", in_profit: "in profit", in_profit_x: "in profit > X%", trend_regime: "trending",
  not_friday: "not Friday", no_earnings: "no earnings next", no_bad_news: "no bad news (4+)", no_bad_news_m3: "no bad news (3+)",
};
const short = (p: { name: string; mode: string; flatten_min: number; require: string[] }, x: number) =>
  p.mode === "flatten" ? `Sell ${p.flatten_min} min before close` : p.mode === "hold" ? "Hold (strategy decides)"
    : `Hold if ${p.require.map((c) => SHORT[c].replace("X", String(x))).join(" + ")}`;
const th = "py-1 px-2 border-b border-line";
interface OosRow { key: string; label: string; title: string; net?: number; vs?: Paired; net1?: number; vs1?: Paired; bold?: boolean; muted?: boolean }
const pct = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "–" : `${signed(v, d)}%`);
const bps = (v: Paired | null | undefined) => (v ? `${signed(v.mean_bps, 1)} [${signed(v.ci95_bps[0], 1)}, ${signed(v.ci95_bps[1], 1)}]${v.significant ? " *" : ""}` : "–");

/** Lab: sell everything before the close (today's rule) vs holding overnight, and under which conditions. */
export function OvernightStudy() {
  const [days, setDays] = useState(90);
  const [long, setLong] = useState(true);
  const [source, setSource] = useState<"alpaca" | "demo">("alpaca");
  const [jobId, setJobId] = useState<string | null>(null);
  const [periodKey, setPeriodKey] = useState("long");
  const [sym, setSym] = useState("all");
  const [curve, setCurve] = useState<"period" | "test">("period");
  const [more, setMore] = useState(false);
  const saved = useQuery({ queryKey: ["overnight-study"], queryFn: () => api<Res | null>("/overnight-study"), staleTime: 60_000 });
  const job = useJob<Res>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");
  const d = (job.data?.status === "done" ? job.data.result : null) ?? saved.data ?? null;
  const run = async () => setJobId((await api<Job>("/overnight-study", { days, long, source })).id);

  return (
    <Panel title="Overnight: sell before the close or hold? (the live jobs)" right={
      <div className="flex flex-wrap gap-1.5 items-center text-[12px]">
        <label className="flex items-center gap-1">days <input className="input w-16" type="number" min={30} max={365} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
        <label className="flex items-center gap-1"><input type="checkbox" checked={long} onChange={(e) => setLong(e.target.checked)} /> 2 years</label>
        <select className="input" value={source} onChange={(e) => setSource(e.target.value as "alpaca" | "demo")} aria-label="Data"><option value="alpaca">Alpaca data</option><option value="demo">demo data</option></select>
        <button className="btn btn-primary" disabled={!!busy} onClick={run}>{busy ? <Spinner /> : "▶"} Run study</button>
      </div>}>
      <p className="text-[12px] text-muted mb-2">
        Each enabled stock job's strategy replayed with different end-of-day rules: sell everything N minutes before the close (today's rule), keep whatever
        the strategy holds, or keep it only when chosen conditions hold. A held position earns the gap to the next open; a signal on the last bar is filled at
        the next open, like the bot. Fills at the open and in the last bar cost {d ? d.settings.edge_mult : 2}x the usual. Walk-forward: the rule is picked on
        earlier days and used on the next ones, so only those test days are an honest result.
      </p>
      {busy && <p className="text-[12px] text-muted">{job.data?.message}…</p>}
      {job.data?.status === "error" && <p className="text-[12px] text-down">{job.data.error}</p>}
      {!d && !busy && <p className="text-[12px] text-muted">No study yet: press Run study (about a minute and a half on 2 years).</p>}
      {d && <Body d={d} periodKey={d.periods[periodKey] ? periodKey : Object.keys(d.periods)[0]} setPeriodKey={setPeriodKey}
        sym={sym} setSym={setSym} curve={curve} setCurve={setCurve} more={more} setMore={setMore} />}
    </Panel>
  );
}

function Body({ d, periodKey, setPeriodKey, sym, setSym, curve, setCurve, more, setMore }: {
  d: Res; periodKey: string; setPeriodKey: (k: string) => void; sym: string; setSym: (s: string) => void;
  curve: "period" | "test"; setCurve: (c: "period" | "test") => void; more: boolean; setMore: (b: boolean) => void;
}) {
  const per = d.periods[periodKey];
  const x = d.settings.profit_x_pct;
  const all = sym === "all";
  const test = Object.fromEntries(per.test_days.map((t) => [t.name, t]));
  const wf = per.walk_forward;
  const symbols = d.symbols.map((s) => s.symbol);
  const rows: { key: string; label: string; title: string; s: S | undefined; bold?: boolean; muted?: boolean }[] = [
    { key: "buy_hold", label: "Buy and hold (benchmark)", title: "Buy once, never sell; no costs", s: all ? per.buy_hold.total : per.buy_hold.per_symbol[sym],
      muted: true },
    ...per.policies.map((p) => ({
      key: p.name, label: short(p, x), title: d.policy_text[p.name], s: all ? p.total : p.per_symbol[sym],
      bold: p.name === d.base_policy,
    })),
  ];
  const head = ["Rule", "Net", "Before costs", "Sharpe", "Deepest dip", "Round trips", "Costs", "Intraday", "Overnight", "Weekend",
    "Nights held", "Gained", "Worst night"];
  const oneTest = Object.fromEntries((d.cost_sensitivity[periodKey]?.test_days ?? []).map((t) => [t.name, t]));
  const oos: OosRow[] = wf ? [
    { key: "walk_forward", label: "Walk-forward pick", title: "In each window the rule that scored best on the earlier (train) days, used on the next (test) days",
      net: all ? wf["return_%"] : wf.per_symbol[sym]?.["return_%"], vs: all ? wf.vs_flatten : wf.per_symbol[sym]?.vs_flatten,
      net1: all ? d.cost_sensitivity[periodKey]?.walk_forward?.["return_%"] : undefined, vs1: all ? d.cost_sensitivity[periodKey]?.walk_forward?.vs_flatten : undefined, bold: true },
    { key: "buy_hold", label: "Buy and hold (benchmark)", title: "Buy once, never sell; no costs",
      net: all ? per.test_days_buy_hold?.["return_%"] : wf.per_symbol[sym]?.buy_hold["return_%"], vs: undefined, net1: undefined, vs1: undefined, muted: true },
    ...[...per.policies].map((p) => ({
      key: p.name, label: short(p, x), title: d.policy_text[p.name],
      net: all ? test[p.name]?.["return_%"] : p.name === d.base_policy ? wf.per_symbol[sym]?.fixed_flatten["return_%"]
        : p.mode === "hold" ? wf.per_symbol[sym]?.fixed_hold["return_%"] : undefined,
      vs: all ? test[p.name]?.vs_flatten ?? undefined : undefined, net1: all ? oneTest[p.name]?.["return_%"] : undefined,
      vs1: all ? oneTest[p.name]?.vs_flatten ?? undefined : undefined, bold: p.name === d.base_policy,
    })).sort((a, b) => (b.vs?.mean_bps ?? (b.bold ? 0 : -1e9)) - (a.vs?.mean_bps ?? (a.bold ? 0 : -1e9))),
  ] : [];
  const eq = curve === "period" ? per.equity : per.equity_test;
  const shown = [d.base_policy, "hold", "if_in_profit", "flatten_30", "walk_forward", "buy_hold"];
  const colour: Record<string, string> = { [d.base_policy]: C.navy, hold: C.gold, if_in_profit: C.series[2], flatten_30: C.series[3], walk_forward: C.series[4] };
  const pol = Object.fromEntries(per.policies.map((p) => [p.name, p]));
  const name = (k: string) => (k === "buy_hold" ? "Buy and hold" : k === "walk_forward" ? "Walk-forward pick" : pol[k] ? short(pol[k], x) : k);
  const worth = (all ? per.worth_holding : per.worth_holding_by_symbol[sym] ?? []).filter((w) => w.conditions.length === 1);
  const groups = [...new Set(worth.map((w) => w.group))];
  const worst = per.worst_nights_by_kind?.hold ?? {};
  return (
    <div className="flex flex-col gap-3">
      <div className={`note-box rounded-lg border px-3 py-2 text-[12.5px] ${d.beats_flatten_oos ? "border-up" : "border-line bg-panel-2"}`}>
        <b>{d.summary[0]}</b>
        <ul className="list-disc pl-5 mt-1 flex flex-col gap-0.5">
          {d.summary.slice(1, more ? undefined : 4).map((l, i) => <li key={i}>{l}</li>)}
        </ul>
        <button className="text-[11px] text-muted hover:text-fg mt-1" onClick={() => setMore(!more)}>{more ? "fewer findings ▴" : `all ${d.summary.length} findings ▾`}</button>
      </div>

      <div className="flex flex-wrap gap-1.5 items-center text-[12px]">
        {Object.keys(d.periods).map((k) => (
          <button key={k} className={`btn ${k === periodKey ? "btn-primary" : ""}`} onClick={() => setPeriodKey(k)}>
            {k === "long" ? `${d.periods[k].days} days` : `last ${d.periods[k].days} days`}</button>))}
        <select className="input" value={sym} onChange={(e) => setSym(e.target.value)} aria-label="Symbol">
          <option value="all">All symbols (equal weight)</option>
          {d.symbols.map((s) => <option key={s.symbol} value={s.symbol}>{s.symbol} · {s.job}</option>)}
        </select>
        <span className="text-muted">{per.from} to {per.to} · {d.timeframe} · calendar: {d.calendar} · as of {d.as_of}{d.source === "demo" ? " · DEMO DATA" : ""}</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12px] border-collapse">
          <thead><tr className="text-muted text-[10px] uppercase tracking-wider">{head.map((h, i) => <th key={h} className={`${i ? "text-right" : "text-left"} ${th}`}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((r) => r.s && (
              <tr key={r.key} className={`border-b border-line/60 ${r.bold ? "font-semibold" : ""} ${r.muted ? "text-muted" : ""}`}>
                <td className="py-1 px-2 whitespace-nowrap" title={r.title}>{r.label}</td>
                <td className={`py-1 px-2 text-right num ${pnl(r.s["return_%"])}`}>{pct(r.s["return_%"])}</td>
                <td className="py-1 px-2 text-right num">{pct(r.s["return_before_costs_%"])}</td>
                <td className="py-1 px-2 text-right num">{fmt(r.s.sharpe, 2)}</td>
                <td className="py-1 px-2 text-right num">{pct(r.s["max_drawdown_%"])}</td>
                <td className="py-1 px-2 text-right num">{fmt(r.s.round_trips, 0)}</td>
                <td className="py-1 px-2 text-right num">{r.s["cost_%"] === undefined ? "–" : `${fmt(r.s["cost_%"], 2)}%`}</td>
                <td className="py-1 px-2 text-right num">{pct(r.s["intraday_%"])}</td>
                <td className="py-1 px-2 text-right num">{pct(r.s["overnight_%"])}</td>
                <td className="py-1 px-2 text-right num">{pct(r.s["weekend_%"])}</td>
                <td className="py-1 px-2 text-right num">{fmt(r.s.nights, 0)}</td>
                <td className="py-1 px-2 text-right num">{r.s["helped_%"] == null ? "–" : `${fmt(r.s["helped_%"], 0)}%`}</td>
                <td className="py-1 px-2 text-right num" title={r.s.worst_night_date ?? ""}>{pct(r.s["worst_night_%"])}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      <p className="text-[11.5px] text-muted">
        Whole period, {per.from} to {per.to}: where the money came from. Returns in % of the account ({all ? "the symbols weighted equally" : sym});
        nights in % of the position. The rules were not tuned here, but picking the best row afterwards would be.
      </p>

      {wf && (
        <div className="overflow-x-auto">
          <p className="text-[12.5px] font-semibold mb-1">Out of sample: the walk-forward's test days ({wf.from} to {wf.to}); the pick changed in {fmt(wf.switches["changes_%"], 0)}% of the windows</p>
          <table className="w-full text-[12px] border-collapse">
            <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
              {["Rule", "Net", `vs selling ${d.base_policy.split("_")[1]} min before (bp/day, 95% range)`, "Net at 1x costs", "vs selling at 1x"].map((h, i) =>
                <th key={h} className={`${i ? "text-right" : "text-left"} ${th}`}>{h}</th>)}
            </tr></thead>
            <tbody>{oos.filter((r) => r.net !== undefined).map((r) => (
              <tr key={r.key} className={`border-b border-line/60 ${r.bold ? "font-semibold" : ""} ${r.muted ? "text-muted" : ""}`}>
                <td className="py-1 px-2 whitespace-nowrap" title={r.title}>{r.label}</td>
                <td className={`py-1 px-2 text-right num ${pnl(r.net)}`}>{pct(r.net)}</td>
                <td className="py-1 px-2 text-right num whitespace-nowrap">{r.key === d.base_policy ? "(the baseline)" : bps(r.vs)}</td>
                <td className="py-1 px-2 text-right num">{pct(r.net1)}</td>
                <td className="py-1 px-2 text-right num whitespace-nowrap">{r.key === d.base_policy ? "" : bps(r.vs1)}</td>
              </tr>))}</tbody>
          </table>
          <p className="text-[11.5px] text-muted mt-1">Sorted by the daily difference against selling before the close. * = beyond chance; the best of many rows
            is expected to look good by luck. 1x: fills at the open and in the last bar at the normal cost.{!all && " Per symbol only the pick, the baseline and hold are shown."}</p>
        </div>)}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div>
          <div className="flex gap-1.5 items-center text-[12px] mb-1">
            <span className="text-muted">Equity (all symbols):</span>
            <button className={`btn ${curve === "period" ? "btn-primary" : ""}`} onClick={() => setCurve("period")}>whole period</button>
            <button className={`btn ${curve === "test" ? "btn-primary" : ""}`} onClick={() => setCurve("test")}>test days</button>
          </div>
          <Plot height={300} data={Object.entries(eq).filter(([k]) => shown.includes(k)).map(([k, v]) => ({
            x: v.map((p) => p[0]), y: v.map((p) => (p[1] - 1) * 100), name: name(k), type: "scatter", mode: "lines",
            line: k === "buy_hold" ? { color: C.grey, width: 1.5, dash: "dot" } : { color: colour[k] ?? C.inkFaint, width: k === d.base_policy || k === "walk_forward" ? 2.2 : 1.4 },
          }))} layout={{ yaxis: { ticksuffix: "%" }, margin: { l: 48, r: 10, t: 8, b: 36 }, legend: { orientation: "h", y: -0.2 } }} />
        </div>
        <div>
          <p className="text-[12px] text-muted mb-1">Average next-open result of the nights the strategy held, by condition ({all ? "all symbols" : sym}, % of the position)</p>
          <Plot height={300} data={[true, false].map((met) => {
            const ws = groups.map((g) => worth.find((w) => w.group === g && w.met === met));
            return {
              x: groups.map((g) => SHORT[g]?.replace("X", String(x)) ?? g), y: ws.map((w) => w?.["avg_%"] ?? null),
              name: met ? "condition met (held)" : "not met (would sell)", type: "bar",
              marker: { color: met ? C.navy : C.gold },
              text: ws.map((w) => (w ? `${w.nights}` : "")), textposition: "outside",
              hovertemplate: ws.map((w) => (w ? `${w.nights} nights · avg %{y:.3f}% · gained ${w["gained_%"] ?? "–"}% · worst ${w["worst_%"] ?? "–"}%<extra></extra>` : "")),
            };
          })} layout={{ barmode: "group", hovermode: "closest", yaxis: { ticksuffix: "%" }, margin: { l: 48, r: 10, t: 8, b: 70 }, legend: { orientation: "h", y: -0.35 } }} />
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div className="overflow-x-auto">
          <p className="text-[12px] text-muted mb-1">Share of the strategy's held nights each condition would sell. Outside 2-60% it says little ("not informative", greyed).</p>
          <table className="w-full text-[12px] border-collapse">
            <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
              <th className={`text-left ${th}`}>Condition</th>{symbols.map((s) => <th key={s} className={`text-right ${th}`}>{s}</th>)}<th className={`text-right ${th}`}>All</th>
            </tr></thead>
            <tbody>{(per.blocking?.total ?? []).map((b) => (
              <tr key={b.condition} className="border-b border-line/60">
                <td className="py-1 px-2 whitespace-nowrap" title={d.condition_text[b.condition]}>{SHORT[b.condition]?.replace("X", String(x))}</td>
                {[...symbols.map((s) => per.blocking.per_symbol[s]?.find((y) => y.condition === b.condition)), b].map((y, i) => (
                  <td key={i} className={`py-1 px-2 text-right num ${y && !y.informative ? "text-muted" : ""}`}
                    title={y ? `${y.blocked} of ${y.nights} nights` : ""}>{y && y["blocks_%"] !== null ? `${fmt(y["blocks_%"], 0)}%` : "–"}</td>))}
              </tr>))}</tbody>
          </table>
        </div>
        <div className="overflow-x-auto">
          <p className="text-[12px] text-muted mb-1">Worst nights when the strategy alone decides whether to hold, all symbols</p>
          <table className="w-full text-[12px] border-collapse">
            <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
              {["Night", "Symbol", "From", "To", "Gap", "Result"].map((h, i) => <th key={h} className={`${i > 3 ? "text-right" : "text-left"} ${th}`}>{h}</th>)}
            </tr></thead>
            <tbody>{(["overnight", "weekend"] as const).flatMap((k) => (worst[k] ?? []).map((n, i) => (
              <tr key={`${k}${i}`} className="border-b border-line/60">
                <td className="py-1 px-2">{k === "weekend" ? "weekend / holiday" : "weeknight"}</td><td className="py-1 px-2">{n.symbol}</td>
                <td className="py-1 px-2">{n.date}</td><td className="py-1 px-2">{n.next_date}</td>
                <td className="py-1 px-2 text-right num">{pct(n["gap_%"])}</td><td className={`py-1 px-2 text-right num ${pnl(n["result_%"])}`}>{pct(n["result_%"])}</td>
              </tr>)))}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
