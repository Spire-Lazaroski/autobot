import { useEffect, type ReactNode } from "react";

/** In-page confirmation dialog (no browser confirm(), which blocks the page). */
export function Confirm({ title, children, confirmLabel, danger, busy, onConfirm, onCancel }: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onCancel]);
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onCancel}>
      <div role="dialog" aria-modal="true" aria-label={title}
        className="panel w-full max-w-md p-4 flex flex-col gap-3 border-line-2" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-[15px] font-semibold">{title}</h2>
        <div className="text-[13px] text-muted leading-relaxed">{children}</div>
        <div className="flex justify-end gap-2 pt-1">
          <button className="btn" onClick={onCancel} autoFocus>Cancel</button>
          <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={onConfirm} disabled={busy}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
