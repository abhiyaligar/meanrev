import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatTs } from "@/lib/api";
import type { SchedulerStatus } from "@/lib/api";

export function SchedulerHeader({ scheduler, isFetching }: { scheduler: SchedulerStatus | null; isFetching: boolean }) {
  if (!scheduler) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Scheduler</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{isFetching ? "Loading…" : "No data available — scheduler not started. Run: meanrev --scheduler"}</p>
        </CardContent>
      </Card>
    );
  }
  const startedAt = scheduler.started_at || scheduler.last_run;
  const isOpen = scheduler.is_open;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-base">Scheduler</CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={isOpen ? "default" : "secondary"}>{isOpen ? "Market OPEN" : "Market CLOSED"}</Badge>
          <Badge variant="outline">{scheduler.thread_id}</Badge>
          <Badge variant={isFetching ? "secondary" : "outline"}>{isFetching ? "Fetching…" : `Poll 30s`}</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-2 text-sm">
        <div className="flex flex-wrap gap-4">
          <span>
            <span className="text-muted-foreground">Started:</span> {formatTs(startedAt)}
          </span>
          <span>
            <span className="text-muted-foreground">Last run:</span> {formatTs(scheduler.last_run)}
          </span>
          <span>
            <span className="text-muted-foreground">Next run:</span> {formatTs(scheduler.next_run)}
          </span>
          <span>
            <span className="text-muted-foreground">Runs:</span> {scheduler.run_count} × {scheduler.interval_min}min
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{scheduler.last_status || "no status"}</Badge>
          {scheduler.last_error && <Badge variant="destructive">error: {scheduler.last_error.slice(0, 80)}</Badge>}
        </div>
        {scheduler.run_count === 0 && !scheduler.last_run && (
          <p className="text-muted-foreground">Scheduler not started — dashboard will show live data once you run <code className="rounded bg-muted px-1">meanrev --scheduler</code></p>
        )}
      </CardContent>
    </Card>
  );
}
