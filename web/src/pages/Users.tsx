import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Confirm } from "../components/Confirm";
import { ErrorBox, Panel } from "../components/ui";
import { api } from "../lib/api";
import { fmt, signed } from "../lib/format";

interface User { name: string; created: number; invited_by: string | null; note: string | null; disabled: number; last_login: number | null;
  setup?: string | null; jobs_on?: boolean | null; connected?: boolean; bot_running?: boolean }
interface Invite { id: number; note: string | null; created: number; expires: number; used_by: string | null; used_at: number | null; state: string }
interface Resp { users: User[]; invites: Invite[]; invite_days: number }

const day = (t: number | null) => (t ? new Date(t * 1000).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
const TONE: Record<string, string> = { open: "text-up", used: "text-muted", expired: "text-muted", revoked: "text-down" };

/** Admin only: invite people (a link valid for 7 days, used once), see who joined, switch a user off. */
export function Users() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: () => api<Resp>("/admin/users") });
  const [note, setNote] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [askOff, setAskOff] = useState<User | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-users"] });
  const invite = useMutation({
    mutationFn: () => api<{ token: string }>("/admin/invites", { note }),
    onSuccess: (r) => { setLink(`${window.location.origin}/login?invite=${r.token}`); setCopied(false); setNote(""); refresh(); },
  });
  const revoke = useMutation({ mutationFn: (id: number) => api(`/admin/invites/${id}`, undefined, "DELETE"), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: (u: User) => api(`/admin/users/${encodeURIComponent(u.name)}/disable`, { disabled: !u.disabled }),
    onSuccess: refresh, onSettled: () => setAskOff(null),
  });
  const d = q.data;
  return (
    <div className="flex flex-col gap-4 max-w-[1100px]">
      <Panel title={<span className="inline-flex items-center gap-1.5"><i className="bi bi-person-plus text-muted" aria-hidden="true" />Invite someone</span>}>
        <p className="text-[12.5px] text-muted mb-2">
          Make a link and send it to the person yourself (message or email). It works once, for {d?.invite_days ?? 7} days.
          They choose their own user name and password. Until their own Alpaca paper account is connected they see a welcome
          page only, never your account.</p>
        <div className="flex flex-wrap gap-2 items-center">
          <input className="input w-64" placeholder="Who is it for? (your note)" value={note} maxLength={80} onChange={(e) => setNote(e.target.value)} />
          <button className="btn btn-primary" disabled={invite.isPending} onClick={() => invite.mutate()}>
            <i className="bi bi-link-45deg" aria-hidden="true" /> Create invite link</button>
        </div>
        {link && (
          <div className="mt-3 p-2.5 rounded-lg border border-accent text-[12.5px] flex flex-col gap-1.5">
            <span className="font-semibold">Send this link (it is shown only now):</span>
            <code className="num break-all text-[12px]">{link}</code>
            <div><button className="btn" onClick={async () => { await navigator.clipboard.writeText(link); setCopied(true); }}>
              <i className={`bi ${copied ? "bi-check2" : "bi-clipboard"}`} aria-hidden="true" /> {copied ? "Copied" : "Copy link"}</button></div>
          </div>)}
        <ErrorBox error={invite.error} />
      </Panel>

      <Panel title={<span className="inline-flex items-center gap-1.5"><i className="bi bi-people text-muted" aria-hidden="true" />Users</span>}>
        {d && d.users.length === 0 && <p className="text-[12.5px] text-muted">Nobody has joined yet.</p>}
        {d && d.users.length > 0 && (
          <table className="w-full text-[12.5px]">
            <thead><tr className="text-muted text-[10.5px] uppercase text-left"><th>User</th><th>Note</th><th>Joined</th><th>Last sign-in</th><th>Alpaca</th><th>Setup</th><th>Bot</th><th>Status</th><th /></tr></thead>
            <tbody>{d.users.map((u) => (
              <tr key={u.name} className="border-t border-line/60">
                <td className="num font-semibold py-1">{u.name}</td><td className="text-muted">{u.note || "–"}</td>
                <td>{day(u.created)}</td><td>{day(u.last_login)}</td>
                <td>{u.connected ? "connected" : "–"}</td>
                <td>{u.setup ? `${u.setup}${u.jobs_on ? " + jobs" : ""}` : "–"}</td>
                <td className={u.bot_running ? "text-up" : "text-muted"}>{u.bot_running ? "running" : "stopped"}</td>
                <td className={u.disabled ? "text-down" : "text-up"}>{u.disabled ? "switched off" : "active"}</td>
                <td className="text-right"><button className="btn" onClick={() => (u.disabled ? toggle.mutate(u) : setAskOff(u))}>
                  {u.disabled ? "Switch on" : "Switch off"}</button></td>
              </tr>))}</tbody>
          </table>)}
        <ErrorBox error={toggle.error} />
      </Panel>

      <Panel title={<span className="inline-flex items-center gap-1.5"><i className="bi bi-envelope text-muted" aria-hidden="true" />Invites</span>}>
        {d && d.invites.length === 0 && <p className="text-[12.5px] text-muted">No invites yet.</p>}
        {d && d.invites.length > 0 && (
          <table className="w-full text-[12.5px]">
            <thead><tr className="text-muted text-[10.5px] uppercase text-left"><th>#</th><th>Note</th><th>Made</th><th>Valid until</th><th>State</th><th /></tr></thead>
            <tbody>{d.invites.map((i) => (
              <tr key={i.id} className="border-t border-line/60">
                <td className="num py-1">{i.id}</td><td>{i.note || "–"}</td><td>{day(i.created)}</td><td>{day(i.expires)}</td>
                <td className={TONE[i.state] ?? ""}>{i.state}{i.used_by ? ` by ${i.used_by}` : ""}</td>
                <td className="text-right">{i.state === "open" && <button className="btn" onClick={() => revoke.mutate(i.id)}>Cancel</button>}</td>
              </tr>))}</tbody>
          </table>)}
        <ErrorBox error={revoke.error ?? q.error} />
      </Panel>

      {askOff && (
        <Confirm title={`Switch off ${askOff.name}?`} confirmLabel="Switch off" danger busy={toggle.isPending}
          onCancel={() => setAskOff(null)} onConfirm={() => toggle.mutate(askOff)}>
          {askOff.name} is signed out at their next request and can't sign in until you switch them on again. Nothing is deleted.
        </Confirm>)}
    </div>
  );
}

