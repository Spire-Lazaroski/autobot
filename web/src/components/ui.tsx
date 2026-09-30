import type { ReactNode } from "react";

export function LowTrades({ n, min = 30 }: { n: number; min?: number }) {
  if (n >= min) return null;
  return (
    <span className="chip bg-warn/15 text-warn border border-warn/40" title={`Only ${n} trades: mostly noise. Add history or symbols.`}>
      ⚠ {n} trades
    </span>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="panel border-down/50 bg-down/10 text-down px-3 py-2 text-[12px] whitespace-pre-wrap break-words">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-muted text-[12px]">
      <span className="h-3 w-3 rounded-full border-2 border-accent border-t-transparent animate-spin" />
      {label}
    </span>
  );
}

export function Progress({ value, label }: { value: number; label?: string }) {
  return (
    <div className="flex items-center gap-3 w-full">
      <div className="h-1.5 flex-1 bg-line rounded overflow-hidden">
        <div className="h-full bg-accent transition-all" style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      <span className="num text-muted text-[11px] w-40 truncate">{Math.round(value * 100)}% {label}</span>
    </div>
  );
}

export function Panel({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel p-3 min-w-0 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between gap-2 mb-2">
          <h3 className="panel-title">{title}</h3>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, tone = "text-fg" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="panel px-3 py-2 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-muted truncate">{label}</div>
      <div className={`num text-[17px] font-semibold ${tone} truncate`}>{value}</div>
      {sub && <div className="num text-[11px] text-muted truncate">{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="panel p-8 text-center text-muted">{children}</div>;
}
