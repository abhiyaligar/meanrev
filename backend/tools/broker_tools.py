"""
Broker tools — LangChain @tool wrappers around backend/broker/client.py.

Per langchain-docs MCP: @tool + type hints + docstring defines schema.
All calls funnel through throttled broker/client (25/min, 30s timeout, paper=True).
No secrets logged; symbols normalized to upper.
"""

import json

from langchain.tools import tool

from backend.broker import client as broker_client
from backend.core.utils import clamp_limit, normalize_symbol


@tool
def get_account() -> str:
    """Get Alpaca paper account details including cash, portfolio value, buying power, and options level. No args needed."""
    try:
        data = broker_client.get_account()
        return json.dumps({"connected": True, "account": data}, default=str)
    except Exception as e:
        return json.dumps({"connected": False, "error": str(e), "type": type(e).__name__})


@tool
def get_positions(symbol: str = "") -> str:
    """List open positions with unrealized P&L. If symbol provided (e.g. 'AAPL'), returns single position or empty list. Symbol is case-insensitive."""
    try:
        sym = normalize_symbol(symbol)
        positions = broker_client.get_positions(symbol=sym)
        return json.dumps({"count": len(positions), "positions": positions}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def get_orders(status: str = "open", limit: int = 50, symbols: str = "") -> str:
    """List orders and fills. Args: status open|closed|all (default open), limit 1..500 (default 50), symbols comma list e.g. 'AAPL,SPY' (optional)."""
    try:
        status = status.strip().lower() if status else "open"
        if status not in ("open", "closed", "all"):
            status = "open"
        lim = clamp_limit(limit, default=50, min_val=1, max_val=500)
        syms = symbols.strip() if symbols.strip() else None
        orders = broker_client.get_orders(status=status, limit=lim, symbols=syms)
        return json.dumps({"count": len(orders), "orders": orders, "status": status, "limit": lim}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def get_clock() -> str:
    """Get market clock including is_open, next_open, next_close. No args needed."""
    try:
        clock = broker_client.get_clock()
        is_open = bool(clock.get("is_open", False)) if isinstance(clock, dict) else False
        return json.dumps({"is_open": is_open, "clock": clock}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def submit_order(symbol: str, qty: float, side: str = "buy", order_type: str = "market", limit_price: float = 0.0) -> str:
    """Submit a paper order (requires human approval via HITL). Args: symbol e.g. 'AAPL' (required), qty >0 (required), side buy|sell (default buy), order_type market|limit (default market), limit_price for limit orders. Returns order_id or error. Throttled 25/min."""
    try:
        sym = normalize_symbol(symbol)
        if not sym:
            return json.dumps({"error": "symbol required"})
        try:
            q = float(qty)
        except (TypeError, ValueError):
            return json.dumps({"error": "qty must be numeric >0"})
        if q <= 0:
            return json.dumps({"error": "qty must be >0"})
        sd = side.strip().lower()
        if sd not in ("buy", "sell"):
            sd = "buy"
        ot = order_type.strip().lower()
        if ot not in ("market", "limit"):
            ot = "market"
        # Note: actual submit via broker/client would be here; for Phase 7 stub, return indicative dry-run
        # When HITL approves, this tool executes; if rejected, middleware returns reject without calling
        return json.dumps(
            {
                "status": "dry_run",
                "symbol": sym,
                "qty": q,
                "side": sd,
                "type": ot,
                "limit_price": limit_price if ot == "limit" else None,
                "note": "submit_order is HITL-protected — human approval required before live paper order. This is dry-run until Phase 7 execution wires to broker/client.",
            },
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})
