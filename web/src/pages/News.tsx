import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { SortTable } from "../components/SortTable";
import { ErrorBox, Kpi, Panel, Spinner } from "../components/ui";
import { api, useJob, type Job, type Row } from "../lib/api";
import { fmt, nyTime } from "../lib/format";

interface Status {
  collector: { running: boolean; pid?: number; heartbeat_age_s?: number; stream_items?: number; poll_new?: number; last_item_at?: string | null; connected?: boolean };
  db: { items: number; first: string | null; last: string | null; by_origin: Record<string, number>; by_symbol: Record<string, number>; edited: number; last_received: string | null };
  symbols: string[];
}

const secs = (iso?: string | null) => (iso ? Date.parse(iso) / 1000 : null);

export function News() {
  const st = useQuery({ queryKey: ["news-status"], queryFn: () => api<Status>("/news/status"), refetchInterval: 10_000 });
  const [sym, setSym] = useState<string>("");
  const items = useQuery({ queryKey: ["news-items", sym], queryFn: () => api<Row[]>(`/news/items?limit=150${sym ? `&symbol=${sym}` : ""}`), refetchInterval: 30_000 });
  const c = st.data?.collector;
  const db = st.data?.db;

  return (
    <div className="flex flex-col gap-3">
      <div className="panel p-2.5 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${c?.running ? "bg-up" : "bg-dim"}`} />
          <span className={`font-semibold uppercase tracking-wide text-[12px] ${c?.running ? "text-up" : "text-muted"}`}>
            news collector {c?.running ? "running" : "stopped"}</span>
          {c?.running && <span className="num text-[11.5px] text-muted">pid {c.pid} · beat {fmt(c.heartbeat_age_s, 0)}s ago · {c.stream_items ?? 0} live items, {c.poll_new ?? 0} caught by the poll</span>}
        </span>
        {!c?.running && <span className="text-[12px] text-muted">It starts with start_lab.bat (window "autobot news") and never trades.</span>}
      </div>
      <ErrorBox error={st.error} />

      {db && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
          <Kpi label="Stored items" value={fmt(db.items, 0)} sub={`${fmt(db.edited, 0)} edited later`} />
          <Kpi label="From" value={db.first ? db.first.slice(0, 10) : "–"} sub={`to ${db.last ? db.last.slice(0, 10) : "–"}`} />
          <Kpi label="Backfilled" value={fmt(db.by_origin.backfill ?? 0, 0)} sub="no receive time" />
          <Kpi label="Received live" value={fmt((db.by_origin.stream ?? 0) + (db.by_origin.poll ?? 0), 0)} sub={`${fmt(db.by_origin.poll ?? 0, 0)} via the poll`} />
          <Kpi label="Last received" value={db.last_received ? nyTime(secs(db.last_received)!) : "–"} sub="New York time" />
        </div>
      )}

      <Panel title="Latest news" right={
        <div className="flex gap-1 flex-wrap">
          {["", ...(st.data?.symbols ?? [])].map((s) => (
            <button key={s || "all"} onClick={() => setSym(s)}
              className={`chip border num ${s === sym ? "tab-on" : "border-line-2 text-muted"}`}>{s || "all"}{s && db ? ` · ${fmt(db.by_symbol[s] ?? 0, 0)}` : ""}</button>
          ))}
        </div>}>
        {items.isLoading && <Spinner />}
        {items.data && (
          <div className="max-h-[60vh] overflow-y-auto">
            <SortTable rows={items.data} rowKey={(r) => String(r.id)} initialSort={{ key: "created_at", desc: true }}
              cols={[
                { key: "created_at", label: "Published (NY)", render: (r) => <span className="num whitespace-nowrap">{nyTime(secs(r.created_at)!)}</span> },
                { key: "symbols", label: "Symbols", align: "left", render: (r) => <span className="num">{(r.symbols as string[]).slice(0, 4).join(", ")}{r.symbols.length > 4 ? ` +${r.symbols.length - 4}` : ""}</span> },
                { key: "headline", label: "Headline", align: "left", render: (r) => (
                  <a className="hover:underline whitespace-normal block max-w-[70ch]" href={r.url} target="_blank" rel="noreferrer">{r.headline}
                    {r.n_updates > 0 && <span className="chip chip-warn ml-1.5">edited ×{r.n_updates}</span>}</a>) },
                { key: "source", label: "Source", align: "left", render: (r) => <span className="text-muted">{r.source || "–"}</span> },
                { key: "origin", label: "How", render: (r) => <span className="chip bg-panel-2 border border-line text-muted">{r.origin}</span> },
                { key: "received_at", label: "Delay", title: "Received minus published", render: (r) => (
                  <span className="num">{r.received_at ? `${fmt(secs(r.received_at)! - secs(r.created_at)!, 1)} s` : "–"}</span>) },
              ]} />
          </div>
        )}
      </Panel>
      <ScoresPanel symbols={st.data?.symbols ?? []} />
      <p className="text-[11.5px] text-dim">The impact study (step 4) will appear on this page.</p>
    </div>
  );
}

const CATS = ["earnings", "guidance", "m&a", "analyst_rating", "legal_regulatory", "macro", "other"];
interface ScoresRes {
  rows: Row[];
  summary: Record<string, Record<string, number>>;
  versions: Record<string, string>;
  model: string;
  estimate: { events: number; cost_usd: number; cost_usd_batch: number };
}

