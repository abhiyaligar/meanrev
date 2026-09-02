import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { formatMoney, formatTs } from "@/lib/api";
import type { OrdersResp } from "@/lib/api";

export function OrdersTable({ data }: { data: OrdersResp | null }) {
  const rows = data?.orders ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Current Orders</CardTitle>
        <CardDescription>{data ? `${data.count} ${data.status_filter}` : "No data available"} — strict API GET /api/v1/orders?status=open</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">{data ? "No open orders — filled orders appear in Decisions feed" : "No data available"}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Order ID</TableHead>
                <TableHead>Symbol / Side</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Type / TIF</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((o: any) => (
                <TableRow key={o.id}>
                  <TableCell className="font-mono text-xs">{String(o.id).slice(0, 8)}…</TableCell>
                  <TableCell>
                    <span className="font-medium">{o.symbol}</span> <Badge variant={o.side === "buy" ? "default" : "destructive"}>{o.side}</Badge>
                  </TableCell>
                  <TableCell>{o.qty ?? o.notional ?? "—"} {o.filled_qty ? `→ ${o.filled_qty}` : ""}</TableCell>
                  <TableCell>
                    {o.type || o.order_type} <span className="text-muted-foreground">/ {o.time_in_force}</span>
                    <div className="text-xs">{o.limit_price ? formatMoney(o.limit_price) : o.stop_price ? `stop ${formatMoney(o.stop_price)}` : "market"}</div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={o.status === "filled" ? "default" : o.status === "canceled" ? "destructive" : "secondary"}>{o.status}</Badge>
                  </TableCell>
                  <TableCell className="text-xs">{formatTs(o.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
