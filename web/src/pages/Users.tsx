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

/** What an invited user sees until their own Alpaca paper account is connected (the next step). */
export function Welcome({ name }: { name: string }) {
  return (
    <div className="max-w-[640px] mx-auto mt-8 panel p-6 flex flex-col gap-3">
      <h1 className="text-[20px] font-semibold" style={{ fontFamily: "Manrope, sans-serif" }}>Welcome, {name}</h1>
      <p className="text-[13.5px]">Your Spire Analytics account is ready. Your own paper-trading bot is being set up:</p>
      <ol className="list-decimal pl-5 text-[13.5px] flex flex-col gap-1.5">
        <li><b>Connect your Alpaca paper account</b>: you'll sign in on Alpaca's own page and allow the app to trade your
          paper account only. Your Alpaca password never comes here.</li>
        <li><b>Choose your setup</b>: Careful, Balanced or Growth, each with its expected return and worst drop.</li>
        <li><b>Start</b>: your bot then trades your paper account every day. Paper only, no real money.</li>
      </ol>
      <p className="text-[12.5px] text-muted">These steps appear here as soon as they are switched on. You can install the app on your phone from the header.</p>
    </div>
  );
}