interface AlpacaStatus { connected: boolean; method?: string; account_tail?: string; since?: number; oauth_available: boolean }

/** An invited user's home until their own bot runs: connect the Alpaca paper account (Alpaca's own sign-in, or a
 *  paper key pair), then the next steps. Never shows anything of the lab owner's account. */
export function Welcome({ name }: { name: string }) {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["alpaca-status"], queryFn: () => api<AlpacaStatus>("/alpaca/status") });
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [askOff, setAskOff] = useState(false);
  const back = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(
    back.get("alpaca") === "connected" ? { ok: true, text: "Your Alpaca paper account is connected." }
      : back.get("alpaca_error") ? { ok: false, text: back.get("alpaca_error")! } : null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["alpaca-status"] }); qc.invalidateQueries({ queryKey: ["my-bot"] }); };
  const my = useQuery({ queryKey: ["my-bot"], queryFn: () => api<MyBot>("/my/bot"), refetchInterval: 15_000 });
  const refreshMy = () => qc.invalidateQueries({ queryKey: ["my-bot"] });
  const keys = useMutation({
    mutationFn: () => api("/alpaca/keys", { key_id: keyId, secret_key: secret }),
    onSuccess: () => { setKeyId(""); setSecret(""); setNotice({ ok: true, text: "Your Alpaca paper account is connected." }); refresh(); },
  });
  const off = useMutation({ mutationFn: () => api("/alpaca/disconnect", {}), onSuccess: () => { setNotice(null); refresh(); }, onSettled: () => setAskOff(false) });
  const s = st.data;
  const step = (n: number, done: boolean, title: string, body: React.ReactNode) => (
    <li className="flex gap-3">
      <span className={`shrink-0 h-6 w-6 rounded-full grid place-items-center text-[12px] font-semibold ${done ? "bg-up text-white" : "border border-line-2 text-muted"}`}>
        {done ? <i className="bi bi-check-lg" /> : n}</span>
      <div className="flex flex-col gap-1.5 min-w-0"><b className="text-[14px]">{title}</b>{body}</div>
    </li>);
  return (
    <div className="max-w-[680px] mx-auto mt-6 panel p-6 flex flex-col gap-4">
      <h1 className="text-[20px] font-semibold" style={{ fontFamily: "Manrope, sans-serif" }}>Welcome, {name}</h1>
      <p className="text-[13.5px] text-muted">Your own paper-trading bot, in three steps. Paper only: no real money is ever used.</p>
      {notice && <p className={`text-[13px] ${notice.ok ? "text-up" : "text-down"}`}>{notice.text}</p>}
      <ol className="flex flex-col gap-4">
        {step(1, !!s?.connected, "Connect your Alpaca paper account", s?.connected ? (
          <div className="text-[13px] flex flex-wrap items-center gap-2">
            <span>Connected: paper account ···{s.account_tail || "?"} ({s.method === "oauth" ? "through Alpaca" : "with your paper keys"}).</span>
            <button className="btn" onClick={() => setAskOff(true)}>Disconnect</button>
          </div>) : (
          <div className="flex flex-col gap-2 text-[13px]">
            {s?.oauth_available && (<>
              <span>You sign in on Alpaca's own page and allow this app to trade <b>one paper account</b>. Your Alpaca password never comes here.</span>
              <AlpacaDisclosure />
              <div><a className="btn btn-primary" href="/api/alpaca/connect"><i className="bi bi-link-45deg" aria-hidden="true" /> Connect with Alpaca</a></div>
            </>)}
            <details open={!s?.oauth_available} className="text-[12.5px]">
              <summary className="cursor-pointer text-muted">{s?.oauth_available ? "Or use your paper API keys" : "Use your paper API keys"}</summary>
              <p className="text-muted mt-1">Alpaca dashboard → switch to <b>Paper Trading</b> → API Keys → Generate. The key id starts with PK.
                They are tested on Alpaca's paper service and kept encrypted.</p>
              <div className="flex flex-col gap-2 mt-2 max-w-[420px]">
                <input className="input" placeholder="Key id (PK…)" autoComplete="off" spellCheck={false} value={keyId} onChange={(e) => setKeyId(e.target.value)} />
                <input className="input" placeholder="Secret key" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} />
                <div><button className="btn" disabled={!keyId || !secret || keys.isPending} onClick={() => keys.mutate()}>Connect with keys</button></div>
                <ErrorBox error={keys.error} />
              </div>
            </details>
          </div>))}
        {step(2, !!my.data?.settings, "Choose your setup", <MySetup data={my.data} onSaved={refreshMy} />)}
        {step(3, !!my.data?.bot.running, "Start", <MyStartStop data={my.data} onDone={refreshMy} />)}
      </ol>
      {my.data?.connected && <MyAccount data={my.data} />}
      <ErrorBox error={st.error ?? off.error ?? my.error} />
      {askOff && (
        <Confirm title="Disconnect your Alpaca paper account?" confirmLabel="Disconnect" danger busy={off.isPending}
          onCancel={() => setAskOff(false)} onConfirm={() => off.mutate()}>
          This app forgets its permission for your paper account. Nothing in your Alpaca account changes. To also remove the
          app on Alpaca's side, open Alpaca's dashboard and revoke it there.
        </Confirm>)}
    </div>
  );
}

