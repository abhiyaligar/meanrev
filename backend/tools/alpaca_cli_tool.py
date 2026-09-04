"""
Alpaca CLI tools — satisfies PHASES.md Phase 12 "MCP Server or Alpaca CLI usage in one agent".

Provides LangChain @tool wrappers that shell out to the `alpaca` CLI (https://github.com/alpacahq/alpaca-trade-api)
for account/positions/orders/clock. Falls back deterministically to the throttled broker/client.py
(when CLI binary not installed or returns non-JSON) so the graph never fails in CI/offline.

Every call logs via backend.core.logging.log_event with source="alpaca_cli" or "alpaca_cli_fallback".
No mock data — on empty/error returns {"error": "No data available..."} per v1 no-mock rule.
"""

import json
import shlex
import subprocess
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from backend.broker import client as broker_client
from backend.core.logging import log_event
from backend.core.utils import clamp_limit, normalize_symbol

# Timeout for CLI subprocess (shorter than broker 30s)
_CLI_TIMEOUT = 8
_CLI_BINARY = "alpaca"


def _run_alpaca_cli(args: List[str]) -> Dict[str, Any]:
    """
    Run `alpaca <args> --format json` (or similar) and parse JSON.
    Returns {"ok": True, "data": parsed} or {"ok": False, "error": "...", "fallback": True}
    """
    # Try common CLI flag variants: --format json, --json, -o json
    # We try without format first (some versions auto-json), then with --json
    attempts = [
        [_CLI_BINARY] + args,
        [_CLI_BINARY] + args + ["--json"],
        [_CLI_BINARY] + args + ["--format", "json"],
        [_CLI_BINARY] + args + ["-o", "json"],
    ]
    last_err = "alpaca CLI not available"
    for cmd in attempts:
        try:
            # Use shell=False for safety
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CLI_TIMEOUT,
            )
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            # Success if stdout is JSON-parseable and returncode 0
            if result.returncode == 0 and out:
                # Try to parse JSON (could be single object or list)
                try:
                    parsed = json.loads(out)
                    log_event("alpaca_cli_ok", cmd=" ".join(cmd), bytes=len(out))
                    return {"ok": True, "data": parsed, "raw": out, "cmd": " ".join(cmd)}
                except json.JSONDecodeError:
                    # Not JSON — try next flag variant
                    last_err = f"non-JSON output: {out[:200]}"
                    continue
            else:
                # Non-zero or empty
                last_err = err or out or f"exit {result.returncode}"
                # If binary missing, FileNotFoundError would have been raised earlier; but some shells return 127
                if "not found" in last_err.lower() or "not recognized" in last_err.lower():
                    return {"ok": False, "error": last_err, "fallback": True, "reason": "binary_not_found"}
                continue
        except FileNotFoundError as e:
            log_event("alpaca_cli_not_found", level="warning", error=str(e)[:200])
            return {"ok": False, "error": f"alpaca CLI binary not found: {e}", "fallback": True, "reason": "binary_not_found"}
        except subprocess.TimeoutExpired:
            log_event("alpaca_cli_timeout", level="warning", cmd=" ".join(cmd), timeout=_CLI_TIMEOUT)
            return {"ok": False, "error": f"alpaca CLI timeout after {_CLI_TIMEOUT}s", "fallback": True, "reason": "timeout"}
        except Exception as e:
            last_err = str(e)[:300]
            continue
    # All attempts failed — signal fallback
    log_event("alpaca_cli_fallback", level="warning", error=last_err[:300])
    return {"ok": False, "error": last_err, "fallback": True, "reason": "all_attempts_failed"}


