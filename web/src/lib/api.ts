import { useQuery } from "@tanstack/react-query";

export type Params = Record<string, unknown>;

export interface StrategyInfo {
  name: string;
  family: string;
  description: string;
  defaults: Params;
  grid: Record<string, unknown[]>;
  source: string;
  warmup_days: number;
}

export interface DataReq {
  source: "demo" | "alpaca";
  symbols: string[];
  timeframe: string;
  days: number;
  rth_only: boolean;
}

export interface RunReq {
  data: DataReq;
  fee_bps: number;
  slippage_bps: number;
  allow_short: boolean;
}

export type Row = Record<string, any>;

export interface Frame {
  t: number[];
  series: Record<string, (number | null)[]>;
}

export interface CompareResult {
  summary: Row[];
  per_symbol: Row[];
  equity: Frame;
  bars: Record<string, number>;
  low_trades_threshold: number;
}

export interface Line {
  name: string;
  color?: string | null;
  dash?: string | null;
  values: (number | null)[];
}
export interface Pane {
  name: string;
  series: Line[];
  levels: number[];
  range?: [number, number] | null;
}
export interface Fill {
  time: number;
  price: number;
  side: "buy" | "sell";
  kind: "entry" | "exit";
  from: number;
  to: number;
  label?: string; // marker text override (live fills show quantity)
}
export interface Candles {
  t: number[];
  o: number[];
  h: number[];
  l: number[];
  c: number[];
  v: number[];
}
export interface DetailResult {
  symbol: string;
  strategy: string;
  params: Params;
  candles: Candles;
  overlays: Line[];
  panes: Pane[];
  position: number[];
  equity: number[];
  benchmark: number[];
  fills: Fill[];
  trades: Row[];
  metrics: Row;
  benchmark_metrics: Row;
  low_trades: boolean;
  active?: { labels: string[]; names: string[]; values: (number | null)[] } | null;
}

export interface Job<T = unknown> {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "error";
  progress: number;
  message: string;
  error: string | null;
  result?: T;
}

export class ApiError extends Error {}

/** To the sign-in page (the lab's own login, api/auth.py); in development Vite serves it as login.html. */
export function toLogin() {
  window.location.assign(import.meta.env.DEV ? "/login.html" : "/login");
}

export async function api<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    toLogin();
    throw new ApiError("Please sign in.");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw new ApiError(msg);
  }
  return res.json() as Promise<T>;
}

export interface Me { user: string | null; role: "admin" | "user" | null; login_required: boolean }

/** Who is signed in: "admin" (the lab's owner, or the PC without a login) or an invited "user". */
export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me"), staleTime: 300_000 });
}

export function useStrategies() {
  return useQuery({ queryKey: ["strategies"], queryFn: () => api<StrategyInfo[]>("/strategies"), staleTime: Infinity });
}

export function useHealth() {
  return useQuery({ queryKey: ["health"], queryFn: () => api<{ ok: boolean; alpaca_keys: boolean; timeframes: string[] }>("/health"), staleTime: 60_000, refetchInterval: 30_000 });   // recovers by itself after an API restart
}

/** Poll a background job until it finishes. */
export function useJob<T>(id: string | null) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: () => api<Job<T>>(`/jobs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "done" || s === "error" ? false : 400;
    },
  });
}
