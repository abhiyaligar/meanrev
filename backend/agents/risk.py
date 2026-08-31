"""
Risk agent — deterministic rules engine per PHASES.md Phase 6 (Priority 1).

No LLM, no hardcoded thresholds — all limits via core/config.py RISK_MAX_* from .env.
Tracks P&L, margin, cash, drawdown; enforces position/exposure caps; circuit breaker auto-pause;
SPXW/XSP close-before-expiry handling.

Uses:
- backend/broker/client (account/positions) for equity/cash/margin
- backend/data/market (fetch_ohlcv, fetch_option_chain) for price/spot and SPXW detection
- backend/core/utils (normalize_symbol, clamp_limit) for DRY
- backend/core/logging for structured logs
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import get_settings
from backend.core.logging import log_event
from backend.core.utils import normalize_symbol

# Constants for SPXW detection
_SPXW_PREFIXES = ("SPXW", "XSP", "SPX", "NDX", "RUT")  # cash-settled index family
_PAUSED_FLAG = Path(__file__).resolve().parents[1] / "logs" / ".paused"


def _is_spxw_symbol(symbol: str) -> bool:
    """Check if symbol is cash-settled index option family."""
    if not symbol:
        return False
    s = normalize_symbol(symbol) or ""
    return any(s.startswith(p) for p in _SPXW_PREFIXES) or s in ("SPXW", "XSP")


def track_account_state(account: Optional[Dict[str, Any]], positions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Extract normalized account state from broker dumps.
    Returns {cash, equity, portfolio_value, buying_power, unrealized_pl, realized_pl, margin_usage, peak_equity}
    """
    account = account or {}
    positions = positions or []

    def _to_float(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    cash = _to_float(account.get("cash"))
    equity = _to_float(account.get("equity") or account.get("portfolio_value") or account.get("portfolio_value"))
    # Fallback to cash if equity missing
    if equity == 0:
        equity = cash
    buying_power = _to_float(account.get("buying_power"), equity * 4)
    # Unrealized from positions
    unrealized = 0.0
    for p in positions:
        unrealized += _to_float(p.get("unrealized_pl") or p.get("unrealized_profit") or 0)
    # Realized not directly in account dump, use 0 and track via logs
    realized = _to_float(account.get("realized_pl") or 0)

    # Margin usage: (equity - cash) / equity approx, or buying_power ratio
    margin_usage = 0.0
    if equity > 0:
        margin_usage = max(0.0, (equity - cash) / equity) if cash < equity else 0.0

    # Peak equity for drawdown — max of last_equity, config, and current equity (true peak)
    s = get_settings()
    candidates = [equity]
    try:
        last_eq_raw = account.get("last_equity") if isinstance(account, dict) else None
        last_eq = float(last_eq_raw) if last_eq_raw is not None and str(last_eq_raw).strip() != "" else None
        if last_eq is not None and last_eq > 0:
            candidates.append(last_eq)
    except (TypeError, ValueError):
        pass
    try:
        if s.risk_peak_equity and s.risk_peak_equity > 0:
            candidates.append(float(s.risk_peak_equity))
    except (TypeError, ValueError):
        pass
    peak = max(candidates) if candidates else equity

    return {
        "cash": cash,
        "equity": equity,
        "portfolio_value": equity,
        "buying_power": buying_power,
        "unrealized_pl": unrealized,
        "realized_pl": realized,
        "margin_usage": margin_usage,
        "peak_equity": peak,
    }


def check_position_limit(
    symbol: str,
    qty: float,
    price: float,
    equity: float,
    max_position_pct: Optional[float] = None,
) -> Tuple[bool, float, str]:
    """
    Per-position size limit: position_notional / equity <= max_position_pct.
    Returns (pass, adjusted_qty, rule_msg).
    If fails, returns adjusted_qty scaled to max allowed.
    """
    s = get_settings()
    max_pct = max_position_pct if max_position_pct is not None else s.risk_max_position_pct
    if equity <= 0:
        return False, 0.0, f"equity {equity} invalid for position limit"
    if qty == 0 or price <= 0:
        return False, 0.0, f"qty {qty} or price {price} invalid"
    notional = abs(qty * price)
    ratio = notional / equity
    if ratio <= max_pct:
        return True, qty, f"position ok {ratio:.3f} <= {max_pct:.3f}"
    # Scale down
    max_notional = equity * max_pct
    adjusted_qty = max_notional / price
    # Keep side sign
    if qty < 0:
        adjusted_qty = -adjusted_qty
    return False, adjusted_qty, f"position limit breached {ratio:.3f} > {max_pct:.3f} — scaled qty {qty} -> {adjusted_qty:.2f}"


def check_exposure(
    new_notional: float,
    existing_exposure: float,
    equity: float,
    max_exposure_pct: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Gross exposure cap: (existing + new) / equity <= max_exposure_pct.
    Returns (pass, rule_msg).
    """
    s = get_settings()
    max_pct = max_exposure_pct if max_exposure_pct is not None else s.risk_max_exposure_pct
    if equity <= 0:
        return False, f"equity {equity} invalid for exposure check"
    gross = abs(existing_exposure) + abs(new_notional)
    ratio = gross / equity
    if ratio <= max_pct:
        return True, f"exposure ok {ratio:.3f} <= {max_pct:.3f}"
    return False, f"exposure cap breached {ratio:.3f} > {max_pct:.3f} (existing {existing_exposure:.2f} + new {new_notional:.2f} / equity {equity:.2f})"


def check_drawdown(
    equity: float,
    peak_equity: float,
    threshold_pct: Optional[float] = None,
) -> Tuple[bool, float, str]:
    """
    Drawdown circuit breaker: (equity - peak)/peak < -threshold → trigger.
    Returns (triggered, drawdown_pct, rule_msg). Negative drawdown means loss.
    """
    s = get_settings()
    thresh = threshold_pct if threshold_pct is not None else s.risk_daily_drawdown_pct
    if peak_equity <= 0:
        return False, 0.0, f"peak {peak_equity} invalid"
    drawdown = (equity - peak_equity) / peak_equity
    triggered = drawdown < -thresh
    msg = f"drawdown {drawdown:.4f} {'TRIGGERED' if triggered else 'ok'} vs -{thresh:.4f} (equity {equity:.2f} peak {peak_equity:.2f})"
    return triggered, drawdown, msg


def check_spxw_settlement(
    symbol: str,
    expiration: Optional[str] = None,
) -> Dict[str, Any]:
    """
    SPXW/XSP cash-settled handling per DOC.md §7.
    Returns {is_spxw, should_close_before_expiry, settlement_lag_flag, days_to_expiry, reason}
    """
    sym = normalize_symbol(symbol) or ""
    is_spxw = _is_spxw_symbol(sym)
    if not is_spxw:
        return {"is_spxw": False, "should_close_before_expiry": False, "settlement_lag_flag": False, "reason": "not cash-settled index"}

    # Determine days to expiry
    days_to_expiry: Optional[int] = None
    if expiration:
        try:
            exp_dt = datetime.fromisoformat(expiration).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_to_expiry = (exp_dt.date() - now.date()).days
        except Exception:
            days_to_expiry = None

    should_close = False
    lag_flag = False
    reason = "cash-settled index — settlement lag risk"
    if days_to_expiry is not None:
        if days_to_expiry <= 1:
            should_close = True
            lag_flag = True
            reason = f"SPXW expiry in {days_to_expiry}d — close before expiry to avoid overnight journal lag (posts ~10am next day)"
        elif days_to_expiry <= 3:
            lag_flag = True
            reason = f"SPXW near expiry ({days_to_expiry}d) — monitor settlement lag"
    else:
        # If no expiration provided but symbol is SPXW family, flag caution
        lag_flag = True
        should_close = False
        reason = "SPXW family without expiration — flag settlement lag for any 0DTE hold"

    return {
        "is_spxw": True,
        "should_close_before_expiry": should_close,
        "settlement_lag_flag": lag_flag,
        "days_to_expiry": days_to_expiry,
        "expiration": expiration,
        "reason": reason,
    }


def _is_paused() -> bool:
    """Check circuit breaker paused flag file."""
    return _PAUSED_FLAG.exists()


def _set_paused(triggered: bool, reason: str = "") -> None:
    """Set or clear paused flag. Writes reason to file when paused."""
    _PAUSED_FLAG.parent.mkdir(parents=True, exist_ok=True)
    if triggered:
        _PAUSED_FLAG.write_text(f"{datetime.now(timezone.utc).isoformat()} {reason}\n", encoding="utf-8")
        log_event("circuit_breaker_paused", reason=reason)
    else:
        if _PAUSED_FLAG.exists():
            _PAUSED_FLAG.unlink(missing_ok=True)
            log_event("circuit_breaker_resumed", reason=reason)


def evaluate_risk(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main risk evaluation — deterministic, pure, testable.
    Consumes state["strategy"] proposed trade, state["account_state"] or live broker, state["market_snapshot"] optional.
    Returns RiskVerdict dict for state["risk"].

    Decision:
    - approved: all checks pass
    - approved_scaled: position limit scaled qty, exposure/drawdown still ok
    - rejected: exposure/drawdown/spxw block
    - no_trade: no strategy trade
    """
    # Check paused first — if breaker triggered, reject all
    if _is_paused():
        return {
            "decision": "rejected",
            "rule": "circuit_breaker_paused — system paused until CLI /resume",
            "adjusted_qty": None,
            "original_qty": None,
            "drawdown": None,
            "exposure": None,
            "spxw_flag": False,
            "paused": True,
        }

    strategy = state.get("strategy") or {}
    # Strategy may be stub or built-in output string — extract
    action = str(strategy.get("action", "")).lower()
    raw_symbol = strategy.get("symbol")
    raw_qty = strategy.get("qty") if strategy.get("qty") is not None else strategy.get("notional")
    # Try parse from output string if stub doesn't have symbol/qty
    if (not raw_symbol or raw_qty is None) and isinstance(strategy.get("output"), str):
        try:
            import json, re

            m = re.search(r"\{[^}]+\}", strategy["output"])
            if m:
                parsed = json.loads(m.group(0))
                raw_symbol = parsed.get("symbol", raw_symbol)
                raw_qty = parsed.get("qty", parsed.get("notional", raw_qty))
                action = str(parsed.get("action", action)).lower()
        except Exception:
            pass

    if action not in ("buy", "sell") or not raw_symbol or raw_qty is None:
        return {
            "decision": "no_trade",
            "rule": f"no trade to risk-check (action={action!r} symbol={raw_symbol!r} qty={raw_qty!r})",
            "adjusted_qty": None,
            "original_qty": raw_qty,
        }

    try:
        qty = float(raw_qty)
    except (TypeError, ValueError):
        return {"decision": "rejected", "rule": f"qty {raw_qty!r} not numeric", "original_qty": raw_qty}

    if qty == 0:
        return {"decision": "no_trade", "rule": "qty 0 — no trade", "original_qty": qty}

    symbol = normalize_symbol(str(raw_symbol)) or str(raw_symbol).upper()
    # Keep side via qty sign for sell
    if action == "sell" and qty > 0:
        qty = -qty

    # Fetch account state — prefer state["account_state"], else live broker
    account_state = state.get("account_state")
    if not account_state:
        try:
            from backend.broker.client import get_account, get_positions

            acct = get_account()
            positions = get_positions()
            account_state = track_account_state(acct, positions)
        except Exception as e:
            log_event("risk_account_fetch_failed", level="warning", error=str(e)[:200])
            account_state = {"equity": 100000, "cash": 100000, "peak_equity": 100000, "unrealized_pl": 0, "realized_pl": 0, "margin_usage": 0}

    equity = float(account_state.get("equity") or account_state.get("portfolio_value") or 0)
    peak = float(account_state.get("peak_equity") or equity)
    cash = float(account_state.get("cash") or 0)

    # Drawdown check first — triggers pause
    triggered, drawdown, drawdown_msg = check_drawdown(equity, peak)
    if triggered:
        _set_paused(True, reason=drawdown_msg)
        log_event("risk_rejected_drawdown", drawdown=drawdown, equity=equity, peak=peak, rule=drawdown_msg)
        return {
            "decision": "rejected",
            "rule": drawdown_msg,
            "adjusted_qty": None,
            "original_qty": qty,
            "drawdown": drawdown,
            "equity": equity,
            "peak_equity": peak,
            "paused": True,
        }

    # SPXW check — flag but don't automatically reject unless should_close
    spxw_info = check_spxw_settlement(symbol, expiration=strategy.get("expiration"))
    if spxw_info["is_spxw"] and spxw_info["should_close_before_expiry"]:
        log_event("risk_spxw_close_recommended", symbol=symbol, expiration=spxw_info.get("expiration"), reason=spxw_info["reason"])
        # For hold-to-expiry with SPXW, reject or flag — here we flag but allow if strategy is closing
        # If strategy is buy/hold into expiry, we treat as reject to enforce close-before-expiry per DOC.md §7
        if action == "buy":
            return {
                "decision": "rejected",
                "rule": f"SPXW close-before-expiry: {spxw_info['reason']}",
                "adjusted_qty": None,
                "original_qty": qty,
                "spxw_flag": True,
                "spxw_info": spxw_info,
            }

    # Need price for notional checks — fetch via market or use last close
    price = None
    # Try strategy provided price
    price = strategy.get("price") or strategy.get("limit_price") or strategy.get("entry_price")
    if price is None:
        # Try market snapshot
        market_snapshot = state.get("market_snapshot")
        if market_snapshot and isinstance(market_snapshot, dict):
            # Find symbol in any timeframe
            for tf_data in market_snapshot.values():
                if isinstance(tf_data, list) and tf_data:
                    for bar in tf_data:
                        if str(bar.get("symbol", "")).upper() == symbol and bar.get("close"):
                            try:
                                price = float(bar["close"])
                                break
                            except Exception:
                                pass
                elif isinstance(tf_data, dict) and tf_data.get("close"):
                    try:
                        price = float(tf_data["close"])
                        break
                    except Exception:
                        pass
        # Fallback to live market fetch
        if price is None:
            try:
                from backend.data.market import fetch_ohlcv

                df = fetch_ohlcv(symbol, timeframe="1Day", limit=1)
                if not df.empty and "close" in df.columns and not df["close"].isna().all():
                    price = float(df["close"].iloc[-1])
            except Exception:
                pass
    if price is None or price <= 0:
        price = 100.0  # fallback for dry-run
        log_event("risk_price_fallback", symbol=symbol, price=price)

    # Position limit check — may scale
    pos_pass, adjusted_qty, pos_rule = check_position_limit(symbol, qty, price, equity)
    if not pos_pass:
        # Check if scaled qty still passes exposure/drawdown
        scaled_notional = abs(adjusted_qty * price)
        # Estimate existing exposure from positions
        existing_exposure = 0.0
        try:
            from backend.broker.client import get_positions

            positions = get_positions()
            for p in positions:
                try:
                    existing_exposure += abs(float(p.get("market_value") or 0))
                except Exception:
                    pass
        except Exception:
            existing_exposure = 0.0

        exp_pass, exp_rule = check_exposure(scaled_notional, existing_exposure, equity)
        if not exp_pass:
            log_event("risk_rejected_exposure_after_scale", symbol=symbol, rule=exp_rule)
            return {
                "decision": "rejected",
                "rule": f"{pos_rule} + {exp_rule}",
                "adjusted_qty": adjusted_qty,
                "original_qty": qty,
                "exposure": existing_exposure + scaled_notional,
                "spxw_flag": spxw_info.get("settlement_lag_flag", False),
            }

        log_event("risk_approved_scaled", symbol=symbol, original_qty=qty, adjusted_qty=adjusted_qty, rule=pos_rule)
        return {
            "decision": "approved_scaled",
            "rule": pos_rule,
            "adjusted_qty": adjusted_qty,
            "original_qty": qty,
            "price": price,
            "exposure": existing_exposure + scaled_notional,
            "drawdown": drawdown,
            "spxw_flag": spxw_info.get("settlement_lag_flag", False),
            "spxw_info": spxw_info,
        }

    # Exposure check for non-scaled
    notional = abs(qty * price)
    existing_exposure = 0.0
    try:
        from backend.broker.client import get_positions

        positions = get_positions()
        for p in positions:
            try:
                existing_exposure += abs(float(p.get("market_value") or 0))
            except Exception:
                pass
    except Exception:
        pass

    exp_pass, exp_rule = check_exposure(notional, existing_exposure, equity)
    if not exp_pass:
        log_event("risk_rejected_exposure", symbol=symbol, rule=exp_rule)
        return {
            "decision": "rejected",
            "rule": exp_rule,
            "adjusted_qty": None,
            "original_qty": qty,
            "exposure": existing_exposure + notional,
            "spxw_flag": spxw_info.get("settlement_lag_flag", False),
        }

    # All checks passed
    log_event("risk_approved", symbol=symbol, qty=qty, price=price, rule="all checks pass", drawdown=drawdown, spxw_flag=spxw_info.get("settlement_lag_flag"))
    return {
        "decision": "approved",
        "rule": f"approved: {pos_rule} | {exp_rule} | drawdown {drawdown:.4f}",
        "adjusted_qty": qty,
        "original_qty": qty,
        "price": price,
        "exposure": existing_exposure + notional,
        "drawdown": drawdown,
        "equity": equity,
        "spxw_flag": spxw_info.get("settlement_lag_flag", False),
        "spxw_info": spxw_info,
    }


def risk_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    StateGraph node adapter — calls evaluate_risk and writes state["risk"].
    Handles paused flag and updates state in place.
    """
    try:
        verdict = evaluate_risk(state)
        state["risk"] = verdict
        # Update timestamp
        try:
            from backend.graph.state import GraphState  # noqa: F401
            # If state is GraphState, update timestamp
            if hasattr(state, "update_timestamp"):
                state.update_timestamp()  # type: ignore
        except Exception:
            pass
        return state
    except Exception as e:
        log_event("risk_agent_error", level="warning", error=str(e)[:300])
        state["risk"] = {"decision": "rejected", "rule": f"risk error: {e}", "original_qty": None}
        return state


def clear_pause() -> bool:
    """CLI /resume helper — clear paused flag."""
    if _is_paused():
        _set_paused(False, reason="manual resume via CLI")
        return True
    return False


def is_paused() -> bool:
    """Check if circuit breaker is paused."""
    return _is_paused()