@tool
def alpaca_cli_account() -> str:
    """
    Get Alpaca paper account via Alpaca CLI (`alpaca account`) with fallback to broker client.
    No args needed. Satisfies Phase 12 CLI requirement. Returns JSON with source field.
    """
    cli = _run_alpaca_cli(["account", "show"])
    if cli.get("ok"):
        try:
            data = cli["data"]
            pv = float(data.get("portfolio_value") or data.get("equity") or 0) if isinstance(data, dict) else 0
            log_event("alpaca_cli_account", level="info", source="alpaca_cli", connected=True, portfolio_value=pv, status="ok")
        except Exception:
            log_event("alpaca_cli_account", level="info", source="alpaca_cli", connected=True, status="ok")
        return json.dumps({"source": "alpaca_cli", "connected": True, "account": cli["data"]}, default=str)
    # Fallback to throttled broker client (no mock — real data or explicit error)
    try:
        data = broker_client.get_account()
        if not data:
            log_event("alpaca_cli_account", level="warning", source="alpaca_cli_fallback", connected=False, error="No data available", status="empty")
            return json.dumps({"source": "alpaca_cli_fallback", "connected": False, "error": "No data available for account via Alpaca CLI and broker fallback — check ALPACA_API_KEY/SECRET"}, default=str)
        try:
            pv = float(data.get("portfolio_value") or data.get("equity") or 0) if isinstance(data, dict) else 0
            log_event("alpaca_cli_account", level="info", source="alpaca_cli_fallback", connected=True, portfolio_value=pv, cli_error=str(cli.get("error") or "")[:100], status="ok")
        except Exception:
            log_event("alpaca_cli_account", level="info", source="alpaca_cli_fallback", connected=True, status="ok")
        return json.dumps({"source": "alpaca_cli_fallback", "connected": True, "account": data, "cli_error": cli.get("error")}, default=str)
    except Exception as e:
        log_event("alpaca_cli_account", level="warning", source="alpaca_cli_fallback", error=str(e)[:200], status="error")
        return json.dumps({"source": "alpaca_cli_fallback", "connected": False, "error": str(e), "type": type(e).__name__, "cli_error": cli.get("error")}, default=str)


@tool
def alpaca_cli_positions(symbol: str = "") -> str:
    """
    List positions via Alpaca CLI (`alpaca positions`) with broker fallback.
    Args: symbol optional (e.g. 'AAPL') — if provided, returns single position or empty list. Case-insensitive.
    """
    sym = normalize_symbol(symbol)
    cmd_args = ["positions", "show"] if not sym else ["positions", "show", sym]
    # Some CLI versions use `alpaca position` singular
    cli = _run_alpaca_cli(cmd_args)
    if cli.get("ok"):
        data = cli["data"]
        # Normalize to list
        positions = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
        # Filter if sym requested but CLI returned all
        if sym and isinstance(positions, list):
            positions = [p for p in positions if str(p.get("symbol", "")).upper() == sym]
        try:
            log_event("alpaca_cli_positions", level="info", source="alpaca_cli", symbol=sym or "all", count=len(positions) if isinstance(positions, list) else 0, status="ok")
        except Exception:
            pass
        return json.dumps({"source": "alpaca_cli", "count": len(positions), "positions": positions}, default=str)
    # Fallback
    try:
        positions = broker_client.get_positions(symbol=sym)
        if positions is None:
            positions = []
        try:
            symbols = [str(p.get("symbol", "")) for p in positions[:20]] if isinstance(positions, list) else []
            log_event("alpaca_cli_positions", level="info", source="alpaca_cli_fallback", symbol=sym or "all", count=len(positions) if isinstance(positions, list) else 0, symbols=symbols, status="ok")
        except Exception:
            pass
        return json.dumps({"source": "alpaca_cli_fallback", "count": len(positions), "positions": positions, "cli_error": cli.get("error")}, default=str)
    except Exception as e:
        log_event("alpaca_cli_positions", level="warning", source="alpaca_cli_fallback", symbol=sym or "all", error=str(e)[:200], status="error")
        return json.dumps({"source": "alpaca_cli_fallback", "error": str(e), "type": type(e).__name__, "cli_error": cli.get("error")}, default=str)


