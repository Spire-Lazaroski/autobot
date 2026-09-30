import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, type DetailResult, type Params, type RunReq } from "../lib/api";
import { fmt, nyTime, pnl, signed } from "../lib/format";
import { CandleChart } from "./CandleChart";
import { SortTable } from "./SortTable";
import { ErrorBox, Kpi, LowTrades, Spinner } from "./ui";

/** Full-screen drawer: one strategy on one symbol, with candles, indicators, fills and trades. */
export function DetailView({ run, strategy, params, symbols, initialSymbol, onClose }: {
  run: RunReq;
  strategy: string;
  params: Params;
  symbols: string[];
  initialSymbol?: string;
  onClose: () => void;
}) {
  const [symbol, setSymbol] = useState(initialSymbol ?? symbols[0]);
  const body = { ...run, symbol, strategy, params };
  const q = useQuery({
    queryKey: ["detail", body],
    queryFn: () => api<DetailResult>("/detail", body),
    staleTime: 5 * 60_000,
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const d = q.data;
  const equity = useMemo(() => (d ? { strategy: d.equity, benchmark: d.benchmark } : undefined), [d]);
  const band = useMemo(() => (d?.active ? { names: d.active.names, title: "active" } : undefined), [d]);
  const m = d?.metrics, b = d?.benchmark_metrics;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex" onClick={onClose}>
      <div className="m-0 sm:m-4 flex-1 bg-bg border border-line rounded-md flex flex-col min-w-0 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <header className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-line">
          <span className="font-semibold num">{strategy}</span>
          <span className="text-muted num text-[11px] truncate max-w-[40ch]" title={JSON.stringify(params)}>
            {Object.entries(d?.params ?? params).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ")}
          </span>
          <div className="flex gap-1 ml-auto flex-wrap">
            {symbols.map((s) => (
              <button key={s} onClick={() => setSymbol(s)}
                className={`chip border num ${s === symbol ? "border-accent text-accent bg-accent/10" : "border-line-2 text-muted hover:text-fg"}`}>{s}</button>
            ))}
          </div>
          <button className="btn" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-3">
          {q.isLoading && <Spinner label="Running backtest…" />}
          <ErrorBox error={q.error} />
          {d && m && b && (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
                <Kpi label="Return" value={`${signed(m["total_return_%"])}%`} tone={pnl(m["total_return_%"])}
                  sub={`B&H ${signed(b["total_return_%"])}%`} />
                <Kpi label="Sharpe" value={fmt(m.sharpe)} tone={pnl(m.sharpe)} sub={`B&H ${fmt(b.sharpe)}`} />
                <Kpi label="Max DD" value={`${fmt(m["max_drawdown_%"])}%`} tone="text-down" sub={`B&H ${fmt(b["max_drawdown_%"])}%`} />
                <Kpi label="Trades" value={<span className="flex items-center gap-2">{m.trades}<LowTrades n={m.trades} /></span>}
                  sub={`win ${fmt(m["win_rate_%"], 1)}%`} />
                <Kpi label="Profit factor" value={fmt(m.profit_factor)} sub={`avg ${signed(m["avg_trade_%"], 3)}%`} />
                <Kpi label="Exposure" value={`${fmt(m["exposure_%"], 1)}%`} sub={`${d.candles.t.length} bars`} />
              </div>
              <div className="panel p-1">
                <CandleChart candles={d.candles} overlays={d.overlays} panes={d.panes} fills={d.fills}
                  position={d.position} equity={equity} band={band} height={Math.max(520, Math.min(900, window.innerHeight - 230))} />
              </div>
              <div className="flex gap-4 text-[11px] text-muted flex-wrap">
                <span><span className="text-up">▲ L</span> / <span className="text-down">▼ S</span> entry</span>
                <span>x exit</span>
                <span>Fills are drawn at the close of the bar that produced the signal (the backtest trades the next bar).</span>
              </div>
              <section className="panel p-3">
                <h3 className="panel-title mb-2">Trades ({d.trades.length})</h3>
                <div className="max-h-80 overflow-y-auto">
                  <SortTable rows={d.trades} rowKey={(r) => String(r.entry_time)}
                    initialSort={{ key: "entry_time", desc: true }}
                    cols={[
                      { key: "entry_time", label: "Entry (NY)", render: (r) => <span className="num">{nyTime(r.entry_time)}</span> },
                      { key: "exit_time", label: "Exit", render: (r) => nyTime(r.exit_time) },
                      { key: "side", label: "Side", render: (r) => <span className={r.side === "long" ? "text-up" : "text-down"}>{r.side}</span> },
                      { key: "bars", label: "Bars", digits: 0 },
                      { key: "entry_price", label: "In" },
                      { key: "exit_price", label: "Out" },
                      { key: "return_%", label: "Ret %", digits: 3, tone: true },
                    ]} />
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
