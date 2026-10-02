import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, useStrategies, type Params } from "../lib/api";
import { colorFor } from "../lib/format";
import { ParamInput } from "./ParamInput";
import { ErrorBox, Panel, Spinner } from "./ui";

interface Job {
  name: string;
  strategy: string;
  params: Params;
  symbols: string[];
  allocation_pct: number;
  enabled: boolean;
}
interface Live {
  timeframe: string;
  poll_seconds?: number;
  allow_short?: boolean;
  risk: { max_daily_loss_pct: number; max_position_usd: number; flatten_before_close_min: number };
  jobs: Job[];
  [k: string]: unknown;
}
interface Config {
  backtest: Record<string, unknown>;
  live: Live;
}

const TFS = ["1Min", "5Min", "15Min", "30Min", "1Hour", "1Day"];

const OPEN_KEY = "autobot.live.jobsOpen"; // per browser: is the editor unfolded

/** Edit config.yaml's live section. The running bot re-reads it at every bar. Folded by default
 *  (recalibration picks the jobs); folding only hides it, so unsaved edits are kept. */
export function JobsEditor({ botRunning }: { botRunning: boolean }) {
  const strategies = useStrategies();
  const qc = useQueryClient();
  const cfgQ = useQuery({ queryKey: ["config"], queryFn: () => api<Config>("/config") });
  const [draft, setDraft] = useState<Live | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [symText, setSymText] = useState<Record<number, string | undefined>>({}); // raw text while typing
  const [open, setOpenState] = useState<boolean>(() => {
    try { return localStorage.getItem(OPEN_KEY) === "1"; } catch { return false; }
  });
  const setOpen = (v: boolean) => {
    setOpenState(v);
    try { localStorage.setItem(OPEN_KEY, v ? "1" : "0"); } catch { /* private window: just not remembered */ }
  };

  useEffect(() => {
    if (cfgQ.data && !draft) setDraft(structuredClone(cfgQ.data.live));
  }, [cfgQ.data, draft]);

  if (!draft || !strategies.data) return <Panel title="Jobs"><Spinner label="Loading config…" /></Panel>;
  const specs = Object.fromEntries(strategies.data.map((s) => [s.name, s]));
  const dirty = JSON.stringify(draft) !== JSON.stringify(cfgQ.data?.live);
  const set = (patch: Partial<Live>) => { setDraft({ ...draft, ...patch }); setSaved(null); };
  const setJob = (i: number, patch: Partial<Job>) => set({ jobs: draft.jobs.map((j, k) => (k === i ? { ...j, ...patch } : j)) });
  const setRisk = (patch: Partial<Live["risk"]>) => set({ risk: { ...draft.risk, ...patch } });

  const enabledSyms = draft.jobs.filter((j) => j.enabled).flatMap((j) => j.symbols);
  const dupes = new Set(enabledSyms.filter((s, i) => enabledSyms.indexOf(s) !== i));

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      await api("/config", { ...cfgQ.data, live: draft }, "PUT");
      await qc.invalidateQueries({ queryKey: ["config"] });
      setSaved(botRunning ? "Saved. The bot applies it at its next bar, no restart; every change is logged." : "Saved. Takes effect when the bot starts.");
      setDraft(null); // reload from server
    } catch (e) {
      setErr(e);
    } finally {
      setSaving(false);
    }
  };

  const on = draft.jobs.filter((j) => j.enabled);
  const autoOn = on.filter((j) => j.name.startsWith("auto_")).length;
  const summary = `${on.length} on (${autoOn} auto, ${on.length - autoOn} hand-made) · ${draft.jobs.length - on.length} off · ${draft.timeframe} bars`;
  return (
    <Panel title={
      <button className="flex items-center gap-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="text-muted w-3">{open ? "▾" : "▸"}</span>
        <span>Jobs · config.yaml</span>
        <span className="text-[11.5px] font-normal normal-case tracking-normal text-muted">{summary}</span>
      </button>} right={
      <div className="flex items-center gap-2">
        {dirty && <span className="text-[11px] text-warn">unsaved changes</span>}
        {dirty && <button className="btn" onClick={() => { setDraft(structuredClone(cfgQ.data!.live)); setErr(null); }}>Discard</button>}
        <button className="btn btn-primary" onClick={save} disabled={!dirty || saving || dupes.size > 0}>{saving ? <Spinner /> : "💾"} Save</button>
      </div>}>
      <div className={open ? "" : "hidden"}>
      <div className="flex flex-wrap items-end gap-3 mb-3">
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-wider text-muted">Bar timeframe</span>
          <select className="input" value={draft.timeframe} onChange={(e) => set({ timeframe: e.target.value })}>{TFS.map((t) => <option key={t}>{t}</option>)}</select>
        </label>
        {([["max_daily_loss_pct", "Max daily loss %", 0.1], ["max_position_usd", "Max $ per symbol", 500], ["flatten_before_close_min", "Flat N min before close", 1]] as const).map(([k, label, step]) => (
          <label key={k} className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-muted">{label}</span>
            <input className="input w-28" type="number" step={step} min={0} value={draft.risk[k]}
              onChange={(e) => setRisk({ [k]: Number(e.target.value) || 0 })} />
          </label>
        ))}
        <label className="flex items-center gap-1.5 text-[12px] text-muted pb-1">
          <input type="checkbox" checked={!!draft.allow_short} onChange={(e) => set({ allow_short: e.target.checked })} /> allow short
        </label>
      </div>

      <div className="flex flex-col gap-2">
        {draft.jobs.map((j, i) => {
          const spec = specs[j.strategy];
          return (
            <div key={i} className={`panel p-2.5 bg-panel-2 ${j.enabled ? "" : "opacity-60"}`} style={{ borderLeft: `3px solid ${j.enabled ? colorFor(j.strategy) : "var(--line-strong)"}` }}>
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex items-center gap-1.5 text-[12px] pb-1.5 cursor-pointer">
                  <input type="checkbox" checked={j.enabled} onChange={(e) => setJob(i, { enabled: e.target.checked })} aria-label={`enable ${j.name}`} />
                  <span className={j.enabled ? "text-up" : "text-muted"}>{j.enabled ? "on" : "off"}</span>
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-muted">name</span>
                  <input className="input w-32" value={j.name} onChange={(e) => setJob(i, { name: e.target.value })} />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-muted">strategy</span>
                  <select className="input" value={j.strategy}
                    onChange={(e) => setJob(i, { strategy: e.target.value, params: structuredClone(specs[e.target.value]?.defaults ?? {}) })}>
                    {["benchmark", "trend", "mean_reversion", "breakout", "intraday", "meta"].map((fam) => (
                      <optgroup key={fam} label={fam.replace("_", " ")}>
                        {strategies.data!.filter((s) => s.family === fam).map((s) => <option key={s.name}>{s.name}</option>)}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-0.5 grow basis-40">
                  <span className="text-[10px] text-muted">symbols</span>
                  <input className={`input w-full ${j.symbols.some((s) => dupes.has(s)) && j.enabled ? "border-down" : ""}`}
                    value={symText[i] ?? j.symbols.join(", ")}
                    onChange={(e) => {
                      setSymText({ ...symText, [i]: e.target.value });
                      setJob(i, { symbols: e.target.value.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean) });
                    }}
                    onBlur={() => setSymText({ ...symText, [i]: undefined })} />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-muted">alloc % / symbol</span>
                  <input className="input w-20" type="number" min={1} max={100} value={j.allocation_pct}
                    onChange={(e) => setJob(i, { allocation_pct: Number(e.target.value) || 0 })} />
                </label>
                <button className="btn ml-auto" onClick={() => { setSymText({}); set({ jobs: draft.jobs.filter((_, k) => k !== i) }); }} aria-label={`remove ${j.name}`}>✕</button>
              </div>
              {spec && Object.keys(spec.defaults).length > 0 && (
                <div className="grid gap-1.5 mt-2 grid-cols-2 sm:grid-cols-4 lg:grid-cols-6">
                  {Object.entries(spec.defaults).map(([k, dv]) => (
                    <ParamInput key={`${j.strategy}.${k}`} name={k} value={j.params?.[k] ?? dv}
                      onChange={(v) => setJob(i, { params: { ...j.params, [k]: v } })} />
                  ))}
                </div>
              )}
              {spec?.family === "meta" && <p className="text-[11px] text-dim mt-1">Meta-strategy: the status table shows which strategy is active per symbol.</p>}
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-3 mt-2 flex-wrap">
        <button className="btn" onClick={() => set({ jobs: [...draft.jobs, { name: `job_${draft.jobs.length + 1}`, strategy: "ema_cross", params: structuredClone(specs.ema_cross?.defaults ?? {}), symbols: [], allocation_pct: 10, enabled: false }] })}>＋ Add job</button>
        {dupes.size > 0 && <span className="text-[12px] text-down">In more than one enabled job: {[...dupes].join(", ")}. Alpaca nets positions per symbol, so one job per symbol.</span>}
        {saved && <span className="text-[12px] text-up">{saved}</span>}
      </div>
      <div className="mt-2"><ErrorBox error={err} /></div>
      <p className="text-[11px] text-dim mt-2">
        The server checks the same rules as the bot before saving. A symbol removed from all enabled jobs gets its position closed at the bot's next bar.
      </p>
      </div>
    </Panel>
  );
}
