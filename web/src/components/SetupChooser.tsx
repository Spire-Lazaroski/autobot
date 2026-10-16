import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { Confirm } from "./Confirm";
import { ErrorBox, Panel } from "./ui";

interface Preset { label: string; about: string; core: { allocation_pct: number; min_pct: number; max_pct: number }; rotation: { allocation_pct: number } }
interface Stat { "yearly_%": number; "worst_drop_%": number; "worst_year_%": number; "avg_invested_%": number; from: string; to: string }
interface Resp {
  presets: Record<string, Preset>;
  current: string | null;
  study: null | { note: string; samples: Record<string, Record<string, Stat>> };
}

const ICON: Record<string, string> = { careful: "bi-shield-check", balanced: "bi-sliders", growth: "bi-graph-up-arrow" };

/** The three setups for the long-term holdings (autobot/presets.py): same mix, three sizes, each with its backtest.
 *  Choosing one changes only the core and rotation sizes in config.yaml. */
export function SetupChooser() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["setups"], queryFn: () => api<Resp>("/setups"), staleTime: 60_000 });
  const [ask, setAsk] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const choose = useMutation({
    mutationFn: (name: string) => api<{ message: string }>("/setups", { name, confirm: true }),
    onSuccess: (r) => { setMsg(r.message); qc.invalidateQueries({ queryKey: ["setups"] }); },
    onSettled: () => setAsk(null),
  });
  const d = q.data;
  if (!d) return null;
  const samples = Object.entries(d.study?.samples ?? {});
  return (
    <Panel title={<span className="inline-flex items-center gap-1.5"><i className="bi bi-ui-radios-grid text-muted" aria-hidden="true" />
      Setup · how much of the account the long-term holdings use</span>}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {Object.entries(d.presets).map(([name, p]) => {
          const on = d.current === name;
          return (
            <div key={name} className={`rounded-lg border p-3 flex flex-col gap-2 ${on ? "border-accent bg-[rgb(212_160_23/0.06)]" : "border-line"}`}>
              <div className="flex items-center gap-2">
                <i className={`bi ${ICON[name] ?? "bi-circle"} text-[16px]`} aria-hidden="true" />
                <span className="font-semibold text-[14px]">{p.label}</span>
                {on && <span className="chip border border-accent text-[10.5px] ml-auto">in use</span>}
              </div>
              <p className="text-[12px] text-muted">{p.about}</p>
              <p className="text-[12px]">Core {p.core.allocation_pct}% (moves {p.core.min_pct}-{p.core.max_pct}% with volatility) ·
                sector sleeve up to {p.rotation.allocation_pct}% · the rest cash</p>
              {samples.map(([label, rows]) => rows[name] && (
                <div key={label} className="text-[12px] border-t border-line/60 pt-1.5">
                  <div className="text-[10.5px] uppercase tracking-wide text-muted">{label}</div>
                  <div className="num">{fmt(rows[name]["yearly_%"], 1)}% a year · worst drop <span className="text-down">{fmt(rows[name]["worst_drop_%"], 0)}%</span>
                    {" "}· worst year {fmt(rows[name]["worst_year_%"], 0)}%</div>
                </div>))}
              <button className="btn mt-auto" disabled={on || choose.isPending} onClick={() => setAsk(name)}>
                {on ? "In use" : `Use ${p.label}`}</button>
            </div>);
        })}
      </div>
      {samples.length > 0 && (
        <p className="text-[11.5px] text-muted mt-2">
          For comparison, the market alone: {samples.map(([label, rows]) => rows.market &&
            `${label} ${fmt(rows.market["yearly_%"], 1)}% a year, worst drop ${fmt(rows.market["worst_drop_%"], 0)}%`).filter(Boolean).join("; ")}.
          {" "}{d.study?.note} The trading jobs are not part of these figures and are not changed by a setup.</p>)}
      {msg && <p className="text-[12px] text-up mt-2">{msg}</p>}
      <ErrorBox error={choose.error} />
      {ask && (
        <Confirm title={`Switch to ${d.presets[ask].label}?`} confirmLabel={`Use ${d.presets[ask].label}`} busy={choose.isPending}
          onCancel={() => setAsk(null)} onConfirm={() => choose.mutate(ask)}>
          The core moves to about <b className="text-fg">{d.presets[ask].core.allocation_pct}%</b> of the account
          ({d.presets[ask].core.min_pct}-{d.presets[ask].core.max_pct}% with volatility) and the sector sleeve to up
          to <b className="text-fg">{d.presets[ask].rotation.allocation_pct}%</b>. The bot buys or sells the difference at its next tick
          while the market is open (otherwise at the next open). Paused symbols stay paused.
        </Confirm>)}
    </Panel>
  );
}
