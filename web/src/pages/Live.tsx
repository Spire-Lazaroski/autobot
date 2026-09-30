import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { CandleChart } from "../components/CandleChart";
import { Confirm } from "../components/Confirm";
import { JobsEditor } from "../components/JobsEditor";
import { SortTable } from "../components/SortTable";
import { ErrorBox, Kpi, Panel, Spinner } from "../components/ui";
import { api, type Candles, type Fill, type Row } from "../lib/api";
import { colorFor, fmt, nyTime, pnl, signed } from "../lib/format";
import { useLiveSocket } from "../lib/useLiveSocket";

interface BotState {
  running: boolean;
  unresponsive?: boolean;
  pid?: number;
  heartbeat_age_s?: number;
  process_alive?: boolean;
  stop_requested?: boolean;
}
interface Snapshot {
  time: number;
  bot: BotState;
  status: null | {
    time: string; equity: number; "day_pnl_%": number; market_open: boolean; halted: boolean; timeframe?: string;
    jobs?: Row[]; config_changes?: { time: string; change: string }[];
    symbols: Record<string, { job: string; strategy: string; price: number; signal: number; position: number; target: number; active?: string | null }>;
  };
  trades: Row[];
  log: string[];
  ok: boolean;
  error?: string;
  account?: { number_tail: string; status: string; equity: number; last_equity: number; day_pnl: number; day_pnl_pct: number; cash: number; buying_power: number };
  positions: Row[];
  orders: Row[];
  market?: { is_open: boolean; next_open: number; next_close: number };
}

const ts = (s: string | number | null | undefined) => (s === null || s === undefined || s === "" ? "–" : nyTime(typeof s === "number" ? s : Date.parse(s) / 1000));

