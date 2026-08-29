"""
Broker tools — LangChain @tool wrappers around backend/broker/client.py.

Per langchain-docs MCP: @tool + type hints + docstring defines schema.
All calls funnel through throttled broker/client (25/min, 30s timeout, paper=True).
No secrets logged; symbols normalized to upper.
"""

import json
from langchain.tools import tool

from backend.broker import client as broker_client


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
        sym = symbol.strip().upper() if symbol.strip() else None
        positions = broker_client.get_positions(symbol=sym)
        return json.dumps({"count": len(positions), "positions": positions}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def get_orders(status: str = "open", limit: int = 50, symbols: str = "") -> str:
    """List orders and fills. Args: status open|closed|all (default open), limit 1..500 (default 50), symbols comma list e.g. 'AAPL,SPY' (optional)."""
    try:
        # Normalize
        status = status.strip().lower() if status else "open"
        if status not in ("open", "closed", "all"):
            status = "open"
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 50
        if lim < 1:
            lim = 1
        lim = min(lim, 500)
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