@tool
def alpaca_cli_orders(status: str = "open", limit: int = 50, symbols: str = "") -> str:
    """
    List orders via Alpaca CLI (`alpaca orders`) with broker fallback.
    Args: status open|closed|all (default open), limit 1..500 (default 50), symbols comma list e.g. 'AAPL,SPY'.
    """
    status_norm = status.strip().lower() if status else "open"
    if status_norm not in ("open", "closed", "all"):
        status_norm = "open"
    lim = clamp_limit(limit, default=50, min_val=1, max_val=500)
    # CLI attempt — try `alpaca orders list --status open --limit 50`
    cli_args = ["orders", "list", "--status", status_norm, "--limit", str(lim)]
    if symbols and symbols.strip():
        # Some CLIs accept --symbols
        cli_args += ["--symbols", symbols.strip()]
    cli = _run_alpaca_cli(cli_args)
    if cli.get("ok"):
        data = cli["data"]
        orders = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
        # Post-filter symbols if CLI ignores --symbols (mirrors broker_tools behavior)
        if symbols and symbols.strip():
            wanted = {s.strip().upper() for s in symbols.split(",") if s.strip()}
            orders = [o for o in orders if str(o.get("symbol", "")).upper() in wanted]
        # Clamp limit locally
        orders = orders[:lim]
        try:
            log_event("alpaca_cli_orders", level="info", source="alpaca_cli", status=status_norm, limit=lim, symbols=symbols or "all", count=len(orders) if isinstance(orders, list) else 0)
        except Exception:
            pass
        return json.dumps({"source": "alpaca_cli", "count": len(orders), "orders": orders, "status": status_norm, "limit": lim}, default=str)
    # Fallback
    try:
        syms = symbols.strip() if symbols and symbols.strip() else None
        orders = broker_client.get_orders(status=status_norm, limit=lim, symbols=syms)
        if orders is None:
            orders = []
        try:
            log_event("alpaca_cli_orders", level="info", source="alpaca_cli_fallback", status=status_norm, limit=lim, symbols=symbols or "all", count=len(orders) if isinstance(orders, list) else 0)
        except Exception:
            pass
        return json.dumps({"source": "alpaca_cli_fallback", "count": len(orders), "orders": orders, "status": status_norm, "limit": lim, "cli_error": cli.get("error")}, default=str)
    except Exception as e:
        log_event("alpaca_cli_orders", level="warning", source="alpaca_cli_fallback", order_status=status_norm, error=str(e)[:200], status="error")
        return json.dumps({"source": "alpaca_cli_fallback", "error": str(e), "type": type(e).__name__, "cli_error": cli.get("error")}, default=str)


@tool
def alpaca_cli_clock() -> str:
    """
    Get market clock via Alpaca CLI (`alpaca clock`) with broker fallback.
    No args needed. Returns is_open, next_open, next_close.
    """
    cli = _run_alpaca_cli(["clock", "show"])
    if cli.get("ok"):
        data = cli["data"]
        # Normalize clock shape
        is_open = False
        if isinstance(data, dict):
            is_open = bool(data.get("is_open") or data.get("isOpen"))
        try:
            log_event("alpaca_cli_clock", level="info", source="alpaca_cli", is_open=is_open, status="ok")
        except Exception:
            pass
        return json.dumps({"source": "alpaca_cli", "is_open": is_open, "clock": data}, default=str)
    try:
        clock = broker_client.get_clock()
        is_open = bool(clock.get("is_open", False)) if isinstance(clock, dict) else False
        try:
            log_event("alpaca_cli_clock", level="info", source="alpaca_cli_fallback", is_open=is_open, status="ok")
        except Exception:
            pass
        return json.dumps({"source": "alpaca_cli_fallback", "is_open": is_open, "clock": clock, "cli_error": cli.get("error")}, default=str)
    except Exception as e:
        log_event("alpaca_cli_clock", level="warning", source="alpaca_cli_fallback", error=str(e)[:200], status="error")
        return json.dumps({"source": "alpaca_cli_fallback", "error": str(e), "type": type(e).__name__, "cli_error": cli.get("error")}, default=str)


# Convenience grouping for wiring
ALPACA_CLI_TOOLS = [alpaca_cli_account, alpaca_cli_positions, alpaca_cli_orders, alpaca_cli_clock]

__all__ = [
    "alpaca_cli_account",
    "alpaca_cli_positions",
    "alpaca_cli_orders",
    "alpaca_cli_clock",
    "ALPACA_CLI_TOOLS",
]
