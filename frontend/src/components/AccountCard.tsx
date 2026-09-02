import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMoney, formatTs } from "@/lib/api";
import type { AccountResp } from "@/lib/api";

export function AccountCard({ data }: { data: AccountResp | null }) {
  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No data available</p>
        </CardContent>
      </Card>
    );
  }
  const a = data.account as any;
  const equity = a.equity ?? a.portfolio_value;
  const cash = a.cash;
  const buyingPower = a.buying_power;
  const dayPl = a.portfolio_value && a.last_equity ? Number(a.portfolio_value) - Number(a.last_equity) : null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          Account <span className="text-xs font-normal text-muted-foreground">{formatTs(data.ts)}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <div>
            <div className="text-muted-foreground">Equity</div>
            <div className="text-lg font-semibold">{formatMoney(equity)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Cash</div>
            <div className="text-lg font-semibold">{formatMoney(cash)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Buying Power</div>
            <div className="text-lg font-semibold">{formatMoney(buyingPower)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">Day P&L</div>
            <div className={`text-lg font-semibold ${dayPl !== null && dayPl < 0 ? "text-red-600" : "text-green-600"}`}>{dayPl !== null ? formatMoney(dayPl) : "—"}</div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4 text-xs md:grid-cols-4">
          <span className="text-muted-foreground">ID {a.id?.slice(0, 8)}…</span>
          <span className="text-muted-foreground">Status {a.status}</span>
          <span className="text-muted-foreground">Crypto {a.crypto_status}</span>
          <span className="text-muted-foreground">Options L{a.options_trading_level}</span>
        </div>
      </CardContent>
    </Card>
  );
}
