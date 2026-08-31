"""
Broker tools — LangChain @tool wrappers around backend/broker/client.py.

Per langchain-docs MCP: @tool + type hints + docstring defines schema.
All calls funnel through throttled broker/client (25/min, 30s timeout, paper=True).
No secrets logged; symbols normalized to upper.
"""

import json

from langchain.tools import tool

from backend.broker import client as broker_client
from backend.core.logging import log_event
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


@tool
def set_stop_loss(symbol: str, stop_price: float, qty: float = 0, side: str = "sell") -> str:
    """
    Add a stop-loss order for an existing position (throttled 25/min, live paper order).
    Args: symbol e.g. 'AAPL' or 'BTC/USD' (required), stop_price >0 (required, trigger price),
          qty >0 optional (if 0, uses full open position qty), side sell|buy (default sell for long stop-loss).
    Returns order dict with order_id, or error. Use to protect long positions: sell stop below market.
    """
    try:
        sym = normalize_symbol(symbol)
        if not sym:
            return json.dumps({"error": "symbol required for stop-loss"})
        try:
            sp = float(stop_price)
        except (TypeError, ValueError):
            return json.dumps({"error": "stop_price must be numeric >0"})
        if sp <= 0:
            return json.dumps({"error": "stop_price must be >0"})
        sd = side.strip().lower() if side else "sell"
        if sd not in ("buy", "sell"):
            sd = "sell"
        # Resolve qty — if 0 or not provided, fetch open position qty (robust for crypto slash)
        q_val: float | None = None
        if qty and float(qty) > 0:
            q_val = float(qty)
        else:
            # Auto-resolve from position (no mock — if no position, return error)
            # Crypto positions stored as BTCUSD but queried as BTC/USD — try both via full scan fallback
            try:
                positions = broker_client.get_positions(symbol=sym)
                # If direct symbol lookup empty (e.g., BTC/USD vs BTCUSD mismatch), scan all
                if not positions:
                    all_pos = broker_client.get_positions()
                    # Match slash-insensitive and base (BTC)
                    norm = sym.replace("/", "").upper()
                    positions = [p for p in all_pos if str(p.get("symbol", "")).replace("/", "").upper() == norm]
                    if not positions:
                        # Also try base prefix match (e.g., BTC)
                        base = norm.replace("USD", "").replace("USDT", "")
                        positions = [p for p in all_pos if str(p.get("symbol", "")).replace("/", "").upper().startswith(base)]
                if not positions:
                    return json.dumps({"error": f"No open position for {sym} — cannot set stop-loss without qty; provide qty explicitly"})
                # positions[0] qty is string
                q_val = float(positions[0].get("qty", 0))
                if q_val <= 0:
                    return json.dumps({"error": f"Position qty 0 for {sym} — provide qty explicitly"})
            except Exception as e:
                return json.dumps({"error": f"Failed to resolve position qty for {sym}: {e}", "type": type(e).__name__})
        # Crypto does not support stop orders on Alpaca (market/limit only)
        from backend.broker.client import _is_crypto_symbol as _is_crypto

        if _is_crypto(sym):
            log_event("set_stop_loss", level="warning", order_id="pending", price=float(sp), symbol=sym, qty=float(q_val) if q_val else None, side=sd, error="CryptoStopNotSupported")
            return json.dumps(
                {
                    "error": f"Crypto {sym} does not support stop orders on Alpaca (only market/limit for crypto). For BTC/ETH, manage risk via position size or manual market sell, or use equities/ETFs for stop-loss demo.",
                    "type": "CryptoStopNotSupported",
                    "hint": "Try: set_stop_loss(symbol='AAPL', stop_price=150, qty=1) for equities; for crypto, use cancel_order or close via market sell",
                }
            )
        # Submit stop order via throttled broker client (live)
        result = broker_client.submit_order(symbol=sym, qty=q_val, side=sd, order_type="stop", stop_price=sp)
        oid = str(result.get("id") or result.get("order_id") or "")
        # jsonl log — required fields: ts (auto), event, level, order_id, price
        log_event("set_stop_loss", level="info", order_id=oid, price=float(sp), symbol=sym, qty=float(q_val), side=sd, type="stop")
        # Also emit generic sell/buy event for dashboard compatibility (event=buy/sell)
        log_event(sd, level="info", order_id=oid, price=float(sp), symbol=sym, qty=float(q_val), status="stop_submitted")
        return json.dumps({"status": "submitted", "order": result, "stop_price": sp, "symbol": sym, "qty": q_val, "side": sd}, default=str)
    except Exception as e:
        log_event("set_stop_loss", level="warning", order_id="pending", price=float(stop_price) if str(stop_price).replace(".","",1).isdigit() else None, symbol=symbol, error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def modify_order(order_id: str, qty: float = 0, limit_price: float = 0, stop_price: float = 0, trail: float = 0) -> str:
    """
    Modify (replace) an existing open order — throttled 25/min.
    Args: order_id UUID/str (required), qty >0 optional (new qty), limit_price >0 optional (new limit),
          stop_price >0 optional (new stop), trail >0 optional (trailing amount).
          At least one of qty/limit_price/stop_price/trail must be >0.
    Returns updated order dict or error. Only open orders can be replaced.
    """
    try:
        oid = str(order_id).strip()
        if not oid:
            return json.dumps({"error": "order_id required"})
        # Build kwargs — 0 means not provided
        kwargs = {}
        if qty and float(qty) > 0:
            kwargs["qty"] = float(qty)
        if limit_price and float(limit_price) > 0:
            kwargs["limit_price"] = float(limit_price)
        if stop_price and float(stop_price) > 0:
            kwargs["stop_price"] = float(stop_price)
        if trail and float(trail) > 0:
            kwargs["trail"] = float(trail)
        if not kwargs:
            return json.dumps({"error": "At least one of qty, limit_price, stop_price, trail required >0 for modify"})
        result = broker_client.replace_order(order_id=oid, **kwargs)
        new_oid = str(result.get("id") or result.get("order_id") or oid)
        # Determine price for jsonl (prefer limit > stop > trail)
        price_val = kwargs.get("limit_price") or kwargs.get("stop_price") or kwargs.get("trail")
        log_event("modify_order", level="info", order_id=new_oid, price=float(price_val) if price_val is not None else None, symbol=str(result.get("symbol") or ""), qty=float(kwargs.get("qty")) if kwargs.get("qty") else None, modified=kwargs, replaces=oid)
        return json.dumps({"status": "replaced", "order": result, "order_id": oid, "modified": kwargs}, default=str)
    except Exception as e:
        log_event("modify_order", level="warning", order_id=str(order_id) if order_id else "pending", price=float(limit_price) if limit_price and float(limit_price) > 0 else (float(stop_price) if stop_price and float(stop_price) > 0 else None), error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def cancel_order(order_id: str) -> str:
    """Cancel a single open order by UUID/str id — throttled 25/min. Args: order_id required. Returns {cancelled: true, order_id} or error."""
    try:
        oid = str(order_id).strip()
        if not oid:
            return json.dumps({"error": "order_id required"})
        result = broker_client.cancel_order(order_id=oid)
        log_event("cancel_order", level="info", order_id=str(oid), price=None, status="cancelled")
        # Also generic event for jsonl dashboard compatibility (cancel as sell-like)
        return json.dumps(result, default=str)
    except Exception as e:
        log_event("cancel_order", level="warning", order_id=str(order_id) if order_id else "pending", price=None, error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def cancel_all_orders() -> str:
    """Cancel all open orders — throttled 25/min. No args needed. Returns list of cancel results or error."""
    try:
        results = broker_client.cancel_all_orders()
        cnt = len(results) if isinstance(results, list) else 0
        log_event("cancel_all_orders", level="info", order_id="all", price=None, count=cnt, status="cancelled")
        return json.dumps({"cancelled": True, "count": cnt, "results": results}, default=str)
    except Exception as e:
        log_event("cancel_all_orders", level="warning", order_id="all", price=None, error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__})
