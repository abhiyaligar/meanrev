import { usePolling } from "@/hooks/usePolling";
import { SchedulerHeader } from "@/components/SchedulerHeader";
import { AccountCard } from "@/components/AccountCard";
import { PositionsTable } from "@/components/PositionsTable";
import { OrdersTable } from "@/components/OrdersTable";
import { DecisionsFeed } from "@/components/DecisionsFeed";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatTs } from "@/lib/api";

export default function App() {
  const { account, positions, orders, scheduler, decisions, lastUpdated, error, isFetching, refresh } = usePolling(true);

  return (
    <div className="min-h-screen bg-muted/30">
      <header className="sticky top-0 z-10 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div>
            <h1 className="text-lg font-bold tracking-tight">Meanrev — Paper Trading Dashboard</h1>
            <p className="text-xs text-muted-foreground">Strict API • Buffered 30s • No mock data • Start: meanrev --scheduler</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden text-xs text-muted-foreground md:inline">Last updated: {formatTs(lastUpdated)}</span>
            <Button variant="outline" size="sm" onClick={refresh} disabled={isFetching}>
              {isFetching ? "Refreshing…" : "Refresh now"}
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-4 px-4 py-6">
        {error && (
          <Card className="border-destructive">
            <CardContent className="py-3 text-sm text-destructive">API error (strict, no mock): {error}</CardContent>
          </Card>
        )}

        <SchedulerHeader scheduler={scheduler} isFetching={isFetching} />

        <div className="grid gap-4 md:grid-cols-2">
          <AccountCard data={account} />
          <Card>
            <CardContent className="pt-6">
              <div className="text-sm text-muted-foreground">Polling</div>
              <div className="text-2xl font-bold">Every 30s</div>
              <p className="pt-2 text-xs text-muted-foreground">
                Respects Alpaca 25/min bucket. Scheduler started at <span className="font-medium">{formatTs(scheduler?.started_at || scheduler?.last_run || null)}</span>. Dashboard
                captures time scheduler started — not page load.
              </p>
              <p className="pt-1 text-xs text-muted-foreground">Backend: FastAPI <code>GET /api/v1/account|positions|orders|scheduler/*</code> — empty → “No data available”.</p>
            </CardContent>
          </Card>
        </div>

        <PositionsTable data={positions} />
        <OrdersTable data={orders} />
        <DecisionsFeed data={decisions} />

          <footer className="py-4 text-center text-xs text-muted-foreground">
          Frontend only in <code>frontend/</code> • Shadcn UI • API base <code>{(import.meta as any).env?.VITE_API_BASE || "/api (proxied to :8000)"}</code> • No mock data
        </footer>
      </main>
    </div>
  );
}
