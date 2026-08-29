"""
Slash commands — Phase 11.2

Deterministic handlers for /status, /positions, /report, /pause, /resume, /help
All respect throttling (25/min) and never log secrets.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.logging import log_event

_PAUSED_FLAG = Path(__file__).resolve().parents[1] / "logs" / ".paused"


def _fmt_account(account: Dict[str, Any]) -> str:
    try:
        return (
            f"Account {account.get('account_number') or account.get('id') or 'unknown'} — "
            f"status {account.get('status')} cash {account.get('cash')} "
            f"portfolio {account.get('portfolio_value') or account.get('equity')} "
            f"buying_power {account.get('buying_power')} options_level {account.get('options_approved_level')}"
        )
    except Exception:
        return str(account)[:500]


def handle_status(args: str = "") -> Dict[str, Any]:
    """Handle /status — account + risk paused flag + last graph state."""
    try:
        from backend.broker.client import get_account
        from backend.agents.risk import is_paused

        try:
            acct_data = get_account()
            acct = acct_data if isinstance(acct_data, dict) and "account_number" in acct_data else acct_data.get("account", acct_data) if isinstance(acct_data, dict) else {}
        except Exception as e:
            acct = {"error": str(e)[:300]}

        paused = is_paused()
        # Try to get last graph state from logs
        log_path = Path(__file__).resolve().parents[1] / "logs" / "broker.jsonl"
        last_event = "no logs"
        if log_path.exists():
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()[-5:]
                last_event = lines[-1][:300] if lines else "empty"
            except Exception:
                pass

        data: Dict[str, Any] = {
            "account": acct,
            "paused": paused,
            "last_log": last_event,
            "hitl": _hitl_status(),
        }
        log_event("cli_status", paused=paused)
        return {"output": _render_status(data), "data": data}
    except Exception as e:
        return {"output": f"/status failed: {e}", "data": {"error": str(e)}}


def _hitl_status() -> Dict[str, Any]:
    try:
        from backend.core.config import get_settings

        s = get_settings()
        return {"execution_mode": getattr(s, "execution_mode", "auto"), "hitl_enabled": bool(getattr(s, "hitl_enabled", False))}
    except Exception:
        return {}


def _render_status(data: Dict[str, Any]) -> str:
    acct = data.get("account", {})
    if isinstance(acct, dict) and "account_number" in acct:
        acct_str = _fmt_account(acct)
    elif isinstance(acct, dict) and "account" in acct:
        acct_str = _fmt_account(acct["account"])
    else:
        acct_str = str(acct)[:400]
    paused = data.get("paused")
    hitl = data.get("hitl", {})
    return (
        f"Status:\n"
        f"  Account: {acct_str}\n"
        f"  Paused: {paused} (circuit breaker: logs/.paused exists)\n"
        f"  HITL: mode={hitl.get('execution_mode')} enabled={hitl.get('hitl_enabled')}\n"
        f"  Last log: {data.get('last_log','')[:200]}"
    )


def handle_positions(args: str = "") -> Dict[str, Any]:
    """Handle /positions [symbol] — open positions."""
    symbol = args.strip().upper() if args.strip() else None
    try:
        from backend.broker.client import get_positions

        positions = get_positions(symbol=symbol)
        log_event("cli_positions", symbol=symbol or "all", count=len(positions))
        if not positions:
            return {"output": f"No open positions{f' for {symbol}' if symbol else ''}.", "data": {"positions": []}}
        # Render table via rich fallback to text
        lines = [f"Positions ({len(positions)}):"]
        for p in positions[:10]:
            lines.append(
                f"  {p.get('symbol')} qty {p.get('qty')} avg {p.get('avg_entry_price')} mkt {p.get('market_value')} uPL {p.get('unrealized_pl')}"
            )
        if len(positions) > 10:
            lines.append(f"  ... and {len(positions)-10} more")
        return {"output": "\n".join(lines), "data": {"positions": positions}}
    except Exception as e:
        return {"output": f"/positions failed: {e}", "data": {"error": str(e)}}


def handle_report(args: str = "") -> Dict[str, Any]:
    """Handle /report [lines] [export] — generate report via reporting_agent."""
    parts = args.strip().split()
    lines = 50
    export_path = "reports/report.md"
    if parts:
        try:
            lines = int(parts[0])
        except ValueError:
            # If first part is export path
            export_path = parts[0]
            lines = 50
        if len(parts) > 1:
            export_path = parts[1]

    try:
        from backend.agents.reporting import reporting_agent

        res = reporting_agent({}, export_path=export_path)
        report = res.get("report", "")[:2000]
        log_event("cli_report", lines=lines, exported_to=res.get("exported_to"))
        # Try rich markdown rendering, fallback to plain
        try:
            from rich.console import Console
            from rich.markdown import Markdown

            Console().print(Markdown(report[:2000]))
            return {"output": f"Report exported to {res.get('exported_to')} and {res.get('exported_json')}", "data": res}
        except Exception:
            return {"output": report[:2000] + f"\n\nExported to {res.get('exported_to')}", "data": res}
    except Exception as e:
        return {"output": f"/report failed: {e}", "data": {"error": str(e)}}


def handle_pause(args: str = "") -> Dict[str, Any]:
    """Handle /pause — set circuit breaker paused flag (requires confirm via questionary if available)."""
    try:
        # Confirm via questionary if available
        try:
            import questionary

            if not questionary.confirm("Pause trading? Orders will be rejected until /resume.", default=False).ask():
                return {"output": "Pause cancelled.", "data": {"paused": False}}
        except Exception:
            pass

        _PAUSED_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _PAUSED_FLAG.write_text(f"paused via CLI /pause at {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}\n", encoding="utf-8")
        log_event("cli_pause", reason=args[:100] if args else "manual")
        return {"output": "Paused — circuit breaker set (logs/.paused). Use /resume to clear. Risk will reject new orders.", "data": {"paused": True}}
    except Exception as e:
        return {"output": f"/pause failed: {e}", "data": {"error": str(e)}}


def handle_resume(args: str = "") -> Dict[str, Any]:
    """Handle /resume — clear paused flag."""
    try:
        try:
            import questionary

            if not questionary.confirm("Resume trading? This clears the circuit breaker.", default=True).ask():
                return {"output": "Resume cancelled.", "data": {"paused": True}}
        except Exception:
            pass

        if _PAUSED_FLAG.exists():
            _PAUSED_FLAG.unlink(missing_ok=True)
            log_event("cli_resume", reason=args[:100] if args else "manual")
            return {"output": "Resumed — circuit breaker cleared. Trading will proceed.", "data": {"paused": False}}
        else:
            return {"output": "Not paused — no flag to clear.", "data": {"paused": False}}
    except Exception as e:
        return {"output": f"/resume failed: {e}", "data": {"error": str(e)}}


def handle_help(args: str = "") -> Dict[str, Any]:
    """Handle /help — list commands."""
    text = (
        "Commands:\n"
        "  /status              — account, paused, HITL mode, last log\n"
        "  /positions [SYMBOL]  — open positions (e.g., /positions AAPL)\n"
        "  /report [lines] [path] — generate report, export to reports/report.md (default 50 lines)\n"
        "  /pause [reason]      — set circuit breaker paused flag\n"
        "  /resume [reason]     — clear paused flag\n"
        "  /help                — this help\n"
        "  /quit, /exit         — exit CLI\n"
        "Natural language (no slash) → routed to graph as instruction (e.g., 'be more conservative today', 'explain last trade')\n"
        "Prompts are from backend/core/system_prompt.py (<1000 tokens) and models from LLM_MODEL_* in .env"
    )
    return {"output": text, "data": {}}


# Registry for repl routing
COMMANDS = {
    "status": handle_status,
    "positions": handle_positions,
    "report": handle_report,
    "pause": handle_pause,
    "resume": handle_resume,
    "help": handle_help,
    "quit": lambda args: {"output": "Use /exit to quit", "data": {}},
    "exit": lambda args: {"output": "exit", "data": {"exit": True}},
}
