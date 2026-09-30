import { useState } from "react";
import { api, useJob, type Job, type Params, type RunReq } from "../lib/api";
import { Spinner } from "./ui";

interface Result { name: string; html: string; pdf?: string }

/** "Generate report" for backtest results: re-runs the backtest with decision records and
 *  writes a plain-English report (HTML + PDF), which then appears on the Reports page. */
export function GenerateReport({ run, strategy, params, choices }: {
  run: RunReq;
  strategy: string;
  params: Params;
  choices?: string[]; // offer a strategy picker (Lab)
}) {
  const [pick, setPick] = useState(strategy);
  const [days, setDays] = useState<number | "all">(5);
  const [jobId, setJobId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const job = useJob<Result>(jobId);
  const busy = job.data && (job.data.status === "queued" || job.data.status === "running");
  const done = job.data?.status === "done" ? job.data.result : undefined;
  const failed = job.data?.status === "error" ? job.data.error : null;

  const go = async () => {
    setErr(null);
    try {
      const j = await api<Job>("/reports/backtest", { ...run, strategy: pick, params: pick === strategy ? params : {}, days: days === "all" ? null : days });
      setJobId(j.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {choices && (
        <select className="input" value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Strategy for the report">
          {choices.map((c) => <option key={c}>{c}</option>)}
        </select>
      )}
      <select className="input" value={String(days)} onChange={(e) => setDays(e.target.value === "all" ? "all" : Number(e.target.value))} aria-label="Report period">
        <option value="1">last day</option>
        <option value="5">last 5 days</option>
        <option value="20">last 20 days</option>
        <option value="all">whole backtest</option>
      </select>
      <button className="btn" onClick={go} disabled={!!busy}>{busy ? <Spinner /> : <i className="bi bi-file-earmark-text" />} Generate report</button>
      {busy && <span className="text-[11.5px] text-muted">{job.data?.message}</span>}
      {done && (
        <span className="text-[12px] flex gap-2">
          <a className="font-semibold underline" href={`/reports/${done.name}.html`} target="_blank" rel="noreferrer">Open report</a>
          {done.pdf && <a className="underline" href={`/reports/${done.name}.pdf`} target="_blank" rel="noreferrer">PDF</a>}
        </span>
      )}
      {(err || failed) && <span className="text-[12px] text-down">{err || failed}</span>}
    </div>
  );
}