/** The disclosure Alpaca requires at the moment of connecting (Alpaca Connect app registration), word for word. */
function AlpacaDisclosure() {
  return (
    <div id="alpaca-disclosure" className="rounded-lg border border-line-2 bg-bg p-3 text-[12.5px] leading-relaxed">
      <b className="block mb-1 text-[13px]">Authorize Spire Analytics</b>
      By allowing Spire Analytics to access your Alpaca account, you are granting Spire Analytics access to your account
      information and authorization to place transactions in your account at your direction. Alpaca does not warrant or
      guarantee that Spire Analytics will work as advertised or expected. Before authorizing,{" "}
      <a className="underline" href="/terms" target="_blank" rel="noopener">learn more about Spire Analytics</a>.
    </div>
  );
}

interface PresetInfo { label: string; about: string; core: { allocation_pct: number; min_pct: number; max_pct: number }; rotation: { allocation_pct: number } }
interface MyBot {
  user: string; connected: boolean;
  settings: null | { setup: string; jobs_on: boolean; want_running: boolean };
  presets: Record<string, PresetInfo>;
  study: null | { samples: Record<string, Record<string, { "yearly_%": number; "worst_drop_%": number }>> };
  bot: { running: boolean; unresponsive: boolean; stop_requested?: boolean; heartbeat_age_s?: number };
  account: null | { ok: boolean; equity?: number; cash?: number; day_pnl?: number; day_pnl_pct?: number; error?: string;
    positions?: { symbol: string; qty: number; market_value: number; unrealized_pl: number; unrealized_plpc: number }[] };
  trades: Record<string, string>[]; log: string[];
}