function ScoresPanel({ symbols }: { symbols: string[] }) {
  const [scorer, setScorer] = useState<"rules" | "claude">("rules");
  const [cat, setCat] = useState("");
  const [sym, setSym] = useState("");
  const [minMat, setMinMat] = useState(1);
  const [jobId, setJobId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["news-scores", scorer, cat, sym, minMat],
    queryFn: () => api<ScoresRes>(`/news/scores?scorer=${scorer}&min_materiality=${minMat}&limit=80${cat ? `&category=${encodeURIComponent(cat)}` : ""}${sym ? `&symbol=${sym}` : ""}`),
  });
  const job = useJob<{ scored: number; failed: number }>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");
  const runClaude = async (limit: number) => {
    setErr(null);
    try { setJobId((await api<Job>("/news/score-claude", { limit })).id); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const sum = q.data?.summary[scorer] ?? {};
  const total = Object.values(sum).reduce((a, b) => a + b, 0);
  return (
    <Panel title="Scores: sanity check" right={
      <div className="flex gap-1.5 flex-wrap items-center">
        {(["rules", "claude"] as const).map((s) => (
          <button key={s} onClick={() => setScorer(s)} className={`chip border ${scorer === s ? "tab-on" : "border-line-2 text-muted"}`}>
            {s === "rules" ? `keyword rules (${q.data?.versions.rules ?? ""})` : `Claude (${q.data?.model ?? ""})`}</button>
        ))}
        <select className="input" value={sym} onChange={(e) => setSym(e.target.value)} aria-label="Symbol"><option value="">all symbols</option>{symbols.map((x) => <option key={x}>{x}</option>)}</select>
        <select className="input" value={minMat} onChange={(e) => setMinMat(Number(e.target.value))} aria-label="Minimum materiality">
          {[1, 2, 3, 4, 5].map((m) => <option key={m} value={m}>materiality ≥ {m}</option>)}</select>
      </div>}>
      <div className="flex flex-wrap gap-1.5 mb-2">
        <button onClick={() => setCat("")} className={`chip border ${cat === "" ? "tab-on" : "border-line-2 text-muted"}`}>all · {fmt(total, 0)}</button>
        {CATS.map((c) => (
          <button key={c} onClick={() => setCat(c)} className={`chip border ${cat === c ? "tab-on" : "border-line-2 text-muted"}`}>{c.replace("_", " ")} · {fmt(sum[c] ?? 0, 0)}</button>
        ))}
      </div>
      {q.data && (
        <div className="flex flex-wrap items-center gap-2 mb-2 text-[12px]">
          <span className="text-muted">Claude scoring for the {fmt(q.data.estimate.events, 0)} stories not yet scored by Claude:
            about <b>${fmt(q.data.estimate.cost_usd, 2)}</b> (≈ ${fmt(q.data.estimate.cost_usd_batch, 2)} via the Batch API). Rough estimate from text length.</span>
          <button className="btn" disabled={!!busy} onClick={() => runClaude(50)}>{busy ? <Spinner /> : <i className="bi bi-stars" />} Score the newest 50 with Claude</button>
          {busy && <span className="text-muted">{job.data?.message}</span>}
          {job.data?.status === "done" && <span className="text-up">Scored {job.data.result?.scored}, failed {job.data.result?.failed}.</span>}
          {(err || job.data?.status === "error") && <span className="text-down">{err || job.data?.error}</span>}
        </div>
      )}
      {q.isLoading && <Spinner />}
      <ErrorBox error={q.error} />
      {q.data && q.data.rows.length === 0 && <p className="text-muted text-[12px]">{scorer === "claude" ? "No Claude scores yet. Add ANTHROPIC_API_KEY to .env (or run `ant auth login`), restart the lab, then score a sample." : "Nothing matches."}</p>}
      {q.data && q.data.rows.length > 0 && (
        <div className="max-h-[60vh] overflow-y-auto">
          <SortTable rows={q.data.rows} rowKey={(r) => `${r.event_id}-${r.symbol}`} initialSort={{ key: "created_at", desc: true }}
            cols={[
              { key: "created_at", label: "Published (NY)", render: (r) => <span className="num whitespace-nowrap">{nyTime(secs(r.created_at)!)}</span> },
              { key: "symbol", label: "Sym", render: (r) => <span className="num font-semibold">{r.symbol}</span> },
              { key: "headline", label: "Headline", align: "left", render: (r) => <span className="whitespace-normal block max-w-[64ch]">{r.headline}{r.n_items > 1 && <span className="chip bg-panel-2 border border-line text-muted ml-1.5">{r.n_items} copies</span>}</span> },
              { key: "category", label: "Category", render: (r) => <span className="chip bg-panel-2 border border-line text-muted">{String(r.category).replace("_", " ")}</span> },
              { key: "relevance", label: "Relevance", render: (r) => <span className={r.relevance === "main" ? "font-semibold" : "text-muted"}>{r.relevance}</span> },
              { key: "sentiment", label: "Sent.", tone: true },
              { key: "surprise", label: "Surprise", render: (r) => <span className={r.surprise === "unexpected" ? "font-semibold" : "text-muted"}>{r.surprise}</span> },
              { key: "materiality", label: "Mat.", digits: 0, render: (r) => <span className="num font-semibold">{"●".repeat(r.materiality)}<span className="text-line-2">{"●".repeat(5 - r.materiality)}</span></span> },
              { key: "rationale", label: "Why", align: "left", render: (r) => <span className="text-muted whitespace-normal block max-w-[32ch] text-[11.5px]">{r.rationale}</span> },
            ]} />
        </div>
      )}
    </Panel>
  );
}
