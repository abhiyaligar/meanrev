"""
Reporting agent — built-in LangChain create_agent + deterministic fallback per Phase 8/8b.

- Built-in path: create_agent(model=_model_id("reporting") via LLM_MODEL_REPORTING, tools=[read_logs, get_positions, get_orders, get_account], system_prompt=REPORTING_SYSTEM_PROMPT)
- Deterministic fallback: parses logs/broker.jsonl without LLM, produces catalyst → technicals → risk → execution → P&L narrative
- Supports CLI /report and export to reports/report.md + json for submission
- Tools and prompts are fetched from backend/tools and backend/core/system_prompt — no hardcoded models/prompts
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.tools import tool

from backend.core.system_prompt import REPORTING_SYSTEM_PROMPT
from backend.core.utils import get_model_id, handle_tool_errors


@tool
def read_logs(lines: int = 50) -> str:
    """Read last N lines from structured JSON-line logs (logs/broker.jsonl). Args: lines 1..200 (default 50). Returns JSON lines as text."""
    try:
        lim = int(lines)
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(lim, 200))
    log_path = Path(__file__).resolve().parents[1] / "logs" / "broker.jsonl"
    if not log_path.exists():
        return "No logs yet — run the pipeline first."
    try:
        all_lines = log_path.read_text(encoding="utf-8").splitlines()
        tail = all_lines[-lim:]
        return "\n".join(tail)
    except Exception as e:
        return f"read_logs failed: {e}"


def _model_id() -> str:
    return get_model_id("reporting")


def get_reporting_agent():
    """
    Factory — returns built-in LangChain agent per docs with live broker/market context tools.
    """
    # Import lazily to avoid circular
    from backend.tools.broker_tools import get_account, get_orders, get_positions

    return create_agent(
        model=_model_id(),
        tools=[read_logs, get_positions, get_orders, get_account],
        system_prompt=REPORTING_SYSTEM_PROMPT,
        middleware=[ToolCallLimitMiddleware(thread_limit=10, run_limit=5), handle_tool_errors],
    )


# --- Deterministic fallback helpers (no LLM) ---


def _parse_log_lines(lines: List[str]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            events.append(obj)
        except Exception:
            # Fallback for non-JSON lines
            events.append({"raw": line, "event": "raw"})
    return events


def _load_recent_logs(limit: int = 100) -> List[Dict[str, Any]]:
    log_path = Path(__file__).resolve().parents[1] / "logs" / "broker.jsonl"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()[-limit:]
        return _parse_log_lines(lines)
    except Exception:
        return []


def _summarize_pnl(account_state: Optional[Dict[str, Any]], positions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    # Prefer live broker if available, else from logs
    pnl: Dict[str, Any] = {"unrealized_pl": 0.0, "realized_pl": 0.0, "cash": None, "equity": None, "exposure": 0.0}
    if account_state:
        try:
            pnl["cash"] = float(account_state.get("cash", 0)) if account_state.get("cash") is not None else None
            pnl["equity"] = float(account_state.get("equity") or account_state.get("portfolio_value") or 0) if account_state.get("equity") or account_state.get("portfolio_value") else None
            pnl["unrealized_pl"] = float(account_state.get("unrealized_pl", 0))
            pnl["realized_pl"] = float(account_state.get("realized_pl", 0))
        except Exception:
            pass
    if positions:
        try:
            unreal = sum(float(p.get("unrealized_pl") or p.get("unrealized_profit") or 0) for p in positions)
            pnl["unrealized_pl"] = unreal
            pnl["exposure"] = sum(abs(float(p.get("market_value") or 0)) for p in positions)
            pnl["positions_count"] = len(positions)
        except Exception:
            pass
    return pnl


def _reasoning_trail(events: List[Dict[str, Any]]) -> List[str]:
    trail: List[str] = []
    for e in events[-20:]:
        ev = e.get("event", "")
        if ev in ("risk_verdict", "risk_approved", "risk_approved_scaled", "risk_rejected_exposure", "risk_rejected_drawdown", "risk_spxw_close_recommended"):
            trail.append(f"Risk: {e.get('rule') or e.get('reason') or ev} — decision {e.get('decision') or ''}")
        elif ev in ("execution_submitted", "execution_approved_by_human", "execution_rejected_by_human", "execution_failed", "execution_partial_fill"):
            trail.append(f"Execution: {ev} {e.get('symbol') or ''} {e.get('qty') or ''} {e.get('order_id') or ''}")
        elif ev in ("market_data_fetch_ok", "news_fetch_alpaca_ok", "news_fetch_mock", "macro_calendar_mock"):
            trail.append(f"Data: {ev} {e.get('symbol') or e.get('symbols') or ''}")
        elif "research" in ev or "strategy" in ev:
            trail.append(f"{ev}: {str(e.get('output') or e.get('rule') or '')[:120]}")
    return trail[:15]


def generate_report_deterministic(limit: int = 100, account_state: Optional[Dict[str, Any]] = None, positions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Deterministic report without LLM — parses logs/broker.jsonl and live broker state.
    Returns {report: str (markdown), positions, trades, pnl, reasoning_trail, risk_events, events}
    Structure: catalyst summary → technical evidence → risk rule → execution result → P&L (per 8b)
    """
    events = _load_recent_logs(limit=limit)
    # Try live broker for fresh P&L if not provided
    if account_state is None or positions is None:
        try:
            from backend.broker.client import get_account as _get_acct, get_positions as _get_pos

            if account_state is None:
                try:
                    account_state = _get_acct()
                except Exception:
                    account_state = None
            if positions is None:
                try:
                    positions = _get_pos()
                except Exception:
                    positions = []
        except Exception:
            pass

    pnl = _summarize_pnl(account_state, positions)
    trail = _reasoning_trail(events)

    # Extract catalyst (last research-like event)
    catalyst = "No catalyst events found — run research agent with fetch_news + macro calendar."
    for e in reversed(events):
        if e.get("event") in ("news_fetch_mock", "news_fetch_alpaca_ok", "macro_calendar_mock") or "research" in e.get("event", ""):
            catalyst = e.get("headline") or e.get("catalyst_summary") or e.get("output") or str(e)[:300]
            break
        if e.get("catalyst_summary"):
            catalyst = e["catalyst_summary"]
            break

    # Technicals: last market data event
    technicals = "No technical events — run market fetch for RSI/MACD/EMA/BB/ATR."
    for e in reversed(events):
        if "market_data" in e.get("event", "") or "rsi" in str(e).lower() or "ema" in str(e).lower():
            technicals = f"{e.get('event')} {e.get('symbol') or ''} {e.get('timeframe') or ''} rows={e.get('rows') or ''}"
            break

    # Risk: last risk verdict
    risk_section = "No risk verdict — run risk agent."
    risk_events: List[str] = []
    for e in reversed(events):
        if "risk" in e.get("event", "") or "circuit_breaker" in e.get("event", ""):
            risk_section = e.get("rule") or e.get("reason") or e.get("event")
            risk_events.append(f"{e.get('event')}: {risk_section}")
            break
    # Check paused flag
    paused_flag = Path(__file__).resolve().parents[1] / "logs" / ".paused"
    if paused_flag.exists():
        try:
            paused_content = paused_flag.read_text(encoding="utf-8").strip()
            risk_events.append(f"circuit_breaker_paused: {paused_content}")
        except Exception:
            risk_events.append("circuit_breaker_paused: flag exists")

    # Execution: last execution event
    execution_section = "No execution — no approved trades yet."
    trades: List[Dict[str, Any]] = []
    for e in reversed(events):
        if "execution" in e.get("event", ""):
            execution_section = f"{e.get('event')} {e.get('symbol') or ''} {e.get('order_id') or ''} status={e.get('status') or e.get('event')}"
            # Collect trades from events
            if e.get("order_id"):
                trades.append({"order_id": e.get("order_id"), "symbol": e.get("symbol"), "status": e.get("event")})
            break

    # Build markdown report
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = f"""# Trading Report — {now}

## Catalyst Summary
{catalyst}

## Technical Evidence
{technicals}
Trailing reasoning: {"; ".join(trail[:3]) if trail else "none"}

## Risk Rule
{risk_section}
Risk events: {", ".join(risk_events) if risk_events else "none"}

## Execution Result
{execution_section}
Trades this window: {len(trades)} — {trades[:3] if trades else "none"}

## P&L
- Cash: {pnl.get('cash')}
- Equity: {pnl.get('equity')}
- Unrealized P&L: {pnl.get('unrealized_pl')}
- Realized P&L: {pnl.get('realized_pl')}
- Exposure: {pnl.get('exposure')}
- Positions: {pnl.get('positions_count', len(positions) if positions else 0)}

## Reasoning Trail
{chr(10).join(f"- {t}" for t in trail[:10]) if trail else "- No trail — run research→strategy→risk→execution cycle at least once."}

## Risk Events (Circuit Breaker, SPXW)
{chr(10).join(f"- {r}" for r in risk_events) if risk_events else "- None"}

*Generated deterministically from logs/broker.jsonl + live broker state (no LLM). For LLM narrative, set OPENROUTER_API_KEY and LLM_MODEL_REPORTING in .env.*
"""

    return {
        "report": report,
        "positions": positions or [],
        "trades": trades,
        "pnl": pnl,
        "reasoning_trail": trail,
        "risk_events": risk_events,
        "events": events[-10:],
        "catalyst": catalyst,
        "technicals": technicals,
    }


