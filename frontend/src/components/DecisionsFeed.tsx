import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatTs } from "@/lib/api";
import type { DecisionsResp } from "@/lib/api";

function eventColor(ev: string): "default" | "secondary" | "destructive" | "outline" {
  if (ev.includes("approved") || ev === "buy" || ev === "sell") return "default";
  if (ev.includes("rejected") || ev.includes("error")) return "destructive";
  if (ev === "scheduler_tick") return "secondary";
  return "outline";
}

export function DecisionsFeed({ data }: { data: DecisionsResp | null }) {
  const rows = data?.decisions ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Decisions</CardTitle>
        <CardDescription>{data ? `${data.count} recent` : "No data"} — buffered from logs/broker.jsonl via GET /api/v1/scheduler/decisions</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2">
        {rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No decisions yet — start scheduler to populate</p>
        ) : (
          <div className="max-h-[480px] space-y-2 overflow-auto pr-1">
            {rows.map((d: any, i) => (
              <div key={`${d.ts}-${i}`} className="flex flex-col gap-1 rounded-lg border p-3 text-sm">
                <div className="flex items-center gap-2">
                  <Badge variant={eventColor(String(d.event))}>{d.event}</Badge>
                  <span className="text-xs text-muted-foreground">{formatTs(d.ts)}</span>
                  {d.level && <Badge variant="outline">{d.level}</Badge>}
                  {d.thread_id && <span className="text-xs text-muted-foreground">{d.thread_id}</span>}
                </div>
                <div className="break-words text-xs">
                  {d.last_status && <span className="font-medium">{d.last_status} </span>}
                  {d.symbol && <span> {d.symbol} </span>}
                  {d.qty !== undefined && <span> qty {d.qty} </span>}
                  {d.price !== undefined && <span> @ {String(d.price)} </span>}
                  {d.order_id && <span className="font-mono"> id {String(d.order_id).slice(0, 8)}… </span>}
                  {d.status && <Badge variant="outline">{d.status}</Badge>}
                  {d.rule && <span className="text-muted-foreground"> — {String(d.rule).slice(0, 120)}</span>}
                  {d.reason && <span className="text-muted-foreground"> — {String(d.reason).slice(0, 120)}</span>}
                  {d.error && <span className="text-red-600"> — {String(d.error).slice(0, 120)}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
