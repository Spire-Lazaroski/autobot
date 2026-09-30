import { useEffect, useState, type ReactNode } from "react";

/** Editor for one strategy parameter; picks a control from the default value's type. */
export function ParamInput({ name, value, onChange, disabled, options }: {
  name: string;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled?: boolean;
  options?: string[];
}) {
  const label = <span className="text-[10px] text-muted truncate" title={name}>{name}</span>;
  if (options) {
    return (
      <label className="flex flex-col gap-0.5 min-w-0">
        {label}
        <select className="input" value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          {options.map((o) => <option key={o}>{o}</option>)}
        </select>
      </label>
    );
  }
  if (typeof value === "boolean") {
    return (
      <label className="flex items-center gap-1.5 text-[11px] text-muted pt-3">
        <input type="checkbox" checked={value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /> {name}
      </label>
    );
  }
  if (typeof value === "number") {
    const isInt = Number.isInteger(value);
    return (
      <label className="flex flex-col gap-0.5 min-w-0">
        {label}
        <input className="input w-full" type="number" step={isInt ? 1 : 0.05} value={value} disabled={disabled}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (e.target.value !== "" && isFinite(n)) onChange(n);
          }} />
      </label>
    );
  }
  if (typeof value === "string") {
    return (
      <label className="flex flex-col gap-0.5 min-w-0">
        {label}
        <input className="input w-full" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
      </label>
    );
  }
  return <JsonInput label={label} value={value} onChange={onChange} disabled={disabled} />;
}

function JsonInput({ label, value, onChange, disabled }: {
  label: ReactNode; value: unknown; onChange: (v: unknown) => void; disabled?: boolean;
}) {
  const [text, setText] = useState(JSON.stringify(value));
  const [bad, setBad] = useState(false);
  useEffect(() => setText(JSON.stringify(value)), [value]);
  return (
    <label className="flex flex-col gap-0.5 min-w-0 col-span-full">
      {label}
      <input className={`input w-full ${bad ? "border-down" : ""}`} value={text} disabled={disabled}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setBad(false);
          } catch {
            setBad(true);
          }
        }} />
    </label>
  );
}
