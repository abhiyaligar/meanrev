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


def _to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_account_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Shared account-state loader used by both single-trade and paired-trade evaluation,
    so the two paths never diverge on how equity/cash/peak are derived.
    """
    account_state = state.get("account_state")
    if account_state:
        return account_state
    try:
        from backend.broker.client import get_account, get_positions

        acct = get_account()
        positions = get_positions()
        return track_account_state(acct, positions)
    except Exception as e:
        log_event("risk_account_fetch_failed", level="warning", error=str(e)[:200])
        return {"equity": 100000, "cash": 100000, "peak_equity": 100000, "unrealized_pl": 0, "realized_pl": 0, "margin_usage": 0}


def _load_positions() -> List[Dict[str, Any]]:
    """Fetch current positions, tolerating broker/client failures."""
    try:
        from backend.broker.client import get_positions

        return get_positions() or []
    except Exception:
        return []


def _existing_exposure(positions: List[Dict[str, Any]]) -> float:
    """Sum abs(market_value) across positions — matches the pre-existing single-trade logic."""
    exposure = 0.0
    for p in positions:
        try:
            exposure += abs(float(p.get("market_value") or 0))
        except Exception:
            pass
    return exposure


def _find_position(positions: List[Dict[str, Any]], symbol: str) -> Optional[Dict[str, Any]]:
    """Find a held position by normalized symbol match."""
    norm = normalize_symbol(symbol) or symbol.upper()
    for p in positions:
        p_sym = normalize_symbol(str(p.get("symbol", ""))) or str(p.get("symbol", "")).upper()
        if p_sym == norm:
            return p
    return None


def _resolve_price(symbol: str, leg: Dict[str, Any], state: Dict[str, Any]) -> float:
    """
    Resolve a price for a leg — mirrors the single-trade price resolution order:
    leg-provided price -> market_snapshot -> live fetch_ohlcv -> fallback 100.0.
    """
    price = leg.get("price") or leg.get("limit_price") or leg.get("entry_price")
    if price is not None:
        try:
            return float(price)
        except (TypeError, ValueError):
            pass

    market_snapshot = state.get("market_snapshot")
    if market_snapshot and isinstance(market_snapshot, dict):
        for tf_data in market_snapshot.values():
            if isinstance(tf_data, list) and tf_data:
                for bar in tf_data:
                    if str(bar.get("symbol", "")).upper() == symbol and bar.get("close"):
                        try:
                            return float(bar["close"])
                        except Exception:
                            pass
            elif isinstance(tf_data, dict) and tf_data.get("close"):
                try:
                    return float(tf_data["close"])
                except Exception:
                    pass

    try:
        from backend.data.market import fetch_ohlcv

        df = fetch_ohlcv(symbol, timeframe="1Day", limit=1)
        if not df.empty and "close" in df.columns and not df["close"].isna().all():
            return float(df["close"].iloc[-1])
    except Exception:
        pass

    log_event("risk_price_fallback", symbol=symbol, price=100.0)
    return 100.0


def _parse_strategy_payload(strategy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize the raw strategy dict, including the string-output fallback case.
    Fixes the prior regex-based fallback: paired_trade/arb payloads are nested JSON
    (close_leg/open_leg are objects), and a single flat `\\{[^}]+\\}` regex cannot
    capture nested braces — it will silently mis-parse or drop those keys. Try a full
    json.loads() first; only fall back to the flat-regex extraction for legacy
    single-level outputs that aren't valid JSON as-is.
    """
    if strategy.get("paired_trade") or strategy.get("arb") is not None or strategy.get("action"):
        return strategy

    output = strategy.get("output")
    if not isinstance(output, str):
        return strategy

    import json

    try:
        parsed = json.loads(output)
        if isinstance(parsed, dict):
            merged = dict(strategy)
            merged.update(parsed)
            return merged
    except Exception:
        pass

    # Legacy fallback: flat single-level JSON only (no nested objects).
    try:
        import re

        m = re.search(r"\{[^{}]+\}", output)
        if m:
            parsed = json.loads(m.group(0))
            merged = dict(strategy)
            merged.update(parsed)
            return merged
    except Exception:
        pass

    return strategy


