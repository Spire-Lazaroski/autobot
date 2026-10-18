import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Plot } from "../components/Plot";
import { ErrorBox, Kpi, Panel, Spinner } from "../components/ui";
import { api, useJob, type Job, useMe } from "../lib/api";
import { C } from "../lib/chart";
import { fmt } from "../lib/format";

interface Cell { n?: number; mean: number | null; lo: number | null; hi: number | null }
interface Group { group: string; n: number; days: number; low: boolean; horizons: Record<string, Cell> }
interface VolGroup { group: string; n: number; days: number; low: boolean; ratio: Record<string, Cell> }
interface Impact {
  scorer: string; version: string; generated: string; period: [string, string]; min_events: number; horizons: string[];
  counts: Record<string, number>;
  news: Record<string, Group[]>;
  volatility: Record<string, VolGroup[]>;
  market: { baseline_pct: Record<string, number>; by_category: Group[]; by_sentiment_signed: Group[] };
  spikes: { by_news: Group[]; by_symbol_no_news: Group[]; continue_vs_revert: Record<string, any>[] };
}

const HLABEL: Record<string, string> = { "5m": "+5 min", "15m": "+15 min", "1h": "+1 h", eod: "close", "1d": "+1 day", "3d": "+3 days" };
const GROUPINGS: { key: string; label: string; src: (d: Impact) => Group[]; signed?: boolean; note: string }[] = [
  { key: "by_first", label: "First story vs follow-up", src: (d) => d.news.by_first, note: "Stocks vs SPY, minus each stock's normal return." },
  { key: "by_category", label: "Category", src: (d) => d.news.by_category, note: "Stocks vs SPY, minus each stock's normal return." },
  { key: "by_materiality", label: "Materiality", src: (d) => d.news.by_materiality, note: "Stocks vs SPY, minus each stock's normal return." },
  { key: "by_story_type", label: "Story type", src: (d) => d.news.by_story_type, note: "Stocks vs SPY, minus each stock's normal return." },
  { key: "by_sentiment_signed", label: "Sentiment (signed)", src: (d) => d.news.by_sentiment_signed, signed: true, note: "Signed by the story's sentiment: above zero = the stock moved the way the news pointed." },
  { key: "by_when", label: "During / outside hours", src: (d) => d.news.by_when, note: "Outside-hours stories are entered at the next open." },
  { key: "market_sentiment", label: "Market news (SPY)", src: (d) => d.market.by_sentiment_signed, signed: true, note: "SPY minus its normal drift, signed by sentiment." },
];
const sig = (c?: Cell, zero = 0) => !!c && c.lo !== null && c.hi !== null && (c.lo > zero || c.hi < zero);

