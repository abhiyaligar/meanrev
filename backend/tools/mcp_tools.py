"""
MCP tools — LangChain @tool wrappers that route via Alpaca MCP Server when configured,
otherwise delegate to throttled broker/client. Complements alpaca_cli_tool.py for Phase 12.

Each tool:
- Checks backend/mcp/client.is_mcp_configured() and attempts MCP call if available
- Falls back to broker_client (no mock) with source="mcp_fallback"
- Returns JSON string with {"source": "mcp"|"mcp_fallback"|"alpaca_cli_fallback", ...}
- Never fabricates positions/orders — error path returns {"error": "No data available..."}.
"""

import json
from typing import Any, Dict

from langchain.tools import tool

from backend.broker import client as broker_client
from backend.core.logging import log_event
from backend.core.utils import clamp_limit, normalize_symbol
from backend.mcp.client import is_mcp_configured, mcp_server_info


def _mcp_invoke(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attempt to invoke MCP server tool synchronously.
    For v1 we keep this as a placeholder that logs and returns fallback signal,
    because real MCP invocation is async (see backend/mcp/client.aget_mcp_tools).
    The research agent's async path can await aget_mcp_tools for true MCP calls.
    """
    if not is_mcp_configured():
        try:
            log_event("mcp_not_configured", level="info", tool=tool_name, status="fallback")
        except Exception:
            pass
        return {"ok": False, "fallback": True, "reason": "mcp_not_configured"}
    # In v1, sync path always delegates to broker but logs MCP attempt for audit trail
    log_event("mcp_sync_attempt", tool=tool_name, args=list(arguments.keys()), note="sync MCP falls back to broker; async path uses real MCP")
    return {"ok": False, "fallback": True, "reason": "sync_fallback_to_broker"}


@tool
def mcp_get_account() -> str:
    """
    Get Alpaca paper account via Alpaca MCP Server (if configured) else broker fallback.
    No args needed. Use for Phase 12 MCP demonstration. Returns JSON with source field.
    """
    mcp = _mcp_invoke("get_account", {})
    if mcp.get("ok"):
        try:
            log_event("mcp_get_account", level="info", source="mcp", connected=True, status="ok")
        except Exception:
            pass
        return json.dumps({"source": "mcp", "connected": True, "account": mcp["data"]}, default=str)
    try:
        data = broker_client.get_account()
        if not data:
            log_event("mcp_get_account", level="warning", source="mcp_fallback", connected=False, error="No data available", mcp_reason=str(mcp.get("reason") or "")[:100], status="empty")
            return json.dumps({"source": "mcp_fallback", "connected": False, "error": "No data available for account via MCP and broker fallback — check ALPACA_API_KEY/SECRET", "mcp": mcp_server_info()}, default=str)
        try:
            pv = float(data.get("portfolio_value") or data.get("equity") or 0) if isinstance(data, dict) else 0
            log_event("mcp_get_account", level="info", source="mcp_fallback", connected=True, portfolio_value=pv, mcp_reason=str(mcp.get("reason") or "")[:100], status="ok")
        except Exception:
            log_event("mcp_get_account", level="info", source="mcp_fallback", connected=True, status="ok")
        return json.dumps({"source": "mcp_fallback", "connected": True, "account": data, "mcp": mcp_server_info(), "mcp_reason": mcp.get("reason")}, default=str)
    except Exception as e:
        log_event("mcp_get_account", level="warning", source="mcp_fallback", error=str(e)[:200], status="error")
        return json.dumps({"source": "mcp_fallback", "connected": False, "error": str(e), "type": type(e).__name__, "mcp": mcp_server_info()}, default=str)


@tool
def mcp_get_positions(symbol: str = "") -> str:
    """
    List positions via Alpaca MCP Server (if configured) else broker fallback.
    Args: symbol optional (e.g. 'AAPL') — case-insensitive, returns single position or empty list.
    """
    sym = normalize_symbol(symbol)
    mcp = _mcp_invoke("get_positions", {"symbol": sym} if sym else {})
    if mcp.get("ok"):
        data = mcp["data"]
        positions = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
        try:
            log_event("mcp_get_positions", level="info", source="mcp", symbol=sym or "all", count=len(positions) if isinstance(positions, list) else 0, status="ok")
        except Exception:
            pass
        return json.dumps({"source": "mcp", "count": len(positions), "positions": positions}, default=str)
    try:
        positions = broker_client.get_positions(symbol=sym)
        if positions is None:
            positions = []
        try:
            symbols = [str(p.get("symbol", "")) for p in positions[:20]] if isinstance(positions, list) else []
            log_event("mcp_get_positions", level="info", source="mcp_fallback", symbol=sym or "all", count=len(positions) if isinstance(positions, list) else 0, symbols=symbols, mcp_reason=str(mcp.get("reason") or "")[:100], status="ok")
        except Exception:
            log_event("mcp_get_positions", level="info", source="mcp_fallback", symbol=sym or "all", count=len(positions) if isinstance(positions, list) else 0, status="ok")
        return json.dumps({"source": "mcp_fallback", "count": len(positions), "positions": positions, "mcp": mcp_server_info(), "mcp_reason": mcp.get("reason")}, default=str)
    except Exception as e:
        log_event("mcp_get_positions", level="warning", source="mcp_fallback", symbol=sym or "all", error=str(e)[:200], status="error")
        return json.dumps({"source": "mcp_fallback", "error": str(e), "type": type(e).__name__, "mcp": mcp_server_info()}, default=str)


@tool
def mcp_get_orders(status: str = "open", limit: int = 50, symbols: str = "") -> str:
    """
    List orders via Alpaca MCP Server (if configured) else broker fallback.
    Args: status open|closed|all (default open), limit 1..500 (default 50), symbols comma list e.g. 'AAPL,SPY'.
    """
    status_norm = status.strip().lower() if status else "open"
    if status_norm not in ("open", "closed", "all"):
        status_norm = "open"
    lim = clamp_limit(limit, default=50, min_val=1, max_val=500)
    mcp = _mcp_invoke("get_orders", {"status": status_norm, "limit": lim, "symbols": symbols})
    if mcp.get("ok"):
        data = mcp["data"]
        orders = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
        try:
            log_event("mcp_get_orders", level="info", source="mcp", status=status_norm, limit=lim, symbols=symbols or "all", count=len(orders) if isinstance(orders, list) else 0)
        except Exception:
            pass
        return json.dumps({"source": "mcp", "count": len(orders), "orders": orders, "status": status_norm, "limit": lim}, default=str)
    try:
        syms = symbols.strip() if symbols and symbols.strip() else None
        orders = broker_client.get_orders(status=status_norm, limit=lim, symbols=syms)
        if orders is None:
            orders = []
        try:
            log_event("mcp_get_orders", level="info", source="mcp_fallback", order_status=status_norm, limit=lim, symbols=symbols or "all", count=len(orders) if isinstance(orders, list) else 0, mcp_reason=str(mcp.get("reason") or "")[:100], status="ok")
        except Exception:
            log_event("mcp_get_orders", level="info", source="mcp_fallback", order_status=status_norm, count=len(orders) if isinstance(orders, list) else 0, status="ok")
        return json.dumps({"source": "mcp_fallback", "count": len(orders), "orders": orders, "status": status_norm, "limit": lim, "mcp": mcp_server_info(), "mcp_reason": mcp.get("reason")}, default=str)
    except Exception as e:
        log_event("mcp_get_orders", level="warning", source="mcp_fallback", order_status=status_norm, error=str(e)[:200], status="error")
        return json.dumps({"source": "mcp_fallback", "error": str(e), "type": type(e).__name__, "mcp": mcp_server_info()}, default=str)


@tool
def mcp_get_clock() -> str:
    """
    Get market clock via Alpaca MCP Server (if configured) else broker fallback.
    No args needed. Returns is_open, next_open, next_close.
    """
    mcp = _mcp_invoke("get_clock", {})
    if mcp.get("ok"):
        data = mcp["data"]
        is_open = bool(data.get("is_open") or data.get("isOpen")) if isinstance(data, dict) else False
        try:
            log_event("mcp_get_clock", level="info", source="mcp", is_open=is_open, status="ok")
        except Exception:
            pass
        return json.dumps({"source": "mcp", "is_open": is_open, "clock": data}, default=str)
    try:
        clock = broker_client.get_clock()
        is_open = bool(clock.get("is_open", False)) if isinstance(clock, dict) else False
        try:
            log_event("mcp_get_clock", level="info", source="mcp_fallback", is_open=is_open, mcp_reason=str(mcp.get("reason") or "")[:100], status="ok")
        except Exception:
            pass
        return json.dumps({"source": "mcp_fallback", "is_open": is_open, "clock": clock, "mcp": mcp_server_info(), "mcp_reason": mcp.get("reason")}, default=str)
    except Exception as e:
        log_event("mcp_get_clock", level="warning", source="mcp_fallback", error=str(e)[:200], status="error")
        return json.dumps({"source": "mcp_fallback", "error": str(e), "type": type(e).__name__, "mcp": mcp_server_info()}, default=str)


MCP_TOOLS = [mcp_get_account, mcp_get_positions, mcp_get_orders, mcp_get_clock]

__all__ = [
    "mcp_get_account",
    "mcp_get_positions",
    "mcp_get_orders",
    "mcp_get_clock",
    "MCP_TOOLS",
]
