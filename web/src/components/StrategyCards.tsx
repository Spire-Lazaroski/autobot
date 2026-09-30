import { useMemo, useState } from "react";
import type { StrategyInfo } from "../lib/api";
import { colorFor, FAMILY_ICON } from "../lib/format";
import type { Picked } from "../lib/settings";
import { ParamInput } from "./ParamInput";

export const pickedOf = (s: StrategyInfo, picked: Record<string, Picked>): Picked =>
  picked[s.name] ?? { on: true, params: s.defaults };

/** Toggle + parameter card for every strategy, filterable by family. */
export function StrategyCards({ strategies, picked, onChange }: {
  strategies: StrategyInfo[];
  picked: Record<string, Picked>;
  onChange: (next: Record<string, Picked>) => void;
}) {
  const families = useMemo(() => ["all", ...new Set(strategies.map((s) => s.family))], [strategies]);
  const [fam, setFam] = useState("all");
  const get = (s: StrategyInfo) => pickedOf(s, picked);
  const all = () => Object.fromEntries(strategies.map((s) => [s.name, get(s)]));
  const update = (name: string, p: Picked) => onChange({ ...all(), [name]: p });
  const setAll = (on: boolean) =>
    onChange(Object.fromEntries(strategies.map((s) => [s.name, { ...get(s), on: fam === "all" || s.family === fam ? on : get(s).on }])));
  const shown = strategies.filter((s) => fam === "all" || s.family === fam);
  const count = strategies.filter((s) => get(s).on).length;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {families.map((f) => (
          <button key={f} onClick={() => setFam(f)}
            className={`chip border ${fam === f ? "border-accent text-accent bg-accent/10" : "border-line-2 text-muted hover:text-fg"}`}>
            {FAMILY_ICON[f] ?? ""} {f.replace("_", " ")}
          </button>
        ))}
        <span className="ml-auto text-[11px] text-muted num">{count} on</span>
        <button className="chip border border-line-2 text-muted hover:text-fg" onClick={() => setAll(true)}>all</button>
        <button className="chip border border-line-2 text-muted hover:text-fg" onClick={() => setAll(false)}>none</button>
      </div>
      <div className="grid gap-2 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {shown.map((s) => {
          const p = get(s);
          const changed = JSON.stringify(p.params) !== JSON.stringify(s.defaults);
          return (
            <div key={s.name} className={`panel p-2.5 transition-opacity ${p.on ? "" : "opacity-50"}`}
              style={{ borderLeft: `3px solid ${p.on ? colorFor(s.name) : "#2a3444"}` }}>
              <div className="flex items-start gap-2">
                <input type="checkbox" className="mt-0.5" checked={p.on} aria-label={`use ${s.name}`}
                  onChange={(e) => update(s.name, { ...p, on: e.target.checked })} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-semibold num text-[12px] truncate">{s.name}</span>
                    <span className="chip bg-line text-muted">{FAMILY_ICON[s.family]} {s.family.replace("_", " ")}</span>
                  </div>
                  <p className="text-[11px] text-muted leading-snug mt-0.5">{s.description}</p>
                  {s.source && <p className="text-[10px] text-dim italic mt-0.5 truncate" title={s.source}>{s.source}</p>}
                </div>
                {changed && (
                  <button className="text-[10px] text-muted hover:text-accent" title="Reset to defaults"
                    onClick={() => update(s.name, { ...p, params: s.defaults })}>reset</button>
                )}
              </div>
              {Object.keys(s.defaults).length > 0 && (
                <div className="grid grid-cols-3 gap-1.5 mt-2">
                  {Object.entries(s.defaults).map(([k, dv]) => (
                    <ParamInput key={k} name={k} value={p.params[k] ?? dv} disabled={!p.on}
                      onChange={(v) => update(s.name, { ...p, params: { ...p.params, [k]: v } })} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
