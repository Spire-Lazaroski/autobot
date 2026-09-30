import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { SortTable } from "../components/SortTable";
import { ErrorBox, Panel, Spinner } from "../components/ui";
import { api, useJob, type Job } from "../lib/api";

interface ReportMeta { name: string; kind: "daily" | "range" | "backtest"; html: string; pdf: string | null; created: number }
interface Listing {
  reports: ReportMeta[];
  auto: { enabled: boolean; last_run: string | null; last_error: string | null; next_run: string | null };
}

const nyDate = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
const when = (iso: string | null, tz: string) =>
  iso ? new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso)) : "–";
const KIND: Record<string, string> = { daily: "Daily", range: "Date range", backtest: "Backtest" };

export function Reports() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["reports"], queryFn: () => api<Listing>("/reports"), refetchInterval: 30_000 });
  const today = nyDate(new Date());
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [jobId, setJobId] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);
  const job = useJob<{ name: string }>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");

  useEffect(() => {
    if (job.data?.status === "done") {
      qc.invalidateQueries({ queryKey: ["reports"] });
      setOpen(`/reports/${job.data.result!.name}.html`);
    }
  }, [job.data?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = async (d0: string, d1: string) => {
    setErr(null); setFrom(d0); setTo(d1);
    try {
      setJobId((await api<Job>("/reports/live", { date_from: d0, date_to: d1 })).id);
    } catch (e) {
      setErr(e);
    }
  };
  const back = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return nyDate(d); };
  const auto = list.data?.auto;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Panel title="Report on the paper account" className="lg:col-span-2">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-0.5"><span className="text-[10.5px] text-muted font-semibold">From (New York date)</span>
              <input className="input" type="date" value={from} max={today} onChange={(e) => setFrom(e.target.value)} /></label>
            <label className="flex flex-col gap-0.5"><span className="text-[10.5px] text-muted font-semibold">To</span>
              <input className="input" type="date" value={to} max={today} onChange={(e) => setTo(e.target.value)} /></label>
            <button className="btn btn-primary" disabled={!!busy || !from} onClick={() => go(from, to || from)}>
              {busy ? <Spinner /> : <i className="bi bi-file-earmark-text" />} Generate</button>
            <span className="text-muted text-[12px] ml-1">Quick:</span>
            <button className="btn" disabled={!!busy} onClick={() => go(today, today)}>Today</button>
            <button className="btn" disabled={!!busy} onClick={() => go(back(1), back(1))}>Yesterday</button>
            <button className="btn" disabled={!!busy} onClick={() => go(back(6), today)}>Last 7 days</button>
          </div>
          {busy && <p className="text-[12px] text-muted mt-2">{job.data?.message}…</p>}
          {job.data?.status === "error" && <div className="mt-2"><ErrorBox error={job.data.error} /></div>}
          <div className="mt-2"><ErrorBox error={err} /></div>
          <p className="text-[11.5px] text-muted mt-2">
            Reports explain every trade and strategy switch in plain English, using the reasons the bot logged at the time.
            For a backtest, use <b>Generate report</b> under the Lab or Shifting results.
          </p>
        </Panel>
        <Panel title="Automatic daily report">
          <p className="text-[12.5px]">Written 10 minutes after the US market closes, every trading day, while the lab is running.</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px] mt-2 num">
            <dt className="text-muted">Next</dt>
            <dd>{when(auto?.next_run ?? null, "America/New_York")} New York · {when(auto?.next_run ?? null, "Europe/Skopje")} Skopje</dd>
            <dt className="text-muted">Last</dt><dd>{when(auto?.last_run ?? null, "Europe/Skopje")} Skopje</dd>
          </dl>
          {auto?.last_error && <p className="text-[12px] text-down mt-2">Last problem: {auto.last_error}</p>}
        </Panel>
      </div>

      <Panel title={`Past reports (${list.data?.reports.length ?? 0})`}>
        {list.isLoading && <Spinner />}
        {list.data && list.data.reports.length === 0 && <p className="text-muted">No reports yet.</p>}
        {list.data && list.data.reports.length > 0 && (
          <div className="max-h-72 overflow-y-auto">
            <SortTable rows={list.data.reports} rowKey={(r) => r.name} initialSort={{ key: "created", desc: true }}
              onRowClick={(r) => setOpen(r.html)}
              highlight={(r) => (open === r.html ? "bg-panel-2" : undefined)}
              cols={[
                { key: "name", label: "Report", render: (r) => <span className="font-semibold">{r.name}</span> },
                { key: "kind", label: "Type", render: (r) => <span className="chip bg-panel-2 border border-line text-muted">{KIND[r.kind]}</span> },
                { key: "created", label: "Created (Skopje)", render: (r) => <span className="num">{new Date(r.created * 1000).toLocaleString("en-GB", { timeZone: "Europe/Skopje" })}</span> },
                { key: "html", label: "", render: (r) => (
                  <span className="flex gap-3 justify-end" onClick={(e) => e.stopPropagation()}>
                    <a className="underline" href={r.html} target="_blank" rel="noreferrer"><i className="bi bi-box-arrow-up-right" /> open</a>
                    {r.pdf && <a className="underline" href={r.pdf} target="_blank" rel="noreferrer"><i className="bi bi-filetype-pdf" /> PDF</a>}
                  </span>) },
              ]} />
          </div>
        )}
      </Panel>

      {open && (
        <Panel title="Report" right={<a className="text-[12px] underline" href={open} target="_blank" rel="noreferrer">open in a new tab</a>}>
          <iframe title="report" src={open} className="w-full rounded-lg border border-line" style={{ height: "80vh" }} />
        </Panel>
      )}
    </div>
  );
}