export function NewsImpact() {
  const me = useMe();   // invited users read the news; running studies stays the lab's
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["news-impact"], queryFn: () => api<Impact>("/news/impact"), retry: false });
  const [grouping, setGrouping] = useState(GROUPINGS[0].key);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob<{ counts: Record<string, number> }>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");
  useEffect(() => { if (job.data?.status === "done") qc.invalidateQueries({ queryKey: ["news-impact"] }); }, [job.data?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const recompute = async () => setJobId((await api<Job>("/news/impact", { scorer: "rules" })).id);
  const d = q.data;

  const chance = useMemo(() => {
    if (!d) return null;
    let s = 0, t = 0;
    for (const g of [...Object.values(d.news).flat(), ...d.market.by_sentiment_signed, ...d.spikes.by_news])
      for (const h of ["15m", "1h", "eod", "1d", "3d"]) { const c = g.horizons[h]; if (c && c.lo !== null) { t++; if (sig(c)) s++; } }
    return { s, t };
  }, [d]);

  return (
    <Panel title="Market impact of news (event study)" right={
      me.data?.role !== "user" ? <button className="btn" onClick={recompute} disabled={!!busy}>{busy ? <Spinner /> : <i className="bi bi-arrow-repeat" />} Recompute</button> : null}>
      {busy && <p className="text-[12px] text-muted mb-2">{job.data?.message}…</p>}
      {q.isLoading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {d && (
        <div className="flex flex-col gap-3">
          <p className="text-[12px] text-muted">
            {d.period[0]} to {d.period[1]} · scores from the <b>{d.scorer === "rules" ? `keyword rules (${d.version})` : `Claude (${d.version})`}</b> ·
            entry on the first 5-minute bar after the story arrived (no look-ahead) · stories on the same symbol and day count once ·
            groups with fewer than {d.min_events} days are marked <span className="chip chip-warn">few days</span>
          </p>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
            <Kpi label="Stock reactions measured" value={fmt(d.counts.stock_scores_main_or_secondary, 0)} sub="stories, main or secondary subject" />
            <Kpi label="Market (SPY) reactions" value={fmt(d.counts.spy_scores_main_or_secondary, 0)} sub="vs SPY's normal drift" />
            <Kpi label="Big 15-min moves" value={fmt(d.counts.spikes, 0)} sub={`${fmt(d.counts.spikes_no_news, 0)} with no news`} />
            <Kpi label="Results beyond chance" value={chance ? `${chance.s} of ${chance.t}` : "–"} sub={chance ? `about ${Math.round(chance.t * 0.05)} expected by luck alone` : ""}
              tone={chance && chance.s <= chance.t * 0.075 ? "text-warn" : "text-fg"} />
            <Kpi label="Scorer" value={d.scorer === "rules" ? "keywords" : "Claude"} sub={d.scorer === "rules" ? "rough; see the Scores panel" : d.version} />
          </div>

          <div className="note-box rounded-lg border border-line bg-panel-2 px-3 py-2 text-[12.5px] leading-relaxed">
            <b>How to read this.</b> A curve near zero with a band crossing zero means no measurable reaction after we could act.
            Only trust a result whose band clearly excludes zero <i>and</i> that rests on many days; with this many groups, a few will look
            "significant" by chance alone (see the count above).
          </div>

          <ReactionCurves d={d} grouping={grouping} setGrouping={setGrouping} />
          <VolatilityPanel d={d} />
          <SpikesPanel d={d} />
        </div>
      )}
    </Panel>
  );
}

function ReactionCurves({ d, grouping, setGrouping }: { d: Impact; grouping: string; setGrouping: (g: string) => void }) {
  const g = GROUPINGS.find((x) => x.key === grouping)!;
  const rows = g.src(d);
  const hs = d.horizons;
  const traces = rows.map((r, i) => ({
    x: hs.map((h) => HLABEL[h]), y: hs.map((h) => r.horizons[h]?.mean ?? null), name: `${r.group} (${r.days} d)`, type: "scatter", mode: "lines+markers",
    line: { color: C.series[i % C.series.length], width: r.low ? 1 : 2, dash: r.low ? "dot" : "solid" },
    error_y: { type: "data", symmetric: false, visible: true, thickness: 1, width: 3,
      array: hs.map((h) => { const c = r.horizons[h]; return c?.hi != null && c.mean != null ? c.hi - c.mean : null; }),
      arrayminus: hs.map((h) => { const c = r.horizons[h]; return c?.lo != null && c.mean != null ? c.mean - c.lo : null; }) },
    hovertemplate: `${r.group}: %{y:.3f}%<extra></extra>`,
  }));
  return (
    <section>
      <div className="flex flex-wrap items-center gap-1.5 mb-1">
        <span className="panel-title mr-1">Average reaction</span>
        {GROUPINGS.map((x) => (
          <button key={x.key} onClick={() => setGrouping(x.key)} className={`chip border ${x.key === grouping ? "tab-on" : "border-line-2 text-muted"}`}>{x.label}</button>
        ))}
      </div>
      <p className="text-[11.5px] text-muted mb-1">{g.note} Dotted = fewer than {d.min_events} days. Bars = 95% confidence interval.</p>
      <Plot data={traces} height={330} layout={{ hovermode: "closest", yaxis: { ticksuffix: "%", zeroline: true, zerolinewidth: 1.5 },
        xaxis: { type: "category" }, margin: { l: 56, r: 12, t: 8, b: 40 } }} />
      <div className="overflow-x-auto mt-1">
        <table className="w-full text-[12px] border-collapse">
          <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
            <th className="text-left py-1 px-2 border-b border-line">Group</th>
            <th className="text-right py-1 px-2 border-b border-line">Stories</th><th className="text-right py-1 px-2 border-b border-line">Days</th>
            {hs.map((h) => <th key={h} className="text-right py-1 px-2 border-b border-line">{HLABEL[h]}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.group} className="border-b border-line/60">
                <td className="py-1 px-2 font-semibold">{r.group}{r.low && <span className="chip chip-warn ml-1.5">few days</span>}</td>
                <td className="py-1 px-2 text-right num">{fmt(r.n, 0)}</td><td className="py-1 px-2 text-right num">{fmt(r.days, 0)}</td>
                {hs.map((h) => {
                  const c = r.horizons[h];
                  const s = sig(c);
                  return (
                    <td key={h} className={`py-1 px-2 text-right num ${s ? (c!.mean! > 0 ? "text-up font-semibold" : "text-down font-semibold") : "text-muted"}`}
                      title={c?.lo != null ? `95% CI ${c.lo.toFixed(3)}% to ${c.hi!.toFixed(3)}%` : ""}>
                      {c?.mean != null ? `${c.mean > 0 ? "+" : ""}${c.mean.toFixed(2)}%${s ? " *" : ""}` : "–"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[11px] text-dim mt-1">* = the 95% interval excludes zero. Hover a number for its interval.</p>
      </div>
    </section>
  );
}

function VolatilityPanel({ d }: { d: Impact }) {
  const [by, setBy] = useState("by_category");
  const rows = d.volatility[by] ?? [];
  const hs = ["15m", "1h", "eod", "1d"];
  return (
    <section>
      <div className="flex flex-wrap items-center gap-1.5 mb-1">
        <span className="panel-title mr-1">Move size after news vs normal</span>
        {[["by_category", "Category"], ["by_materiality", "Materiality"], ["by_first", "First vs follow-up"]].map(([k, l]) => (
          <button key={k} onClick={() => setBy(k)} className={`chip border ${k === by ? "tab-on" : "border-line-2 text-muted"}`}>{l}</button>
        ))}
      </div>
      <p className="text-[11.5px] text-muted mb-1">1.0 = the stock's normal move size over that horizon; 1.3 = 30% bigger moves than normal. Direction ignored.
        Part of this runs the other way round: big moves attract news stories.</p>
      <Plot height={300} data={hs.map((h, i) => ({
        x: rows.map((r) => r.group), y: rows.map((r) => r.ratio[h]?.mean ?? null), name: HLABEL[h], type: "bar",
        marker: { color: C.series[i] },
        error_y: { type: "data", symmetric: false, visible: true, thickness: 1, width: 2,
          array: rows.map((r) => (r.ratio[h]?.hi != null && r.ratio[h].mean != null ? r.ratio[h].hi! - r.ratio[h].mean! : null)),
          arrayminus: rows.map((r) => (r.ratio[h]?.lo != null && r.ratio[h].mean != null ? r.ratio[h].mean! - r.ratio[h].lo! : null)) },
        hovertemplate: `${HLABEL[h]}: %{y:.2f}×<extra></extra>`,
      }))} layout={{ barmode: "group", hovermode: "closest", yaxis: { ticksuffix: "×" }, xaxis: { type: "category" },
        shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 1, y1: 1, line: { color: C.inkFaint, dash: "dot", width: 1 } }],
        margin: { l: 44, r: 12, t: 8, b: 40 } }} />
    </section>
  );
}

function SpikesPanel({ d }: { d: Impact }) {
  const hs = ["1h", "eod", "1d", "3d"];
  const rows = d.spikes.by_news;
  return (
    <section className="grid grid-cols-1 xl:grid-cols-2 gap-3">
      <div>
        <span className="panel-title">Big 15-minute moves: with news vs no news</span>
        <p className="text-[11.5px] text-muted mb-1">Moves over 2× their usual size (last 20 sessions), measured from the next bar in the direction of the move:
          above zero = kept going, below zero = gave back.</p>
        <Plot height={280} data={rows.map((r, i) => ({
          x: hs.map((h) => HLABEL[h]), y: hs.map((h) => r.horizons[h]?.mean ?? null), name: `${r.group} (${r.days} d)`, type: "scatter", mode: "lines+markers",
          line: { color: i ? C.navy : C.gold, width: 2 },
          error_y: { type: "data", symmetric: false, visible: true, thickness: 1, width: 3,
            array: hs.map((h) => { const c = r.horizons[h]; return c?.hi != null && c.mean != null ? c.hi - c.mean : null; }),
            arrayminus: hs.map((h) => { const c = r.horizons[h]; return c?.lo != null && c.mean != null ? c.mean - c.lo : null; }) },
        }))} layout={{ hovermode: "closest", yaxis: { ticksuffix: "%" }, xaxis: { type: "category" }, margin: { l: 52, r: 12, t: 8, b: 36 } }} />
      </div>
      <div>
        <span className="panel-title">Kept going or gave back?</span>
        <table className="w-full text-[12.5px] border-collapse mt-2">
          <thead><tr className="text-muted text-[10px] uppercase tracking-wider">
            <th className="text-left py-1 px-2 border-b border-line">Big moves</th><th className="text-right py-1 px-2 border-b border-line">Count</th>
            <th className="text-right py-1 px-2 border-b border-line">Kept going by the close</th><th className="text-right py-1 px-2 border-b border-line">Kept going next day</th>
          </tr></thead>
          <tbody>{d.spikes.continue_vs_revert.map((r) => (
            <tr key={r.group} className="border-b border-line/60">
              <td className="py-1.5 px-2 font-semibold">{r.group}</td><td className="py-1.5 px-2 text-right num">{fmt(r.n, 0)}</td>
              <td className="py-1.5 px-2 text-right num">{fmt(r.continued_eod, 1)}%</td><td className="py-1.5 px-2 text-right num">{fmt(r.continued_1d, 1)}%</td>
            </tr>))}</tbody>
        </table>
        <p className="text-[11.5px] text-muted mt-2">50% = a coin flip. "News" here means any story tagged with the symbol from 60 minutes before the move to its end.</p>
        <div className="mt-2">
          <span className="panel-title">No-news moves by symbol (+3 days)</span>
          <ul className="text-[12px] mt-1">{d.spikes.by_symbol_no_news.map((r) => {
            const c = r.horizons["3d"];
            return <li key={r.group} className="num">{r.group}: {c?.mean != null ? `${c.mean > 0 ? "+" : ""}${c.mean.toFixed(2)}%` : "–"} <span className="text-muted">({r.days} days{sig(c) ? ", beyond chance" : ""})</span></li>;
          })}</ul>
        </div>
      </div>
    </section>
  );
}
