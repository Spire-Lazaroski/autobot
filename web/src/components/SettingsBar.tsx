import type { ReactNode } from "react";
import { useHealth } from "../lib/api";
import { useSettings } from "../lib/settings";

const TFS = ["1Min", "5Min", "15Min", "30Min", "1Hour", "1Day"];

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`flex flex-col gap-0.5 ${className}`}>
      <span className="text-[10px] uppercase tracking-wider text-muted">{label}</span>
      {children}
    </label>
  );
}

/** Data + cost settings shared by every page. `action` is the page's run button. */
export function SettingsBar({ action, extra }: { action?: ReactNode; extra?: ReactNode }) {
  const { s, set } = useSettings();
  const health = useHealth();
  const noKeys = health.data && !health.data.alpaca_keys;
  return (
    <div className="panel p-2.5 flex flex-wrap items-end gap-x-3 gap-y-2">
      <Field label="Data">
        <div className="flex rounded border border-line-2 overflow-hidden">
          {(["demo", "alpaca"] as const).map((src) => (
            <button key={src} onClick={() => set({ source: src })} disabled={src === "alpaca" && noKeys}
              title={src === "alpaca" && noKeys ? "No Alpaca keys in .env" : undefined}
              className={`px-2.5 py-1 text-[12px] font-semibold disabled:opacity-40 ${s.source === src ? (src === "demo" ? "bg-warn/20 text-warn" : "bg-up/20 text-up") : "text-muted hover:text-fg"}`}>
              {src === "demo" ? "Demo" : "Alpaca"}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Symbols" className="grow min-w-[180px] basis-[220px]">
        <input className="input w-full" value={s.symbols} onChange={(e) => set({ symbols: e.target.value })}
          placeholder="SPY, QQQ, BTC/USD" />
      </Field>
      <Field label="Bars">
        <select className="input" value={s.timeframe} onChange={(e) => set({ timeframe: e.target.value })}>
          {TFS.map((t) => <option key={t}>{t}</option>)}
        </select>
      </Field>
      <Field label="Days">
        <input className="input w-20" type="number" min={2} max={3650} value={s.days}
          onChange={(e) => set({ days: Math.max(2, Number(e.target.value) || 2) })} />
      </Field>
      <Field label="Fee bps">
        <input className="input w-16" type="number" step={0.5} min={0} value={s.fee_bps}
          onChange={(e) => set({ fee_bps: Number(e.target.value) || 0 })} />
      </Field>
      <Field label="Slip bps">
        <input className="input w-16" type="number" step={0.5} min={0} value={s.slippage_bps}
          onChange={(e) => set({ slippage_bps: Number(e.target.value) || 0 })} />
      </Field>
      <div className="flex gap-3 pb-1">
        <label className="flex items-center gap-1.5 text-[12px] text-muted cursor-pointer">
          <input type="checkbox" checked={s.rth_only} onChange={(e) => set({ rth_only: e.target.checked })} /> RTH
        </label>
        <label className="flex items-center gap-1.5 text-[12px] text-muted cursor-pointer">
          <input type="checkbox" checked={s.allow_short} onChange={(e) => set({ allow_short: e.target.checked })} /> Short
        </label>
      </div>
      {extra}
      <div className="ml-auto flex gap-2">{action}</div>
      {s.source === "demo" && (
        <div className="basis-full text-[11px] text-warn">
          Demo data is synthetic: it tests the plumbing, not which strategy works.
        </div>
      )}
    </div>
  );
}
