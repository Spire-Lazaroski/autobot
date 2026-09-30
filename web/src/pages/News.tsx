import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { SortTable } from "../components/SortTable";
import { ErrorBox, Kpi, Panel, Spinner } from "../components/ui";
import { api, type Row } from "../lib/api";
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
                { key: "symbols", label: "Symbols", render: (r) => <span className="num">{(r.symbols as string[]).slice(0, 4).join(", ")}{r.symbols.length > 4 ? ` +${r.symbols.length - 4}` : ""}</span> },
                { key: "headline", label: "Headline", render: (r) => (
                  <a className="hover:underline whitespace-normal block max-w-[70ch]" href={r.url} target="_blank" rel="noreferrer">{r.headline}
                    {r.n_updates > 0 && <span className="chip chip-warn ml-1.5">edited ×{r.n_updates}</span>}</a>) },
                { key: "source", label: "Source", render: (r) => <span className="text-muted">{r.source || "–"}</span> },
                { key: "origin", label: "How", render: (r) => <span className="chip bg-panel-2 border border-line text-muted">{r.origin}</span> },
                { key: "received_at", label: "Delay", title: "Received minus published", render: (r) => (
                  <span className="num">{r.received_at ? `${fmt(secs(r.received_at)! - secs(r.created_at)!, 1)} s` : "–"}</span>) },
              ]} />
          </div>
        )}
      </Panel>
      <p className="text-[11.5px] text-dim">Scores (step 3) and the impact study (step 4) will appear on this page.</p>
    </div>
  );
}