/** Step 2: the three setups (the same as the lab's, autobot/presets.py) and trading jobs on or off. */
function MySetup({ data, onSaved }: { data?: MyBot; onSaved: () => void }) {
  const [pick, setPick] = useState<string | null>(null);
  const [jobs, setJobs] = useState<boolean | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (b: { setup: string; jobs_on: boolean }) => api<{ message: string }>("/my/setup", b),
    onSuccess: (r) => { setMsg(r.message); onSaved(); },
  });
  if (!data) return null;
  if (!data.connected) return <span className="text-[13px] text-muted">After step 1.</span>;
  const cur = pick ?? data.settings?.setup ?? "balanced";
  const jobsOn = jobs ?? data.settings?.jobs_on ?? false;
  const samples = Object.entries(data.study?.samples ?? {});
  const changed = !data.settings || cur !== data.settings.setup || jobsOn !== data.settings.jobs_on;
  return (
    <div className="flex flex-col gap-2 text-[13px]">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {Object.entries(data.presets).map(([k, p]) => (
          <button key={k} onClick={() => setPick(k)}
            className={`text-left rounded-lg border p-2.5 flex flex-col gap-1 ${cur === k ? "border-accent bg-[rgb(212_160_23/0.08)]" : "border-line"}`}>
            <b>{p.label}{data.settings?.setup === k ? " · in use" : ""}</b>
            <span className="text-[12px] text-muted">{p.about}</span>
            {samples.map(([label, rows]) => rows[k] && (
              <span key={label} className="num text-[11.5px]">{label.replace(/^.* (\d{4}-\d{4})$/, "$1")}: {fmt(rows[k]["yearly_%"], 1)}%/yr,
                worst drop {fmt(rows[k]["worst_drop_%"], 0)}%</span>))}
          </button>))}
      </div>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={jobsOn} onChange={(e) => setJobs(e.target.checked)} />
        Also run the short-term trading jobs (they buy and sell during the day and close by 15:55 New York; our tests found
        no edge after costs, so they are off by default)
      </label>
      <div className="flex items-center gap-2">
        <button className="btn btn-primary" disabled={!changed || save.isPending} onClick={() => save.mutate({ setup: cur, jobs_on: jobsOn })}>
          {data.settings ? "Save changes" : "Use this setup"}</button>
        {msg && <span className="text-up text-[12.5px]">{msg}</span>}
      </div>
      <p className="text-[11.5px] text-muted">Past results from backtests, no promise of future ones. Paper trading only.</p>
      <ErrorBox error={save.error} />
    </div>
  );
}

