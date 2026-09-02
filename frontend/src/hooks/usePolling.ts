import { useEffect, useRef, useState, useCallback } from "react";
import { api, type AccountResp, type PositionsResp, type OrdersResp, type SchedulerStatus, type DecisionsResp, type ClockResp } from "@/lib/api";

type PollState = {
  account: AccountResp | null;
  positions: PositionsResp | null;
  orders: OrdersResp | null;
  clock: ClockResp | null;
  scheduler: SchedulerStatus | null;
  decisions: DecisionsResp | null;
  lastUpdated: string | null;
  error: string | null;
  isFetching: boolean;
};

const POLL_MS = 30_000; // buffered, not live — respects 25/min

export function usePolling(enabled = true) {
  const [state, setState] = useState<PollState>({
    account: null,
    positions: null,
    orders: null,
    clock: null,
    scheduler: null,
    decisions: null,
    lastUpdated: null,
    error: null,
    isFetching: false,
  });
  const timerRef = useRef<number | null>(null);

  const fetchAll = useCallback(async () => {
    setState((s) => ({ ...s, isFetching: true, error: null }));
    try {
      const [account, positions, orders, clock, scheduler, decisions] = await Promise.all([
        api.getAccount().catch((e) => { throw new Error(`account: ${e.message}`); }),
        api.getPositions().catch((e) => { throw new Error(`positions: ${e.message}`); }),
        api.getOrders("open").catch((e) => { throw new Error(`orders: ${e.message}`); }),
        api.getClock().catch(() => null as unknown as ClockResp),
        api.getSchedulerStatus().catch(() => null as unknown as SchedulerStatus),
        api.getDecisions(50).catch(() => ({ count: 0, decisions: [], ts: new Date().toISOString() }) as DecisionsResp),
      ]);
      setState({
        account,
        positions,
        orders,
        clock,
        scheduler,
        decisions,
        lastUpdated: new Date().toISOString(),
        error: null,
        isFetching: false,
      });
    } catch (e: any) {
      setState((s) => ({ ...s, isFetching: false, error: String(e?.message || e) }));
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    fetchAll();
    timerRef.current = window.setInterval(fetchAll, POLL_MS);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [enabled, fetchAll]);

  return { ...state, refresh: fetchAll, pollIntervalMs: POLL_MS };
}
