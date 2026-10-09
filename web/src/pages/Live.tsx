import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { CandleChart, type PriceMark } from "../components/CandleChart";
import { C } from "../lib/chart";
import { Confirm } from "../components/Confirm";
import { JobsEditor } from "../components/JobsEditor";
import { SortTable } from "../components/SortTable";
import { ErrorBox, Panel, Spinner } from "../components/ui";
import { Chip, CoreCard, EquityChart, JobsCard, LogPanel, PositionsSplit, RotationCard, Section, ShareBar, SplitBar, Tile, toneOf,
  type EquityRow, type JobsVsHold, type LogEntry, type Split } from "../components/LiveOverview";
import { api, type Candles, type Fill, type Row } from "../lib/api";
import { colorFor, fmt, nyTime, pnl, signed } from "../lib/format";
import { useLiveSocket } from "../lib/useLiveSocket";
import { brand, chart } from "../theme";

/** The day's equity as a gold area sparkline on the dark band. */
function BandSparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="text-[11px]" style={{ color: chart.soft }}>Day chart from the first tick after the open</div>;
  const lo = Math.min(...values), hi = Math.max(...values), w = 160, h = 36;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${hi === lo ? h / 2 : h - 3 - ((v - lo) / (hi - lo)) * (h - 6)}`);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" aria-hidden="true">
      <polygon points={`0,${h} ${pts.join(" ")} ${w},${h}`} fill={brand.gold} fillOpacity={0.18} />
      <polyline points={pts.join(" ")} fill="none" stroke={brand.gold} strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

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
    symbols: Record<string, SymView>;
    core?: CoreView;
    rotation?: RotationView;
    split?: Split;
  };
  trades: Row[];
  log: string[];
  log_entries?: LogEntry[];
  equity_today?: EquityRow[];
  jobs_vs_hold?: JobsVsHold | null;
  ok: boolean;
  error?: string;
  account?: { number_tail: string; status: string; equity: number; last_equity: number; day_pnl: number; day_pnl_pct: number; cash: number; buying_power: number };
  positions: Row[];
  orders: Row[];
  market?: { is_open: boolean; next_open: number; next_close: number };
}

/** Display only (autobot/trigger.py): the price that changes the strategy's signal at the next close. */
interface Trigger {
  current_signal: number; flip_to: number; trigger_price: number | null; distance_pct: number | null;
  direction: "above" | "below" | null; kind: "sell" | "buy"; already_past: boolean; bars_if_flat: number | null; last_close: number;
  source?: "strategy" | "trail" | "re-entry" | "take-profit" | "stop";   // with an exit layer: which rule the level comes from
}
interface Peak { peak: number; gave_back_pct: number; since: string }
/** The rotation sleeve (autobot/rotation.py, config live.rotation): monthly sector funds, held long term. */
interface RotationView {
  enabled: boolean; allocation_pct: number; top_n: number; symbols: string[]; held: Record<string, number>;
  target?: { as_of: string | null; invested_pct?: number; "invested_%": number; weights: Record<string, number>; reasons: Record<string, string>;
    ranking: { rank: number; symbol: string; "momentum_%": number; above_trend: boolean; "volatility_%": number }[] };
  last_actions?: { time: string; symbol: string; qty: number; reason: string }[];
}

/** The core holding (autobot/core.py, config live.core): held long term, outside the trading jobs. */
interface CoreView {
  enabled: boolean; symbol: string; allocation_pct: number; rebalance_band_pct: number; qty: number;
  price?: number; value?: number; "weight_%"?: number | null;
  last_action?: { time: string; qty: number; reason: string; order_id: string | null };
  /** Volatility targeting (autobot/core.py vol_target): today's target share and why. */
  vol_target?: { target_pct: number; vol_pct: number; normal_pct: number; ratio: number; as_of: string; reason: string };
}
/** Both sides of the price (autobot/trigger.trigger_levels): at most one level below and one above. */
interface Levels { buy: Trigger[]; sell: Trigger[]; partial?: boolean }
interface SymView {
  job: string; strategy: string; price: number; signal: number; position: number; target: number; active?: string | null;
  trigger?: Trigger | null; triggers?: Levels | null; trigger_note?: string; peak?: Peak | null;
}

const verb = (t: Trigger) => (t.kind === "sell" ? (t.flip_to < 0 ? "goes short" : "sells") : t.flip_to > 0 && t.current_signal < 0 ? "covers" : "buys");
/** "sells below 762.70 (-0.12%, ~3 bars if flat)" / "sells at the next close unless above 105.52 (+0.05%)" */
export function triggerText(t: Trigger | null | undefined, note?: string): string {
  if (!t) return note ?? "–";
  if (t.trigger_price === null) return `${verb(t)} at the next close (whatever the price within ±5%)`;
  const p = fmt(t.trigger_price, 2), d = `${t.distance_pct! > 0 ? "+" : ""}${fmt(t.distance_pct, 2)}%`;
  if (t.already_past) return `${verb(t)} at the next close unless ${t.direction === "below" ? "above" : "below"} ${p} (${d})`;
  const flat = t.bars_if_flat ? `, ~${t.bars_if_flat} bar${t.bars_if_flat > 1 ? "s" : ""} if flat` : "";
  const why = t.source === "trail" ? ", trailing exit" : t.source === "re-entry" ? ", re-entry after an exit"
    : t.source === "take-profit" ? ", take-profit rule" : t.source === "stop" ? ", stop-loss rule" : "";
  return `${verb(t)} ${t.direction} ${p} (${d}${flat}${why})`;
}

/** The buy and sell levels; a bot started before the two columns existed only sends the nearest
 *  one, so the other side is unknown (partial) rather than "none". */
function levelsOf(v: SymView | undefined): Levels | null {
  if (!v) return null;
  if (v.triggers) return v.triggers;
  if (!v.trigger) return null;
  return { buy: v.trigger.kind === "buy" ? [v.trigger] : [], sell: v.trigger.kind === "sell" ? [v.trigger] : [], partial: true };
}

/** For a level that closes the position: on the profit side of the price it takes the profit; on the
 *  other side it is a stop, or locks in a profit once it has moved past the entry price. */
function roleOf(t: Trigger, position: number, entry: number | null): string | null {
  if (!position || t.trigger_price === null || t.already_past) return null;
  const long = position > 0;
  if ((long && t.kind !== "sell") || (!long && t.kind !== "buy")) return null;   // adds or flips, doesn't close
  if (t.direction === (long ? "above" : "below")) return "take profit";
  if (entry !== null && (long ? t.trigger_price > entry : t.trigger_price < entry)) return "locks profit";
  return "stop";
}

/** One trigger column: each level on its own line, and in plain words what is missing. */
function LevelCell({ v, kind, entry }: { v?: SymView; kind: "buy" | "sell"; entry: number | null }) {
  const lv = levelsOf(v);
  if (!v || !lv) return <span className="text-muted">{v?.trigger_note ?? "–"}</span>;
  const pos = v.target ?? v.position ?? 0, long = pos > 0, short = pos < 0;   // after this bar's orders
  const closes = (long && kind === "sell") || (short && kind === "buy");
  const lines = lv[kind].map((t) => { const r = roleOf(t, pos, entry); return { text: `${r ? `${r}: ` : ""}${triggerText(t)}`, muted: false }; });
  if (closes && !lv.partial) {
    const profit = long ? "above" : "below";
    const has = (dir: string) => lv[kind].some((t) => t.direction === dir || t.trigger_price === null);
    if (!has(profit)) lines.push({ text: "no profit target within 5% (lets profits run)", muted: true });
    if (!has(profit === "above" ? "below" : "above")) lines.push({ text: "no stop within 5% (the daily loss limit still applies)", muted: true });
  }
  if (!lines.length) {
    const why = (kind === "sell" && !pos) ? "no position" : (long && kind === "buy") || (short && kind === "sell") ? "holding" : lv.partial ? "" : "none within 5%";
    return <span className="text-muted">{why ? `– (${why})` : "–"}</span>;
  }
  return <span className="block">{lines.map((l, i) => (
    <span key={i} className={`block ${l.muted ? "text-muted" : kind === "sell" ? "text-down" : "text-up"}`}>{l.text}</span>))}</span>;
}

/** Average entry price of the paper position for a job symbol (crypto comes back without the slash). */
function entryOf(positions: Row[] | undefined, symbol: string): number | null {
  const p = positions?.find((q) => q.symbol === symbol || q.symbol === symbol.replace("/", ""));
  return p && p.avg_entry_price !== undefined && p.avg_entry_price !== null ? Number(p.avg_entry_price) : null;
}

const ts = (s: string | number | null | undefined) => (s === null || s === undefined || s === "" ? "–" : nyTime(typeof s === "number" ? s : Date.parse(s) / 1000));

export function Live() {
  const { data: d, connected } = useLiveSocket<Snapshot>();
  const [confirmStop, setConfirmStop] = useState(false);
  const [sellCore, setSellCore] = useState(false);
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
      setBusy(false); setConfirmStop(false); setSellCore(false);
    }
  };

  const bot = d?.bot;
  const st = d?.status;
  const longTerm = [st?.core?.enabled ? `${st.core.symbol} (core, ${fmt(st.core.qty, 0)} shares)` : "",
    ...Object.entries(st?.rotation?.enabled ? st.rotation.held : {}).map(([k, v]) => `${k} ${fmt(v, 0)}`)].filter(Boolean).join(", ");
  const longTermSet = useMemo(() => new Set<string>([...(st?.core?.enabled ? [st.core.symbol] : []),
    ...(st?.rotation?.enabled ? st.rotation.symbols : [])]), [st?.core, st?.rotation]);
  const alive = !!bot && (bot.running || !!bot.unresponsive);
  const state = !bot ? "…" : bot.stop_requested && alive ? "stopping" : bot.unresponsive ? "unresponsive"
    : bot.running ? (st?.halted ? "halted" : "running") : "stopped";
  const acct = d?.account;

  const symbols = useMemo(() => {
    const s = new Set<string>();
    st?.jobs?.forEach((j) => (j.symbols as string[]).forEach((x) => s.add(x)));
    Object.keys(st?.symbols ?? {}).forEach((x) => s.add(x));
    d?.positions.forEach((p) => s.add(p.symbol));
    return [...s];
  }, [st, d?.positions]);

  return (
    <div className="flex flex-col gap-4">
      {/* dark status band (Spire navy): bot state, the two numbers looked at most, the day's sparkline, start / stop */}
      <div className="rounded-xl px-4 py-3 grid gap-x-6 gap-y-2 items-center grid-cols-1 md:grid-cols-[1.3fr_1fr_1fr_1.2fr_auto]"
        style={{ background: brand.navy, color: "#ffffff" }}>
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[12px]">
            <span className={`h-2.5 w-2.5 rounded-full ${state === "running" ? "bg-up animate-pulse" : state === "stopped" ? "bg-dim" : "bg-warn"}`} />
            <span className="font-semibold uppercase tracking-wide">bot {state}</span>
            <span style={{ color: chart.faint }}>· market {d?.market ? (d.market.is_open ? "open" : "closed") : "–"}</span>
          </div>
          <div className="num text-[11px] truncate" style={{ color: chart.soft }}>
            {st ? `last tick ${ts(st.time)} · ${st.jobs?.length ?? 0} jobs` : "no status yet"}
            {d?.market ? ` · ${d.market.is_open ? `closes ${nyTime(d.market.next_close)}` : `opens ${nyTime(d.market.next_open)}`}` : ""}
            {alive ? ` · beat ${fmt(bot?.heartbeat_age_s, 0)}s` : ""}{connected ? "" : " · reconnecting…"}
          </div>
          {bot?.unresponsive && <div className="text-[11px] text-warn">process alive but no heartbeat: Start is blocked</div>}
        </div>
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wider" style={{ color: chart.faint }}>Equity · ···{acct?.number_tail ?? ""}</div>
          <div className="num text-[22px] font-semibold leading-tight">{acct ? `$${fmt(acct.equity, 0)}` : "–"}</div>
        </div>
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wider" style={{ color: chart.faint }}>Day P&L</div>
          <div className="num text-[22px] font-semibold leading-tight" style={{ color: acct && acct.day_pnl < 0 ? "#f09595" : "#7fd3a8" }}>
            {acct ? signed(acct.day_pnl) : "–"}</div>
          <div className="num text-[11px]" style={{ color: chart.soft }}>{acct ? `${signed(acct.day_pnl_pct, 3)}%` : ""}</div>
        </div>
        <div className="min-w-0"><BandSparkline values={(d?.equity_today ?? []).map((r) => r.equity)} /></div>
        <div className="flex gap-2 justify-end">
          <button className="btn btn-primary" disabled={!bot || alive || busy} onClick={() => call("/live/start", {})}>▶ Start bot</button>
          <button className="btn btn-danger" disabled={!alive || busy || bot?.stop_requested} onClick={() => setConfirmStop(true)}>⏹ Stop + flatten</button>
        </div>
        {msg && <div className={`md:col-span-5 text-[12px] ${msg.ok ? "text-up" : "text-down"}`}>{msg.text}</div>}
      </div>

      {!d && <Panel><Spinner label="Connecting to the live feed…" /></Panel>}
      {d && !d.ok && <ErrorBox error={`Alpaca paper account unavailable: ${d.error}`} />}

      {d && (
        <>
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
            <Tile label="Long-term today" tone={toneOf(st?.split?.long_term_pl_today)} valueTone={pnl(st?.split?.long_term_pl_today)}
              value={st?.split ? signed(st.split.long_term_pl_today) : "–"}
              sub={st?.split ? `core + rotation · ${fmt((st.split.core_usd + st.split.rotation_usd) / st.split.equity * 100, 1)}% held` : "core + rotation · from the next tick"}
              extra={st?.split ? <ShareBar now={(st.split.core_usd + st.split.rotation_usd) / st.split.equity * 100}
                target={st.split["targets_%"].core + st.split["targets_%"].rotation} /> : undefined} />
            <Tile label="Jobs today" tone={toneOf(st?.split?.jobs_pl_today)} valueTone={pnl(st?.split?.jobs_pl_today)}
              value={st?.split ? signed(st.split.jobs_pl_today) : "–"}
              sub={st?.split ? `${st?.jobs?.length ?? 0} jobs · realized + open` : "from the next tick"} />
            <Tile label="Cash" value={acct ? `$${fmt(acct.cash, 0)}` : "–"}
              sub={acct && acct.equity ? `${fmt((acct.cash / acct.equity) * 100, 0)}% of equity` : undefined} />
            <Tile label={d.jobs_vs_hold?.from ? `Jobs since ${d.jobs_vs_hold.from}` : "Jobs vs holding"} tone="warn"
              value={d.jobs_vs_hold && !d.jobs_vs_hold.error && d.jobs_vs_hold.job_days ? <>{signed(d.jobs_vs_hold.strategy_usd ?? 0, 0)}
                <span className="text-[13px] font-normal text-muted"> vs hold {signed(d.jobs_vs_hold.hold_usd ?? 0, 0)}</span></> : "–"}
              sub={d.jobs_vs_hold?.job_days ? `${d.jobs_vs_hold.job_days} job-days · the live check` : "worked out once a day"} />
          </div>

          <Section>Holdings</Section>
          <SplitBar split={st?.split} cash={acct?.cash} />
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <CoreCard core={st?.core} />
            <RotationCard rot={st?.rotation} />
            <JobsCard split={st?.split} jobs={st?.jobs?.length ?? 0} jvh={d.jobs_vs_hold}
              positions={d.positions.filter((p) => !longTermSet.has(p.symbol)).length} rules="No quick re-entry on · daily loss limit applies" />
          </div>
          <EquityChart rows={d.equity_today ?? []} fills={d.orders.filter((o) => o.status === "filled" && o.submitted_at)
            .map((o) => ({ t: Date.parse(String(o.submitted_at)) / 1000, side: String(o.side) }))} />
          <Section>Positions and trading jobs</Section>
          <PositionsSplit positions={d.positions} longTerm={longTermSet}
            extra={(r) => { const pk = peakOf(st?.symbols, r.symbol); return pk ? `gave back ${fmt(pk.gave_back_pct, 2)}% from ${fmt(pk.peak, 2)}` : null; }} />
          <div className="grid grid-cols-1 gap-3">
            <Panel title="Bot view per symbol (last tick)">
              {st && Object.keys(st.symbols).length ? (
                <SortTable zebra rows={Object.entries(st.symbols).map(([symbol, v]) => ({ symbol, ...v }))} rowKey={(r) => r.symbol}
                  cols={[
                    { key: "symbol", label: "Symbol", render: (r) => <span className="num font-semibold">{r.symbol}</span> },
                    { key: "job", label: "Job", render: (r) => <span className="text-muted">{r.job}</span> },
                    { key: "strategy", label: "Strategy", render: (r) => (
                      <span className="num">{r.strategy}{r.active && <span className="ml-1.5 chip" style={{ background: `${colorFor(r.active)}33`, color: colorFor(r.active) }}>{r.active}</span>}</span>) },
                    { key: "signal", label: "Signal", render: (r) => <Chip tone={r.signal > 0 ? "up" : r.signal < 0 ? "down" : "neutral"}>
                      {r.signal > 0 ? "long" : r.signal < 0 ? "short" : "flat"}</Chip> },
                    { key: "position", label: "Pos", digits: 4 },
                    { key: "target", label: "Target", digits: 4 },
                    { key: "price", label: "Price" },
                    { key: "trigger_buy", label: "Trigger buy", align: "left", title: "Price at which the strategy would buy at the next bar's close (display only)",
                      render: (r) => <span className="num whitespace-normal block min-w-[18ch] max-w-[30ch]">
                        <LevelCell v={viewOf(st?.symbols, r.symbol)} kind="buy" entry={entryOf(d.positions, r.symbol)} /></span> },
                    { key: "trigger_sell", label: "Trigger sell", align: "left",
                      title: "Price at which the strategy would sell (close the position) at the next bar's close: take profit above, stop or locked-in profit below (display only)",
                      render: (r) => <span className="num whitespace-normal block min-w-[18ch] max-w-[30ch]">
                        <LevelCell v={viewOf(st?.symbols, r.symbol)} kind="sell" entry={entryOf(d.positions, r.symbol)} /></span> },
                  ]} />
              ) : <p className="text-muted text-[12px]">{bot?.running ? "Waiting for the next bar…" : "Start the bot to see its per-symbol view."}</p>}
            </Panel>
          </div>

          <Section>Charts</Section>
          <LiveCharts symbols={symbols} positions={d.positions} timeframe={st?.timeframe} views={st?.symbols} />

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
            {d.log_entries ? <LogPanel entries={d.log_entries} /> : (
              <Panel title="Bot log (tail)">
                <pre className="num text-[10.5px] leading-snug max-h-60 overflow-auto whitespace-pre-wrap text-muted">{d.log.slice(-40).join("\n")}</pre>
              </Panel>)}
          </div>
        </>
      )}

      {confirmStop && (
        <Confirm title="Stop the bot and close every position?" confirmLabel="Stop + flatten" danger busy={busy}
          onCancel={() => { setConfirmStop(false); setSellCore(false); }} onConfirm={() => call("/live/stop", { confirm: true, include_core: sellCore })}>
          The bot will <b className="text-fg">close all open positions in the paper account</b> (market orders, open orders cancelled)
          and exit within a few seconds. This includes positions it didn't open
          {longTerm ? <>, <b className="text-fg">except the long-term holdings ({longTerm})</b> unless you tick the box below.</> : "."}
          {longTerm && (
            <label className="flex items-center gap-2 mt-3 text-fg">
              <input type="checkbox" checked={sellCore} onChange={(e) => setSellCore(e.target.checked)} />
              Also sell the long-term holdings ({longTerm})
            </label>)}
        </Confirm>
      )}
    </div>
  );
}

/** Alpaca reports BTC/USD positions as BTCUSD; the bot's status uses the job symbol. */
function viewOf(views: Record<string, SymView> | undefined, symbol: string): SymView | undefined {
  if (!views) return undefined;
  return views[symbol] ?? Object.entries(views).find(([k]) => k.replace("/", "") === symbol)?.[1];
}
function peakOf(views: Record<string, SymView> | undefined, symbol: string): Peak | null | undefined {
  return viewOf(views, symbol)?.peak;
}

/** The job symbol for an Alpaca position (crypto comes back without the slash: BTCUSD -> BTC/USD). */
function jobSymbol(symbols: string[], alpaca: string): string {
  return symbols.find((s) => s.replace("/", "") === alpaca) ?? alpaca;
}

/** One chart per symbol the account holds right now (appears when the bot buys, goes when it sells),
 *  then the switcher chart for any symbol, held symbols first. */
function LiveCharts({ symbols, positions, timeframe, views }: {
  symbols: string[]; positions: Row[]; timeframe?: string; views?: Record<string, SymView>;
}) {
  const held = useMemo(() => positions.map((p) => ({ sym: jobSymbol(symbols, p.symbol), pos: p })), [positions, symbols]);
  const heldSet = new Set(held.map((h) => h.sym));
  const ordered = [...held.map((h) => h.sym), ...symbols.filter((s) => !heldSet.has(s))];
  const [sym, setSym] = useState<string | null>(null);
  const current = sym && ordered.includes(sym) ? sym : ordered[0];
  return (
    <>
      {held.length > 0 && (
        <div className={`grid grid-cols-1 ${held.length > 1 ? "xl:grid-cols-2" : ""} gap-3`}>
          {held.map(({ sym: s, pos }) => (
            <Panel key={s} title={<span>
              <span className="num font-semibold">{s}</span>
              <span className="text-muted font-normal"> · held {fmt(pos.qty, Number(pos.qty) % 1 ? 4 : 0)} @ {fmt(pos.avg_entry_price, 2)} · </span>
              <span className={`num font-normal ${pnl(pos.unrealized_pl)}`}>{signed(pos.unrealized_pl)} ({signed(pos.unrealized_plpc, 2)}%)</span>
            </span>}>
              <SymbolChart symbol={s} timeframe={timeframe} view={viewOf(views, s)} entry={entryOf(positions, s)} height={held.length > 1 ? 300 : 360} />
            </Panel>))}
        </div>)}
      <Panel title={`${held.length ? "All symbols" : "Live candles"} · markers = actual paper fills · refreshes every 30 s`}
        right={<div className="flex gap-1 flex-wrap">{ordered.map((s) => (
          <button key={s} onClick={() => setSym(s)} title={heldSet.has(s) ? "held now" : "not held"}
            className={`chip border num ${s === current ? "tab-on" : "border-line-2 text-muted"}`}>{heldSet.has(s) && <span className="text-up mr-0.5">●</span>}{s}</button>
        ))}</div>}>
        {!current && <p className="text-muted text-[12px]">No symbols in jobs or positions.</p>}
        {current && <SymbolChart symbol={current} timeframe={timeframe} view={viewOf(views, current)} entry={entryOf(positions, current)} height={420} />}
      </Panel>
    </>
  );
}

function SymbolChart({ symbol, timeframe, view: v, entry, height }: { symbol: string; timeframe?: string; view?: SymView; entry: number | null; height: number }) {
  const pk = v?.peak?.peak ?? null;
  const lv = levelsOf(v), pos = v?.target ?? v?.position ?? 0;
  const key = JSON.stringify([lv, pos, entry]);
  const priceLines: PriceMark[] = useMemo(() => {
    const out: PriceMark[] = [];
    for (const t of [...(lv?.buy ?? []), ...(lv?.sell ?? [])]) {
      if (t.trigger_price === null) continue;
      const role = roleOf(t, pos, entry), src = t.source && t.source !== "strategy" ? ` (${t.source})` : "";
      out.push({ price: t.trigger_price, color: t.kind === "sell" ? C.loss : C.profit, style: "dashed",
        title: `${role ? `${role}: ` : ""}${t.kind} ${t.direction === "below" ? "<" : ">"} ${fmt(t.trigger_price, 2)}${src}` });
    }
    if (pk !== null) out.push({ price: pk, color: C.inkFaint, style: "dotted", title: `peak ${fmt(pk, 2)}` });
    return out;
  }, [key, pk]); // eslint-disable-line react-hooks/exhaustive-deps
  const q = useQuery({
    queryKey: ["live-candles", symbol, timeframe],
    queryFn: () => api<{ candles: Candles; fills: { time: number; price: number; side: string; qty: number }[]; timeframe: string }>(
      `/live/candles?symbol=${encodeURIComponent(symbol)}${timeframe ? `&timeframe=${timeframe}` : ""}`),
    refetchInterval: 30_000,
  });
  const fills: Fill[] = useMemo(() => (q.data?.fills ?? []).map((f) => ({
    time: f.time, price: f.price, side: f.side as "buy" | "sell", kind: "entry", from: 0, to: f.side === "buy" ? 1 : -1,
    label: `${f.side === "buy" ? "B" : "S"} ${f.qty}`,
  })), [q.data]);
  return (
    <>
      {q.isLoading && <Spinner label="Loading bars…" />}
      <ErrorBox error={q.error} />
      {q.data && <CandleChart candles={q.data.candles} fills={fills} priceLines={priceLines} height={height} />}
      {v && (v.trigger || v.peak) && <p className="text-[11.5px] text-muted mt-1">
        {q.data ? `${q.data.timeframe} bars. ` : ""}Dashed lines, judged at the next bar's close: green where the strategy buys, red where it sells
        {lv && [...lv.buy, ...lv.sell].length ? ` (${[...lv.buy, ...lv.sell].map((t) => triggerText(t)).join("; ")})` : ` (${triggerText(v.trigger, v.trigger_note)})`}.
        {v.peak && ` Dotted line: the best price since the bot opened the position; it has given back ${fmt(v.peak.gave_back_pct, 2)}%.`} Display only.</p>}
    </>
  );
}