def evaluate_paired_trade(state: Dict[str, Any], strategy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate a {paired_trade: true, close_leg, open_leg, rationale} proposal per
    STRATEGY_SYSTEM_PROMPT's Capital Reallocation contract:
    - close_leg is validated against the actual held position (not the position/exposure caps —
      reducing exposure is never itself capped).
    - open_leg is checked against equity/exposure state AS IT WOULD EXIST AFTER close_leg fills,
      not current state — otherwise a valid reallocation gets rejected the same way a lone buy did.
    - Returns the paired decision shape: close_leg_decision / open_leg_decision / sequencing.
    """
    close_leg = strategy.get("close_leg") or {}
    open_leg = strategy.get("open_leg") or {}

    close_symbol_raw = close_leg.get("symbol")
    open_symbol_raw = open_leg.get("symbol")
    if not close_symbol_raw or not open_symbol_raw:
        return {
            "paired_trade": True,
            "close_leg_decision": {"decision": "reject", "original": close_leg, "reason": "close_leg missing symbol"},
            "open_leg_decision": {"decision": "reject", "original": open_leg, "reason": "open_leg missing symbol"},
            "sequencing": "close_then_open",
            "circuit_breaker_active": _is_paused(),
        }

    close_symbol = normalize_symbol(str(close_symbol_raw)) or str(close_symbol_raw).upper()
    open_symbol = normalize_symbol(str(open_symbol_raw)) or str(open_symbol_raw).upper()

    account_state = _load_account_state(state)
    equity = _to_float(account_state.get("equity") or account_state.get("portfolio_value"))
    peak = _to_float(account_state.get("peak_equity"), equity)

    # Drawdown gates the whole pair, same as a single proposal.
    triggered, drawdown, drawdown_msg = check_drawdown(equity, peak)
    if triggered:
        _set_paused(True, reason=drawdown_msg)
        log_event("risk_rejected_drawdown_paired", drawdown=drawdown, equity=equity, peak=peak, rule=drawdown_msg)
        rejected = {"decision": "reject", "reason": drawdown_msg}
        return {
            "paired_trade": True,
            "close_leg_decision": {**rejected, "original": close_leg},
            "open_leg_decision": {**rejected, "original": open_leg},
            "sequencing": "close_then_open",
            "circuit_breaker_active": True,
        }

    positions = _load_positions()
    existing_exposure = _existing_exposure(positions)

    # --- close_leg: validate against the actual held position ---
    # Fix PG weekend bug: never use _resolve_price() fallback (100.0) for close_leg freed_notional — broker's held_market_value is live even when 1Day is empty
    held = _find_position(positions, close_symbol)
    requested_close_qty = close_leg.get("qty")
    requested_close_notional = close_leg.get("notional")

    if held is None:
        close_decision = {
            "decision": "reject",
            "original": close_leg,
            "reason": f"close_leg symbol {close_symbol} not found in current positions — cannot close what isn't held",
        }
        log_event("risk_rejected_paired_close_no_position", symbol=close_symbol)
        return {
            "paired_trade": True,
            "close_leg_decision": close_decision,
            "open_leg_decision": {"decision": "reject", "original": open_leg, "reason": "close_leg invalid — open_leg not evaluated, funding is not confirmed"},
            "sequencing": "close_then_open",
            "circuit_breaker_active": False,
        }

    held_qty = _to_float(held.get("qty") or held.get("quantity"))
    held_market_value = abs(_to_float(held.get("market_value")))

    # Resolve requested close qty/notional against what's actually held; cap to held size.
    # freed_notional is derived proportionally from held_market_value (broker live) not from a fetched price that may fallback to 100.0
    if requested_close_qty is not None:
        req_qty = abs(_to_float(requested_close_qty))
        if req_qty > abs(held_qty) + 1e-9:
            close_decision = {
                "decision": "reject",
                "original": close_leg,
                "reason": f"close_leg qty {req_qty} exceeds held qty {abs(held_qty):.6f} for {close_symbol}",
            }
            log_event("risk_rejected_paired_close_qty", symbol=close_symbol, requested=req_qty, held=held_qty)
            return {
                "paired_trade": True,
                "close_leg_decision": close_decision,
                "open_leg_decision": {"decision": "reject", "original": open_leg, "reason": "close_leg invalid — open_leg not evaluated, funding is not confirmed"},
                "sequencing": "close_then_open",
                "circuit_breaker_active": False,
            }
        # Proportional: e.g. PG held 6 shares market_value ~1020, req 6 => 1020; req 3 => 510 — avoids 100.0 *6 =600 bug
        if abs(held_qty) > 1e-9 and held_market_value > 0:
            freed_notional = held_market_value * (req_qty / abs(held_qty))
        else:
            # Fallback only if broker data missing — resolve price then multiply
            close_price = _resolve_price(close_symbol, close_leg, state)
            freed_notional = req_qty * close_price
            log_event("risk_close_fallback_price", level="warning", symbol=close_symbol, price=close_price, reason="held_market_value unavailable, used _resolve_price")
    elif requested_close_notional is not None:
        req_notional = abs(_to_float(requested_close_notional))
        if req_notional > held_market_value + 1e-6:
            close_decision = {
                "decision": "reject",
                "original": close_leg,
                "reason": f"close_leg notional {req_notional:.2f} exceeds held market value {held_market_value:.2f} for {close_symbol}",
            }
            log_event("risk_rejected_paired_close_notional", symbol=close_symbol, requested=req_notional, held=held_market_value)
            return {
                "paired_trade": True,
                "close_leg_decision": close_decision,
                "open_leg_decision": {"decision": "reject", "original": open_leg, "reason": "close_leg invalid — open_leg not evaluated, funding is not confirmed"},
                "sequencing": "close_then_open",
                "circuit_breaker_active": False,
            }
        freed_notional = req_notional
    else:
        close_decision = {"decision": "reject", "original": close_leg, "reason": "close_leg missing qty and notional"}
        return {
            "paired_trade": True,
            "close_leg_decision": close_decision,
            "open_leg_decision": {"decision": "reject", "original": open_leg, "reason": "close_leg invalid — open_leg not evaluated, funding is not confirmed"},
            "sequencing": "close_then_open",
            "circuit_breaker_active": False,
        }

    close_decision = {
        "decision": "approve",
        "original": close_leg,
        "reason": f"close_leg ok — reduces {close_symbol} exposure by {freed_notional:.2f}, within held {held_market_value:.2f}",
    }
    log_event("risk_approved_paired_close", symbol=close_symbol, freed_notional=freed_notional)

    # --- open_leg: evaluate against POST-CLOSE state, not current state ---
    post_close_exposure = max(0.0, existing_exposure - freed_notional)

    open_price = _resolve_price(open_symbol, open_leg, state)
    open_qty_raw = open_leg.get("qty")
    open_notional_raw = open_leg.get("notional")
    if open_qty_raw is not None:
        open_qty = _to_float(open_qty_raw)
    elif open_notional_raw is not None and open_price > 0:
        open_qty = _to_float(open_notional_raw) / open_price
    else:
        open_decision = {"decision": "reject", "original": open_leg, "reason": "open_leg missing qty and notional"}
        return {
            "paired_trade": True,
            "close_leg_decision": close_decision,
            "open_leg_decision": open_decision,
            "sequencing": "close_then_open",
            "circuit_breaker_active": False,
        }

    pos_pass, adjusted_qty, pos_rule = check_position_limit(open_symbol, open_qty, open_price, equity)
    effective_qty = adjusted_qty if not pos_pass else open_qty
    effective_notional = abs(effective_qty * open_price)

    exp_pass, exp_rule = check_exposure(effective_notional, post_close_exposure, equity)

    if not exp_pass:
        # Even after freeing capital, open_leg doesn't fit — reject open_leg, close_leg stands.
        open_decision = {
            "decision": "reject",
            "original": open_leg,
            "reason": f"open_leg still breaches exposure after freeing {freed_notional:.2f} from close_leg — {exp_rule}",
        }
        log_event("risk_rejected_paired_open_exposure", symbol=open_symbol, rule=exp_rule, post_close_exposure=post_close_exposure)
    elif not pos_pass:
        open_decision = {
            "decision": "resize",
            "original": open_leg,
            "adjusted": {**open_leg, "qty": adjusted_qty, "notional": None},
            "reason": f"open_leg resized against post-close state — {pos_rule}",
        }
        log_event("risk_approved_paired_open_resized", symbol=open_symbol, original_qty=open_qty, adjusted_qty=adjusted_qty)
    else:
        open_decision = {
            "decision": "approve",
            "original": open_leg,
            "adjusted": None,
            "reason": f"open_leg ok against post-close exposure — {exp_rule} (post-close base {post_close_exposure:.2f})",
        }
        log_event("risk_approved_paired_open", symbol=open_symbol, qty=effective_qty, post_close_exposure=post_close_exposure)

    return {
        "paired_trade": True,
        "close_leg_decision": close_decision,
        "open_leg_decision": open_decision,
        "sequencing": "close_then_open",
        "circuit_breaker_active": False,
        "drawdown": drawdown,
        "equity": equity,
        "existing_exposure": existing_exposure,
        "post_close_exposure": post_close_exposure,
    }


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
    - paired_trade: routed to evaluate_paired_trade() — returns the two-leg decision shape
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
    strategy = _parse_strategy_payload(strategy)

    # Route paired_trade proposals to their own evaluator — separate shape, separate rules.
    if strategy.get("paired_trade"):
        return evaluate_paired_trade(state, strategy)

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
    account_state = _load_account_state(state)

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
    price = _resolve_price(symbol, strategy, state)

    # Position limit check — may scale
    pos_pass, adjusted_qty, pos_rule = check_position_limit(symbol, qty, price, equity)
    if not pos_pass:
        # Check if scaled qty still passes exposure/drawdown
        scaled_notional = abs(adjusted_qty * price)
        # Estimate existing exposure from positions
        existing_exposure = _existing_exposure(_load_positions())

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
    existing_exposure = _existing_exposure(_load_positions())

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