/** Step 3: the user's own Start and Stop. */
function MyStartStop({ data, onDone }: { data?: MyBot; onDone: () => void }) {
  const [ask, setAsk] = useState(false);
  const [sellAll, setSellAll] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const start = useMutation({ mutationFn: () => api<{ message: string }>("/my/start", {}), onSuccess: (r) => { setMsg(r.message); onDone(); } });
  const stop = useMutation({
    mutationFn: () => api<{ message: string }>("/my/stop", { confirm: true, include_core: sellAll }),
    onSuccess: (r) => { setMsg(r.message); onDone(); }, onSettled: () => { setAsk(false); setSellAll(false); },
  });
  if (!data) return null;
  if (!data.connected || !data.settings) return <span className="text-[13px] text-muted">After steps 1 and 2.</span>;
  const b = data.bot;
  return (
    <div className="flex flex-col gap-2 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5">
          <span className={`h-2.5 w-2.5 rounded-full ${b.running ? "bg-up animate-pulse" : "bg-dim"}`} />
          <b>{b.running ? (b.stop_requested ? "stopping…" : "your bot is running") : "your bot is stopped"}</b></span>
        <button className="btn btn-primary" disabled={b.running || start.isPending} onClick={() => start.mutate()}>▶ Start</button>
        <button className="btn btn-danger" disabled={!b.running || !!b.stop_requested || stop.isPending} onClick={() => setAsk(true)}>⏹ Stop</button>
      </div>
      <span className="text-[12px] text-muted">It trades while the US market is open (15:30-22:00 your time) and keeps running day after day.</span>
      {msg && <span className="text-up text-[12.5px]">{msg}</span>}
      <ErrorBox error={start.error ?? stop.error} />
      {ask && (
        <Confirm title="Stop your bot?" confirmLabel="Stop" danger busy={stop.isPending} onCancel={() => setAsk(false)} onConfirm={() => stop.mutate()}>
          Your bot sells the trading jobs' positions and stops. The long-term holdings (the S&amp;P 500 fund and the sector
          funds) are kept unless you tick the box.
          <label className="flex items-center gap-2 mt-3 text-fg">
            <input type="checkbox" checked={sellAll} onChange={(e) => setSellAll(e.target.checked)} /> Also sell the long-term holdings
          </label>
        </Confirm>)}
    </div>
  );
}

/** The user's own paper account: value, today's change, holdings, the bot's latest orders. */
function MyAccount({ data }: { data: MyBot }) {
  const a = data.account;
  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      <h2 className="text-[15px] font-semibold">Your paper account</h2>
      {a && !a.ok && <p className="text-[12.5px] text-down">The account could not be read just now{a.error ? ` (${a.error})` : ""}.</p>}
      {a?.ok && (<>
        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-lg border border-line p-2.5"><div className="text-[10.5px] uppercase text-muted">Value</div>
            <div className="num text-[18px] font-semibold">${fmt(a.equity, 0)}</div></div>
          <div className="rounded-lg border border-line p-2.5"><div className="text-[10.5px] uppercase text-muted">Today</div>
            <div className={`num text-[18px] font-semibold ${(a.day_pnl ?? 0) < 0 ? "text-down" : "text-up"}`}>{signed(a.day_pnl ?? 0)}</div>
            <div className="num text-[11px] text-muted">{signed(a.day_pnl_pct ?? 0, 3)}%</div></div>
          <div className="rounded-lg border border-line p-2.5"><div className="text-[10.5px] uppercase text-muted">Cash</div>
            <div className="num text-[18px] font-semibold">${fmt(a.cash, 0)}</div></div>
        </div>
        {a.positions && a.positions.length > 0 ? (
          <table className="w-full text-[12.5px]">
            <thead><tr className="text-muted text-[10.5px] uppercase text-left"><th>Holding</th><th className="text-right">Qty</th>
              <th className="text-right">Value</th><th className="text-right">P&amp;L</th></tr></thead>
            <tbody>{a.positions.map((p) => (
              <tr key={p.symbol} className="border-t border-line/60"><td className="num font-semibold py-1">{p.symbol}</td>
                <td className="num text-right">{fmt(p.qty, 0)}</td><td className="num text-right">${fmt(p.market_value, 0)}</td>
                <td className={`num text-right ${p.unrealized_pl < 0 ? "text-down" : "text-up"}`}>{signed(p.unrealized_plpc, 2)}%</td></tr>))}</tbody>
          </table>) : <p className="text-[12.5px] text-muted">No holdings yet: the bot buys at the next market open after you start it.</p>}
      </>)}
      {data.trades.length > 0 && (
        <details className="text-[12.5px]"><summary className="cursor-pointer text-muted">Your bot's latest orders ({data.trades.length})</summary>
          <table className="w-full mt-1"><tbody>{data.trades.map((t, i) => (
            <tr key={i} className="border-t border-line/60"><td className="num py-0.5 whitespace-nowrap">{(t.time ?? "").slice(0, 16).replace("T", " ")}</td>
              <td className="num">{t.side} {t.qty} {t.symbol}</td><td className="text-muted">{t.reason}</td></tr>))}</tbody></table>
        </details>)}
    </div>
  );
}
