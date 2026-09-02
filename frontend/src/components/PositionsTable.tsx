import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { formatMoney } from "@/lib/api";
import type { PositionsResp } from "@/lib/api";

export function PositionsTable({ data }: { data: PositionsResp | null }) {
  const rows = data?.positions ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Current Positions</CardTitle>
        <CardDescription>{data ? `${data.count} open` : "No data available"} — strict API GET /api/v1/positions</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">{data ? "No open positions" : "No data available"}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Qty / Side</TableHead>
                <TableHead>Avg Entry</TableHead>
                <TableHead>Market Value</TableHead>
                <TableHead>Unrealized P&L</TableHead>
                <TableHead>Price</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p: any) => (
                <TableRow key={p.asset_id || p.symbol}>
                  <TableCell className="font-medium">
                    {p.symbol} <Badge variant="outline" className="ml-2">{p.asset_class}</Badge>
                  </TableCell>
                  <TableCell>
                    {p.qty} <span className={p.side === "long" ? "text-green-600" : "text-red-600"}>({p.side})</span>
                  </TableCell>
                  <TableCell>{formatMoney(p.avg_entry_price)}</TableCell>
                  <TableCell>{formatMoney(p.market_value)}</TableCell>
                  <TableCell className={Number(p.unrealized_pl) < 0 ? "text-red-600" : "text-green-600"}>{formatMoney(p.unrealized_pl)} ({p.unrealized_plpc ?? "—"}%)</TableCell>
                  <TableCell>{formatMoney(p.current_price)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
