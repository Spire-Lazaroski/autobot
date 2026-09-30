import { useMemo, useState, type ReactNode } from "react";
import type { Row } from "../lib/api";
import { fmt, pnl } from "../lib/format";

export interface Col {
  key: string;
  label: string;
  digits?: number;
  tone?: boolean; // color green/red by sign
  render?: (row: Row) => ReactNode;
  title?: string;
}

/** Dense sortable table. Click a header to sort; click a row to call onRowClick. */
export function SortTable({ rows, cols, initialSort, onRowClick, rowKey, highlight }: {
  rows: Row[];
  cols: Col[];
  initialSort?: { key: string; desc: boolean };
  onRowClick?: (row: Row) => void;
  rowKey: (row: Row) => string;
  highlight?: (row: Row) => string | undefined;
}) {
  const [sort, setSort] = useState(initialSort ?? { key: cols[0].key, desc: false });
  const sorted = useMemo(() => {
    const out = [...rows];
    out.sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""));
      return sort.desc ? -c : c;
    });
    return out;
  }, [rows, sort]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px] border-collapse">
        <thead>
          <tr className="text-muted text-[10px] uppercase tracking-wider">
            {cols.map((c, i) => (
              <th key={c.key} title={c.title}
                className={`py-1.5 px-2 font-semibold cursor-pointer select-none whitespace-nowrap border-b border-line hover:text-fg ${i === 0 ? "text-left sticky left-0 bg-panel z-10" : "text-right"}`}
                onClick={() => setSort((s) => ({ key: c.key, desc: s.key === c.key ? !s.desc : true }))}>
                {c.label}{sort.key === c.key ? (sort.desc ? " ▾" : " ▴") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)} onClick={onRowClick ? () => onRowClick(r) : undefined}
              className={`group border-b border-line/60 ${onRowClick ? "cursor-pointer hover:bg-panel-2" : ""} ${highlight?.(r) ?? ""}`}>
              {cols.map((c, i) => (
                <td key={c.key}
                  className={`py-1 px-2 whitespace-nowrap ${i === 0 ? "text-left sticky left-0 bg-panel group-hover:bg-panel-2 z-10" : "text-right num"} ${c.tone ? pnl(r[c.key]) : ""}`}>
                  {c.render ? c.render(r) : typeof r[c.key] === "number" ? fmt(r[c.key], c.digits ?? 2) : String(r[c.key] ?? "–")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
