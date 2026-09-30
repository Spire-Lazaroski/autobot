import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { DetailView } from "../components/DetailView";
import { Plot } from "../components/Plot";
import { SettingsBar } from "../components/SettingsBar";
import { SortTable, type Col } from "../components/SortTable";
import { StrategyCards, pickedOf } from "../components/StrategyCards";
import { Empty, ErrorBox, Kpi, LowTrades, Panel, Spinner } from "../components/ui";
import { api, useStrategies, type CompareResult, type Params, type RunReq } from "../lib/api";
import { colorFor, fmt, pnl, signed, toDates } from "../lib/format";
import { useSettings } from "../lib/settings";
import { usePageState } from "../lib/usePageState";

type CompareReq = RunReq & { strategies: Record<string, Params> };

export function Lab() {
  const { run, picked, setPicked } = useSettings();
  const strategies = useStrategies();
  const [req, setReq] = usePageState<CompareReq | null>("lab.req", null);
  const [open, setOpen] = useState<{ strategy: string; symbol?: string } | null>(null);
  const [cardsOpen, setCardsOpen] = usePageState("lab.cardsOpen", true);

  const q = useQuery({
    queryKey: ["compare", req],
    queryFn: () => api<CompareResult>("/compare", req),
    enabled: !!req,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
  });

  const chosen = useMemo(() => {
    const out: Record<string, Params> = {};
    for (const s of strategies.data ?? []) {
      const p = pickedOf(s, picked);
      if (p.on) out[s.name] = p.params;
    }
    return out;
  }, [strategies.data, picked]);

  const runIt = () => setReq({ ...run, strategies: chosen });
  const res = q.data;

  return (
    <div className="flex flex-col gap-3">
      <SettingsBar action={
        <button className="btn btn-primary" onClick={runIt} disabled={!Object.keys(chosen).length || q.isFetching}>
          {q.isFetching ? <Spinner /> : "▶"} Run backtest
        </button>
      } />

      <Panel title={`Strategies (${Object.keys(chosen).length} selected)`}
        right={<button className="text-[11px] text-muted hover:text-fg" onClick={() => setCardsOpen(!cardsOpen)}>{cardsOpen ? "hide ▴" : "show ▾"}</button>}>
        {cardsOpen && strategies.data && <StrategyCards strategies={strategies.data} picked={picked} onChange={setPicked} />}
        {strategies.error && <ErrorBox error={strategies.error} />}
      </Panel>

      <ErrorBox error={q.error} />
      {!req && <Empty>Pick strategies and press <b>Run backtest</b>. Buy-and-hold is the benchmark to beat.</Empty>}
      {res && req && <Results res={res} req={req} onOpen={(strategy, symbol) => setOpen({ strategy, symbol })} />}

      {open && req && (
        <DetailView run={req} strategy={open.strategy} params={req.strategies[open.strategy] ?? {}}
          symbols={req.data.symbols} initialSymbol={open.symbol} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}

function Results({ res, req, onOpen }: { res: CompareResult; req: CompareReq; onOpen: (s: string, sym?: string) => void }) {
  const summary = res.summary;
  const best = summary[0];
  const bh = summary.find((r) => r.strategy === "buy_and_hold");
  const names = Object.keys(res.equity.series);
  const x = useMemo(() => toDates(res.equity.t), [res]);
  const lowMin = res.low_trades_threshold;

  const equityTraces = names.map((n) => ({
    x, y: res.equity.series[n].map((v) => (v === null ? null : (v - 1) * 100)), name: n, type: "scattergl", mode: "lines",
    line: { color: colorFor(n), width: n === best?.strategy ? 2.5 : 1.2, dash: n === "buy_and_hold" ? "dot" : "solid" },
    hovertemplate: "%{y:.2f}%",
  }));
  const ddTraces = names.map((n) => {
    let peak = -Infinity;
    const y = res.equity.series[n].map((v) => {
      if (v === null) return null;
      peak = Math.max(peak, v);
      return (v / peak - 1) * 100;
    });
    return { x, y, name: n, type: "scattergl", mode: "lines", line: { color: colorFor(n), width: 1, dash: n === "buy_and_hold" ? "dot" : "solid" }, showlegend: false, hovertemplate: "%{y:.2f}%" };
  });

  const syms = [...new Set(res.per_symbol.map((r) => r.symbol as string))];
  const strats = summary.map((r) => r.strategy as string);
  const heat = strats.map((s) => syms.map((y) => res.per_symbol.find((r) => r.strategy === s && r.symbol === y)?.["total_return_%"] ?? null));
  const heatText = strats.map((s) => syms.map((y) => {
    const r = res.per_symbol.find((r) => r.strategy === s && r.symbol === y);
    return r ? `${fmt(r["total_return_%"], 1)}${r.trades < lowMin ? "*" : ""}` : "";
  }));

  const cols: Col[] = [
    { key: "strategy", label: "Strategy", render: (r) => (
      <span className="flex items-center gap-1.5">
        <span className="inline-block w-2 h-2 rounded-full" style={{ background: colorFor(r.strategy) }} />
        <span className="num font-semibold">{r.strategy}</span>
        <LowTrades n={r.total_trades} min={lowMin} />
      </span>) },
    { key: "total_return_%", label: "Ret %", tone: true },
    { key: "cagr_%", label: "CAGR %", tone: true },
    { key: "sharpe", label: "Sharpe", tone: true },
    { key: "sortino", label: "Sortino", tone: true },
    { key: "max_drawdown_%", label: "MaxDD %" },
    { key: "calmar", label: "Calmar", tone: true },
    { key: "total_trades", label: "Trades", digits: 0, title: "Total across symbols" },
    { key: "win_rate_%", label: "Win %", digits: 1 },
    { key: "avg_trade_%", label: "Avg tr %", digits: 3, tone: true },
    { key: "profit_factor", label: "PF" },
    { key: "exposure_%", label: "Exp %", digits: 1 },
  ];

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <Kpi label="Best Sharpe" value={best?.strategy} sub={`Sharpe ${fmt(best?.sharpe)}`} tone="text-accent" />
        <Kpi label="Its return" value={`${signed(best?.["total_return_%"])}%`} tone={pnl(best?.["total_return_%"])}
          sub={bh ? `${signed(best["total_return_%"] - bh["total_return_%"])}% vs B&H` : undefined} />
        <Kpi label="Its max drawdown" value={`${fmt(best?.["max_drawdown_%"])}%`} tone="text-down"
          sub={bh ? `B&H ${fmt(bh["max_drawdown_%"])}%` : undefined} />
        <Kpi label="Its trades" value={<span className="flex items-center gap-2">{best?.total_trades}<LowTrades n={best?.total_trades} min={lowMin} /></span>}
          sub={Object.entries(res.bars).map(([s, n]) => `${s}:${n}`).join(" ")} />
      </div>

      <Panel title="Equity (equal weight across symbols, % return)">
        <Plot data={equityTraces} height={380} layout={{ yaxis: { ticksuffix: "%" }, margin: { l: 48, r: 12, t: 8, b: 32 } }} />
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel title="Drawdown %">
          <Plot data={ddTraces} height={280} layout={{ yaxis: { ticksuffix: "%" }, margin: { l: 48, r: 12, t: 8, b: 32 } }} />
        </Panel>
        <Panel title="Return vs drawdown (bubble = trades)">
          <Plot height={280} onClick={(e) => e.points?.[0] && onOpen(e.points[0].text)}
            data={[{
              x: summary.map((r) => r["max_drawdown_%"]), y: summary.map((r) => r["total_return_%"]),
              text: summary.map((r) => r.strategy), mode: "markers+text", type: "scatter", textposition: "top center",
              textfont: { size: 10 },
              marker: {
                size: summary.map((r) => 8 + Math.sqrt(Math.max(1, r.total_trades)) * 2.2),
                color: summary.map((r) => colorFor(r.strategy)), opacity: 0.8, line: { width: 1, color: "#0a0d11" },
              },
              hovertemplate: "%{text}<br>ret %{y:.2f}%<br>dd %{x:.2f}%<extra></extra>",
            }]}
            layout={{ hovermode: "closest", xaxis: { title: { text: "max drawdown %" } }, yaxis: { title: { text: "return %" } }, margin: { l: 52, r: 12, t: 8, b: 40 } }} />
        </Panel>
      </div>

      <Panel title="Metrics · click a row for candles, indicators and trades">
        <SortTable rows={summary} cols={cols} rowKey={(r) => r.strategy} initialSort={{ key: "sharpe", desc: true }}
          onRowClick={(r) => onOpen(r.strategy)}
          highlight={(r) => (r.strategy === "buy_and_hold" ? "text-muted" : undefined)} />
      </Panel>

      <Panel title={`Return % by strategy × symbol · * = fewer than ${lowMin} trades · click a cell`}>
        <Plot height={Math.max(260, strats.length * 24 + 60)}
          onClick={(e) => e.points?.[0] && onOpen(e.points[0].y, e.points[0].x)}
          data={[{
            z: heat, x: syms, y: strats, type: "heatmap", colorscale: [[0, "#ef4444"], [0.5, "#161c25"], [1, "#22c55e"]],
            zmid: 0, text: heatText, texttemplate: "%{text}", textfont: { size: 10, family: "Consolas, monospace", color: "#e5e7eb" },
            hovertemplate: "%{y} · %{x}: %{z:.2f}%<extra></extra>", xgap: 2, ygap: 2, colorbar: { thickness: 8, ticksuffix: "%" },
          }]}
          layout={{ hovermode: "closest", yaxis: { autorange: "reversed", tickfont: { family: "Consolas, monospace", size: 10 } }, margin: { l: 150, r: 12, t: 8, b: 32 } }} />
      </Panel>
      <p className="text-[11px] text-dim">
        {req.data.source === "demo" ? "Demo data. " : ""}Costs: {req.fee_bps} bps fee + {req.slippage_bps} bps slippage per unit turned over.
        Signals trade on the next bar. Trust out-of-sample results (Parameter search, Walk-forward) over this in-sample view.
      </p>
    </>
  );
}