export function Live() {
  const { data: d, connected, lastAt } = useLiveSocket<Snapshot>();
  const [confirmStop, setConfirmStop] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const call = async (path: string, body: unknown) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api<{ message?: string; pid?: number }>(path, body);
      setMsg({ ok: true, text: r.message ?? `Bot started (pid ${r.pid}). It acts a few seconds after each bar closes.` });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false); setConfirmStop(false);
    }
  };

  const bot = d?.bot;
  const st = d?.status;
  const alive = !!bot && (bot.running || !!bot.unresponsive);
  const state = !bot ? "…" : bot.stop_requested && alive ? "stopping" : bot.unresponsive ? "unresponsive"
    : bot.running ? (st?.halted ? "halted" : "running") : "stopped";
  const stateTone = state === "running" ? "text-up" : state === "stopped" ? "text-muted" : "text-warn";
  const acct = d?.account;

  const symbols = useMemo(() => {
    const s = new Set<string>();
    st?.jobs?.forEach((j) => (j.symbols as string[]).forEach((x) => s.add(x)));
    Object.keys(st?.symbols ?? {}).forEach((x) => s.add(x));
    d?.positions.forEach((p) => s.add(p.symbol));
    return [...s];
  }, [st, d?.positions]);

  return (
    <div className="flex flex-col gap-3">
      <div className="panel p-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${state === "running" ? "bg-up animate-pulse" : state === "stopped" ? "bg-dim" : "bg-warn"}`} />
          <span className={`font-semibold uppercase tracking-wide text-[12px] ${stateTone}`}>bot {state}</span>
          {alive && <span className={`num text-[11px] ${bot?.unresponsive ? "text-warn" : "text-muted"}`}>pid {bot?.pid} · beat {fmt(bot?.heartbeat_age_s, 0)}s ago</span>}
          {bot?.unresponsive && <span className="text-[11px] text-warn">process alive but no heartbeat: Start is blocked</span>}
          {bot && !bot.running && bot.process_alive === false && bot.pid && <span className="text-[11px] text-dim">(last pid {bot.pid} exited)</span>}
        </span>
        <span className="text-[12px] text-muted">market <b className={d?.market?.is_open ? "text-up" : "text-fg"}>{d?.market ? (d.market.is_open ? "open" : "closed") : "–"}</b>
          {d?.market && <span className="num text-dim"> · {d.market.is_open ? `closes ${nyTime(d.market.next_close)}` : `opens ${nyTime(d.market.next_open)}`}</span>}
        </span>
        <span className="text-[11px] text-muted num flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-up" : "bg-down"}`} />
          {connected ? `live · ${lastAt ? new Date(lastAt).toLocaleTimeString() : ""}` : "reconnecting…"}
        </span>
        <div className="ml-auto flex gap-2">
          <button className="btn btn-primary" disabled={!bot || alive || busy} onClick={() => call("/live/start", {})}>▶ Start bot</button>
          <button className="btn btn-danger" disabled={!alive || busy || bot?.stop_requested} onClick={() => setConfirmStop(true)}>⏹ Stop + flatten</button>
        </div>
        {msg && <div className={`basis-full text-[12px] ${msg.ok ? "text-up" : "text-down"}`}>{msg.text}</div>}
      </div>

      {!d && <Panel><Spinner label="Connecting to the live feed…" /></Panel>}
      {d && !d.ok && <ErrorBox error={`Alpaca paper account unavailable: ${d.error}`} />}

      {d && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2">
            <Kpi label={`Paper equity · ···${acct?.number_tail ?? ""}`} value={acct ? `$${fmt(acct.equity)}` : "–"} sub={acct?.status} />
            <Kpi label="Day P&L" value={acct ? `${signed(acct.day_pnl)}` : "–"} tone={pnl(acct?.day_pnl)} sub={acct ? `${signed(acct.day_pnl_pct, 3)}%` : undefined} />
            <Kpi label="Buying power" value={acct ? `$${fmt(acct.buying_power, 0)}` : "–"} sub={acct ? `cash $${fmt(acct.cash, 0)}` : undefined} />
            <Kpi label="Open positions" value={d.positions.length}
              sub={`unrealized ${signed(d.positions.reduce((a, p) => a + (p.unrealized_pl ?? 0), 0))}`} />
            <Kpi label="Bot last tick" value={st ? ts(st.time) : "–"} sub={st ? `${st.timeframe ?? ""} bars · ${st.jobs?.length ?? 0} jobs` : "no status yet"} />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            <Panel title="Positions">
              {d.positions.length ? (
                <SortTable rows={d.positions} rowKey={(r) => r.symbol} initialSort={{ key: "market_value", desc: true }}
                  cols={[
                    { key: "symbol", label: "Symbol", render: (r) => <span className="num font-semibold">{r.symbol}</span> },
                    { key: "qty", label: "Qty", digits: 4 },
                    { key: "avg_entry_price", label: "Avg" },
                    { key: "current_price", label: "Last" },
                    { key: "market_value", label: "Value", digits: 0 },
                    { key: "unrealized_pl", label: "P&L $", tone: true },
                    { key: "unrealized_plpc", label: "P&L %", tone: true },
                    { key: "unrealized_intraday_pl", label: "Today $", tone: true },
                  ]} />
              ) : <p className="text-muted text-[12px]">No open positions.</p>}
            </Panel>
            <Panel title="Bot view per symbol (last tick)">
              {st && Object.keys(st.symbols).length ? (
                <SortTable rows={Object.entries(st.symbols).map(([symbol, v]) => ({ symbol, ...v }))} rowKey={(r) => r.symbol}
                  cols={[
                    { key: "symbol", label: "Symbol", render: (r) => <span className="num font-semibold">{r.symbol}</span> },
                    { key: "job", label: "Job", render: (r) => <span className="text-muted">{r.job}</span> },
                    { key: "strategy", label: "Strategy", render: (r) => (
                      <span className="num">{r.strategy}{r.active && <span className="ml-1.5 chip" style={{ background: `${colorFor(r.active)}33`, color: colorFor(r.active) }}>{r.active}</span>}</span>) },
                    { key: "signal", label: "Signal", tone: true },
                    { key: "position", label: "Pos", digits: 4 },
                    { key: "target", label: "Target", digits: 4 },
                    { key: "price", label: "Price" },
                  ]} />
              ) : <p className="text-muted text-[12px]">{bot?.running ? "Waiting for the next bar…" : "Start the bot to see its per-symbol view."}</p>}
            </Panel>
          </div>

          <LiveChart symbols={symbols} timeframe={st?.timeframe} />

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            <Panel title="Orders (paper account, latest 50)">
              <div className="max-h-80 overflow-y-auto">
                <SortTable rows={d.orders} rowKey={(r) => r.id} initialSort={{ key: "submitted_at", desc: true }}
                  cols={[
                    { key: "submitted_at", label: "Submitted (NY)", render: (r) => <span className="num">{ts(r.submitted_at)}</span> },
                    { key: "symbol", label: "Symbol", render: (r) => <span className="num">{r.symbol}</span> },
                    { key: "side", label: "Side", render: (r) => <span className={r.side === "buy" ? "text-up" : "text-down"}>{r.side}</span> },
                    { key: "qty", label: "Qty", digits: 4 },
                    { key: "filled_avg_price", label: "Fill" },
                    { key: "status", label: "Status", render: (r) => <span className={r.status === "filled" ? "text-fg" : "text-warn"}>{r.status}</span> },
                  ]} />
              </div>
            </Panel>
            <Panel title="Bot trades (logs/trades.csv)">
              <div className="max-h-80 overflow-y-auto">
                {d.trades.length ? (
                  <SortTable rows={d.trades} rowKey={(r) => `${r.time}${r.order_id}`} initialSort={{ key: "time", desc: true }}
                    cols={[
                      { key: "time", label: "Time (NY)", render: (r) => <span className="num">{ts(r.time)}</span> },
                      { key: "job", label: "Job", render: (r) => <span className="text-muted">{r.job}</span> },
                      { key: "symbol", label: "Symbol", render: (r) => <span className="num">{r.symbol}</span> },
                      { key: "side", label: "Side", render: (r) => <span className={r.side === "buy" ? "text-up" : r.side === "sell" ? "text-down" : "text-warn"}>{r.side}</span> },
                      { key: "qty", label: "Qty", render: (r) => <span className="num">{r.qty}</span> },
                      { key: "approx_price", label: "~Price", render: (r) => <span className="num">{r.approx_price}</span> },
                      { key: "reason", label: "Reason", render: (r) => <span className="text-muted">{r.reason}</span> },
                    ]} />
                ) : <p className="text-muted text-[12px]">No bot trades yet.</p>}
              </div>
            </Panel>
          </div>

          <JobsEditor botRunning={!!bot?.running} />

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            <Panel title="Config changes seen by the bot">
              {st?.config_changes?.length ? (
                <ul className="num text-[11px] flex flex-col gap-0.5 max-h-60 overflow-y-auto">
                  {[...st.config_changes].reverse().map((c, i) => (
                    <li key={i} className={c.change.startsWith("rejected") ? "text-down" : "text-fg"}><span className="text-dim">{ts(c.time)}</span> {c.change}</li>
                  ))}
                </ul>
              ) : <p className="text-muted text-[12px]">None since the bot started.</p>}
            </Panel>
            <Panel title="Bot log (tail)">
              <pre className="num text-[10.5px] leading-snug max-h-60 overflow-auto whitespace-pre-wrap text-muted">
                {d.log.slice(-40).map((l, i) => (
                  <div key={i} className={/ERROR/.test(l) ? "text-down" : /WARNING|CONFIG|FLATTEN/.test(l) ? "text-warn" : /ORDER/.test(l) ? "text-fg" : ""}>{l}</div>
                ))}
              </pre>
            </Panel>
          </div>
        </>
      )}

      {confirmStop && (
        <Confirm title="Stop the bot and close every position?" confirmLabel="Stop + flatten" danger busy={busy}
          onCancel={() => setConfirmStop(false)} onConfirm={() => call("/live/stop", { confirm: true })}>
          The bot will <b className="text-fg">close all open positions in the paper account</b> (market orders, open orders cancelled)
          and exit within a few seconds. This includes positions it didn't open.
        </Confirm>
      )}
    </div>
  );
}

function LiveChart({ symbols, timeframe }: { symbols: string[]; timeframe?: string }) {
  const [sym, setSym] = useState<string | null>(null);
  const current = sym ?? symbols[0];
  const q = useQuery({
    queryKey: ["live-candles", current, timeframe],
    queryFn: () => api<{ candles: Candles; fills: { time: number; price: number; side: string; qty: number }[]; timeframe: string }>(
      `/live/candles?symbol=${encodeURIComponent(current)}${timeframe ? `&timeframe=${timeframe}` : ""}`),
    enabled: !!current,
    refetchInterval: 30_000,
  });
  const fills: Fill[] = useMemo(() => (q.data?.fills ?? []).map((f) => ({
    time: f.time, price: f.price, side: f.side as "buy" | "sell", kind: "entry", from: 0, to: f.side === "buy" ? 1 : -1,
    label: `${f.side === "buy" ? "B" : "S"} ${f.qty}`,
  })), [q.data]);
  return (
    <Panel title={`Live candles${q.data ? ` · ${q.data.timeframe}` : ""} · markers = actual paper fills · refreshes every 30 s`}
      right={<div className="flex gap-1 flex-wrap">{symbols.map((s) => (
        <button key={s} onClick={() => setSym(s)}
          className={`chip border num ${s === current ? "border-accent text-accent bg-accent/10" : "border-line-2 text-muted"}`}>{s}</button>
      ))}</div>}>
      {!current && <p className="text-muted text-[12px]">No symbols in jobs or positions.</p>}
      {q.isLoading && <Spinner label="Loading bars…" />}
      <ErrorBox error={q.error} />
      {q.data && <CandleChart candles={q.data.candles} fills={fills} height={420} />}
    </Panel>
  );
}
