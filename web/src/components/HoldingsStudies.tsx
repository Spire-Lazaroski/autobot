import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt, pnl, signed } from "../lib/format";
import { Panel } from "./ui";

type Stats = Record<string, number | string>;
interface Resp {
  core: null | { period: [string, string]; methods: Record<string, Stats>; same_amount: Record<string, Stats>; crises?: Record<string, Record<string, number>> };
  rotation: null | { long: Record<string, Stats>; long_vs_market: Record<string, { "%_per_year": number; ci95: number[]; significant: boolean }>;
    long_periods?: Record<string, Record<string, Stats>>; recent: Record<string, Stats> };
}

const CORE_LABEL: Record<string, string> = { hold_30: "plain 30% (before B)", ewma: "EWMA (live: B)", har: "HAR", trees: "trees", vix: "VIX",
  har_vix: "HAR + VIX", trees_vix: "trees + VIX", garch: "GARCH", gjr: "GJR-GARCH", egarch: "EGARCH" };
const LIVE_ROT = "rotation 12m top 5";

/** Lab panel: the backtests behind the long-term holdings (core with volatility targeting, sector rotation), next
 *  to the jobs' strategies. From the saved studies (docs/STRATEGY_EVIDENCE.md sections 12, 15, 23-24). */
export function HoldingsStudies() {
  const q = useQuery({ queryKey: ["holdings-studies"], queryFn: () => api<Resp>("/holdings-studies"), staleTime: 300_000 });
  const d = q.data;
  if (!d) return null;
  return (
    <Panel title={<span className="inline-flex items-center gap-1.5"><i className="bi bi-briefcase text-muted" aria-hidden="true" />
      Long-term holdings · the backtests behind the core and the rotation sleeve</span>}>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 text-[12px]">
        {d.core && (
          <div className="min-w-0">
            <div className="font-semibold mb-1">Core (VOO): sized by a volatility forecast, 15-45% · S&P 500 {d.core.period[0]} to {d.core.period[1]}</div>
            <table className="w-full">
              <thead><tr className="text-muted text-[10.5px] uppercase"><th className="text-left">Method</th><th className="text-right">Per year</th>
                <th className="text-right">Worst drop</th><th className="text-right">Per risk</th><th className="text-right">Trades / yr</th></tr></thead>
              <tbody>{Object.entries(d.core.methods).map(([k, s]) => (
                <tr key={k} className={`border-t border-line/60 ${k === "ewma" ? "font-semibold" : ""}`}>
                  <td>{CORE_LABEL[k] ?? k}</td><td className="num text-right">{fmt(Number(s["yearly_%"]), 2)}%</td>
                  <td className="num text-right text-down">{fmt(Number(s["max_drop_%"]), 1)}%</td>
                  <td className="num text-right">{fmt(Number(d.core!.same_amount[k]?.per_risk), 2)}</td>
                  <td className="num text-right">{fmt(Number(s.trades_per_year), 0)}</td></tr>))}</tbody>
            </table>
            <p className="text-muted mt-1">Account = core + cash, after costs. "Per risk" at the same average amount invested. The live core uses EWMA (bold).</p>
          </div>)}
        {d.rotation && (
          <div className="min-w-0">
            <div className="font-semibold mb-1">Rotation: top sector funds monthly, trend filter, volatility sizing</div>
            <table className="w-full">
              <thead><tr className="text-muted text-[10.5px] uppercase"><th className="text-left">Strategy</th><th className="text-right">1927-2026 / yr</th>
                <th className="text-right">Worst drop</th><th className="text-right">vs market, same risk</th><th className="text-right">2017-2026 / yr</th></tr></thead>
              <tbody>{Object.entries(d.rotation.long).map(([k, s]) => {
                const vs = d.rotation!.long_vs_market[k];
                const rec = d.rotation!.recent[k];
                return (
                  <tr key={k} className={`border-t border-line/60 ${k === LIVE_ROT ? "font-semibold" : ""}`}>
                    <td>{k}</td><td className="num text-right">{fmt(Number(s["yearly_%"]), 1)}%</td>
                    <td className="num text-right text-down">{fmt(Number(s["worst_drop_%"]), 0)}%</td>
                    <td className={`num text-right ${vs ? pnl(vs["%_per_year"]) : ""}`}>{vs ? `${signed(vs["%_per_year"], 1)}%${vs.significant ? "" : " ~"}` : "–"}</td>
                    <td className="num text-right">{rec ? `${fmt(Number(rec["yearly_%"]), 1)}%` : "–"}</td></tr>);
              })}</tbody>
            </table>
            <p className="text-muted mt-1">French 10 industries (1927-2026) and sector ETFs (2017-2026), after costs. "~" = within chance.
              The live sleeve is the top 5 (bold); over 2017-2026 nothing beat holding SPY.</p>
          </div>)}
      </div>
    </Panel>
  );
}
