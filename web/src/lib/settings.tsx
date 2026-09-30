import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type DataReq, type Params, type RunReq } from "./api";

export interface LabSettings {
  source: "demo" | "alpaca";
  symbols: string;
  timeframe: string;
  days: number;
  rth_only: boolean;
  fee_bps: number;
  slippage_bps: number;
  allow_short: boolean;
  train_frac: number;
}

const DEFAULTS: LabSettings = {
  source: "demo", symbols: "SPY, QQQ, AAPL", timeframe: "15Min", days: 90, rth_only: true,
  fee_bps: 0, slippage_bps: 2, allow_short: false, train_frac: 0.7,
};

export interface Picked {
  on: boolean;
  params: Params;
}

interface Ctx {
  s: LabSettings;
  set: (patch: Partial<LabSettings>) => void;
  run: RunReq;
  picked: Record<string, Picked>;
  setPicked: (next: Record<string, Picked>) => void;
}

const SettingsCtx = createContext<Ctx | null>(null);

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}
function save(key: string, v: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(v));
  } catch {
    /* storage unavailable: settings just won't persist */
  }
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const hadSaved = (() => {
    try { return !!localStorage.getItem("lab.settings"); } catch { return false; }
  })();
  const [s, setS] = useState<LabSettings>(() => stored("lab.settings", DEFAULTS));
  const [picked, setPickedState] = useState<Record<string, Picked>>(() => stored("lab.picked", {}));

  // First visit: seed from config.yaml's backtest section (source stays demo until keys are confirmed).
  useEffect(() => {
    if (hadSaved) return;
    api<{ backtest: Record<string, any> }>("/config").then(({ backtest: b }) =>
      setS((cur) => ({
        ...cur, symbols: (b.symbols ?? []).join(", ") || cur.symbols, timeframe: b.timeframe ?? cur.timeframe,
        days: b.days ?? cur.days, rth_only: b.rth_only ?? cur.rth_only, fee_bps: b.fee_bps ?? cur.fee_bps,
        slippage_bps: b.slippage_bps ?? cur.slippage_bps, allow_short: b.allow_short ?? cur.allow_short,
        train_frac: b.train_frac ?? cur.train_frac,
      })),
    ).catch(() => {});
  }, [hadSaved]);

  const value = useMemo<Ctx>(() => {
    const data: DataReq = {
      source: s.source,
      symbols: s.symbols.split(/[,\s]+/).map((x) => x.trim().toUpperCase()).filter(Boolean),
      timeframe: s.timeframe, days: s.days, rth_only: s.rth_only,
    };
    return {
      s,
      set: (patch) => setS((cur) => { const n = { ...cur, ...patch }; save("lab.settings", n); return n; }),
      run: { data, fee_bps: s.fee_bps, slippage_bps: s.slippage_bps, allow_short: s.allow_short },
      picked,
      setPicked: (next) => { setPickedState(next); save("lab.picked", next); },
    };
  }, [s, picked]);

  return <SettingsCtx.Provider value={value}>{children}</SettingsCtx.Provider>;
}

export function useSettings() {
  const c = useContext(SettingsCtx);
  if (!c) throw new Error("useSettings outside SettingsProvider");
  return c;
}
