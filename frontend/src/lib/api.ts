/**
 * Strict API client — no mock fallbacks.
 * All fetches hit backend FastAPI (throttled 25/min). On error return []/null, caller shows "No data available".
 */

const BASE = (import.meta as any).env?.VITE_API_BASE || "";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} ${path}: ${text.slice(0, 300)}`);
  }
  return res.json() as Promise<T>;
}

export type AccountResp = {
  connected: boolean;
  account: Record<string, any>;
  ts: string;
};

export type PositionsResp = {
  count: number;
  positions: Record<string, any>[];
  ts: string;
};

export type OrdersResp = {
  count: number;
  orders: Record<string, any>[];
  status_filter: string;
  ts: string;
};

export type ClockResp = {
  is_open: boolean;
  clock: Record<string, any>;
  ts: string;
};

export type SchedulerStatus = {
  run_count: number;
  last_run: string | null;
  next_run: string | null;
  last_status: string | null;
  last_error: string | null;
  thread_id: string;
  interval_min: number;
  updated_at: string | null;
  is_open: boolean | null;
  market_hours: Record<string, any> | null;
  started_at: string | null;
};

export type DecisionsResp = {
  count: number;
  decisions: Record<string, any>[];
  ts: string;
};

export const api = {
  getAccount: () => fetchJson<AccountResp>("/api/v1/account"),
  getPositions: () => fetchJson<PositionsResp>("/api/v1/positions"),
  getOrders: (status: "open" | "closed" | "all" = "open") => fetchJson<OrdersResp>(`/api/v1/orders?status=${status}&limit=50`),
  getClock: () => fetchJson<ClockResp>("/api/v1/clock"),
  getSchedulerStatus: () => fetchJson<SchedulerStatus>("/api/v1/scheduler/status"),
  getDecisions: (limit = 50) => fetchJson<DecisionsResp>(`/api/v1/scheduler/decisions?limit=${limit}`),
};

export function formatMoney(v: any): string {
  const n = Number(v);
  if (Number.isNaN(n)) return String(v ?? "—");
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function formatTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
