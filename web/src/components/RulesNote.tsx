import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

/** Which of the live bot's signal rules the Lab's backtests use (config live.trade_rules), so the numbers
 *  here describe the bot as it actually trades. */
export function RulesNote() {
  const q = useQuery({ queryKey: ["trade-rules"], queryFn: () => api<{ trade_rules: Record<string, unknown> }>("/trade-rules"), staleTime: 60_000 });
  const r = q.data?.trade_rules ?? {};
  const on = [r.no_quick_reentry ? "no quick re-entry" : null, r.churn_brake ? "churn brake" : null].filter(Boolean);
  return (
    <div className="text-[11.5px] text-muted flex items-center gap-1.5">
      <i className="bi bi-shield-check" aria-hidden="true" />
      {q.isLoading ? "Checking the live rules…" : on.length
        ? <>Backtests use the live bot's rules: <b className="text-fg font-semibold">{on.join(", ")}</b> (config live.trade_rules), so results match how the jobs trade.</>
        : "Backtests use the bare strategy signals (no live trade rules are switched on)."}
    </div>
  );
}