def reporting_agent(state: Optional[Dict[str, Any]] = None, export_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Adapter for CLI /report and graph. Prefers built-in LLM agent when configured, else deterministic.
    If export_path provided, writes Markdown + JSON to that path for submission.
    """
    result: Dict[str, Any]
    # Try built-in LLM path
    try:
        from backend.core.config import get_settings

        if get_settings().is_llm_configured():
            agent = get_reporting_agent()
            # Include deterministic context as part of prompt for richer LLM output
            det = generate_report_deterministic(limit=50)
            prompt = (
                f"Generate the trading report from logs/broker.jsonl. Use read_logs tool for full logs.\n"
                f"Deterministic context for grounding (do not repeat verbatim, use to inform narrative):\n"
                f"Catalyst: {det['catalyst'][:500]}\nTechnicals: {det['technicals'][:300]}\n"
                f"Risk: {det['risk_events'][:2] if det['risk_events'] else 'none'}\n"
                f"P&L: {det['pnl']}\n"
            )
            llm_result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
            last = llm_result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            result = {
                "report": str(content),
                "agent": "reporting",
                "model": _model_id(),
                "built_in": True,
                "deterministic_context": det,
                "positions": det["positions"],
                "pnl": det["pnl"],
            }
        else:
            raise RuntimeError("LLM not configured")
    except Exception as e:
        # Deterministic fallback — always succeeds offline
        det = generate_report_deterministic(limit=100)
        # If LLM was configured but failed, include error
        err_note = f" (LLM failed: {str(e)[:200]})" if "LLM not configured" not in str(e) else ""
        result = {
            "report": det["report"] + (f"\n\n*Note{err_note} — deterministic fallback used.*" if err_note else ""),
            "agent": "reporting",
            "model": _model_id(),
            "stub": False,
            "deterministic": True,
            "positions": det["positions"],
            "trades": det["trades"],
            "pnl": det["pnl"],
            "reasoning_trail": det["reasoning_trail"],
            "risk_events": det["risk_events"],
        }

    # Export if requested
    if export_path:
        try:
            p = Path(export_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(result.get("report", ""), encoding="utf-8")
            # Also write JSON sidecar
            jpath = p.with_suffix(".json")
            # Build export dict without large events
            export_data = {k: v for k, v in result.items() if k not in ("events", "deterministic_context")}
            # Add timestamp
            export_data["exported_at"] = datetime.now(timezone.utc).isoformat()
            jpath.write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")
            result["exported_to"] = str(p)
            result["exported_json"] = str(jpath)
        except Exception as e:
            result["export_error"] = str(e)[:300]

    return result
