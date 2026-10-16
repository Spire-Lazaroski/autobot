import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Confirm } from "../components/Confirm";
import { ErrorBox, Panel } from "../components/ui";
import { api } from "../lib/api";

interface User { name: string; created: number; invited_by: string | null; note: string | null; disabled: number; last_login: number | null }
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
            <thead><tr className="text-muted text-[10.5px] uppercase text-left"><th>User</th><th>Note</th><th>Joined</th><th>Last sign-in</th><th>Status</th><th /></tr></thead>
            <tbody>{d.users.map((u) => (
              <tr key={u.name} className="border-t border-line/60">
                <td className="num font-semibold py-1">{u.name}</td><td className="text-muted">{u.note || "–"}</td>
                <td>{day(u.created)}</td><td>{day(u.last_login)}</td>
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
  const refresh = () => qc.invalidateQueries({ queryKey: ["alpaca-status"] });
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
        {step(2, false, "Choose your setup", <span className="text-[13px] text-muted">Careful, Balanced or Growth, each with its expected return and worst drop. Appears here next.</span>)}
        {step(3, false, "Start", <span className="text-[13px] text-muted">Your bot then trades your paper account every day; you follow it here and in the phone app.</span>)}
      </ol>
      <ErrorBox error={st.error ?? off.error} />
      {askOff && (
        <Confirm title="Disconnect your Alpaca paper account?" confirmLabel="Disconnect" danger busy={off.isPending}
          onCancel={() => setAskOff(false)} onConfirm={() => off.mutate()}>
          This app forgets its permission for your paper account. Nothing in your Alpaca account changes. To also remove the
          app on Alpaca's side, open Alpaca's dashboard and revoke it there.
        </Confirm>)}
    </div>
  );
}
