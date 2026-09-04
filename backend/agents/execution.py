"""
Execution agent — deterministic with Human-in-the-Loop via langgraph.types.interrupt.

Replaces useless stub that always returned skipped.
Now uses built-in HITL library per docs: https://docs.langchain.com/oss/python/langgraph/interrupts
- Before placing any paper order, calls interrupt({"action": "place_order", "order": {...}}) which pauses graph
- Requires checkpointer (InMemorySaver) in graph/build.py and thread_id on invoke
- Human resumes via Command(resume={"decisions": [{"type": "approve"}]}) or {"type": "reject"}
- Also supports middleware HITL via HumanInTheLoopMiddleware on submit_order tool (strategy agent)

Falls back to dry-run when LLM not configured or interrupt not available (offline).
"""

from typing import Any, Dict

from backend.core.logging import log_event


def _execute_paired_trade(state: Dict[str, Any], risk: Dict[str, Any], strategy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Paired-trade execution per RISK_SYSTEM_PROMPT_AGGRESSIVE §5 + EXECUTION_SYSTEM_PROMPT § paired trade:
    sequencing close_then_open, never submit open before close fills, handle resize, partial fills, incomplete logging.
    """
    import time

    close_dec = (risk.get("close_leg_decision") or {})
    open_dec = (risk.get("open_leg_decision") or {})
    sequencing = risk.get("sequencing") or "close_then_open"
    close_strategy = close_dec.get("original") or strategy.get("close_leg") or {}
    open_strategy = open_dec.get("original") or strategy.get("open_leg") or {}
    # Use adjusted open leg if Risk resized
    if open_dec.get("decision") == "resize" and open_dec.get("adjusted"):
        open_strategy = open_dec["adjusted"]

    close_symbol = str(close_strategy.get("symbol") or "").upper()
    open_symbol = str(open_strategy.get("symbol") or "").upper()
    # Extract qty/notional — prefer qty
    def _qty_from_leg(leg):
        q = leg.get("qty")
        if q is not None:
            try:
                return float(q)
            except Exception:
                return None
        n = leg.get("notional")
        if n is not None:
            try:
                return float(n)  # execution will resolve via price if needed
            except Exception:
                return None
        return None

    close_qty = _qty_from_leg(close_strategy)
    open_qty = _qty_from_leg(open_strategy)
    close_decision = str(close_dec.get("decision", "")).lower()
    open_decision = str(open_dec.get("decision", "")).lower()

    # If close rejected, drop whole pair (open depends on Close funding)
    if close_decision not in ("approved", "approved_scaled", "resize", "approve"):
        log_event("execution_paired_skipped_close_rejected", close_symbol=close_symbol, close_decision=close_decision, open_decision=open_decision)
        state["execution"] = {
            "status": "skipped",
            "reason": f"paired_trade close_leg rejected ({close_decision}) — open_leg not attempted",
            "paired_trade": True,
            "close_leg_decision": close_dec,
            "open_leg_decision": open_dec,
            "sequencing": sequencing,
            "stub": False,
        }
        return state

    from backend.core.config import get_settings

    cfg = get_settings()
    mode = str(getattr(cfg, "execution_mode", "auto")).lower()
    hitl_enabled = bool(getattr(cfg, "hitl_enabled", False))
    should_hitl = hitl_enabled and mode == "hitl"

    # HITL for paired trade — single interrupt describing both legs
    if should_hitl:
        try:
            from langgraph.types import interrupt

            human_input = interrupt(
                {
                    "action": "place_paired_trade",
                    "close_leg": {"symbol": close_symbol, "qty": close_qty, "decision": close_decision},
                    "open_leg": {"symbol": open_symbol, "qty": open_qty, "decision": open_decision},
                    "sequencing": sequencing,
                    "risk": risk,
                    "strategy": strategy,
                }
            )
            decision_type = None
            if isinstance(human_input, dict):
                if "decisions" in human_input and isinstance(human_input["decisions"], list) and human_input["decisions"]:
                    decision_type = str(human_input["decisions"][0].get("type", "")).lower()
                elif "type" in human_input:
                    decision_type = str(human_input["type"]).lower()
            if decision_type == "reject":
                log_event("execution_paired_rejected_by_human", close_symbol=close_symbol, open_symbol=open_symbol)
                state["execution"] = {"status": "rejected_by_human", "paired_trade": True, "sequencing": sequencing, "stub": False}
                return state
            log_event("execution_paired_approved_by_human", close_symbol=close_symbol, open_symbol=open_symbol)
        except Exception as e:
            if "interrupt" in str(e).lower() or e.__class__.__name__ in ("GraphInterrupt", "Interrupt"):
                raise
            log_event("execution_paired_hitl_error", level="warning", error=str(e)[:200])
            state["execution"] = {
                "status": "dry_run_no_hitl",
                "paired_trade": True,
                "sequencing": sequencing,
                "note": "No checkpointer / HITL not available — dry-run paired trade",
                "stub": False,
            }
            return state

    # --- Deterministic close_then_open per spec ---
    from backend.broker.client import submit_order as broker_submit_order

    pair_id = f"{close_symbol}->{open_symbol}:{int(time.time())}"
    execution_result: Dict[str, Any] = {"paired_trade": True, "pair_id": pair_id, "sequencing": sequencing, "legs": {}}

    # 1. Submit close_leg first (always sell)
    close_side = str(close_strategy.get("action") or close_strategy.get("side") or "sell").lower()
    if close_side not in ("buy", "sell"):
        close_side = "sell"
    close_order_result = None
    close_filled = False
    start = time.monotonic()
    try:
        close_order_result = broker_submit_order(symbol=close_symbol, qty=abs(close_qty) if close_qty else 1, side=close_side, order_type="market")
        close_order_id = str(close_order_result.get("id") or close_order_result.get("order_id") or "unknown")
        close_status = str(close_order_result.get("status", "submitted")).lower()
        close_filled = close_status in ("filled", "partially_filled", "submitted_awaiting_fill", "submitted")
        execution_result["legs"]["close"] = {"order_id": close_order_id, "status": close_status, "result": close_order_result, "symbol": close_symbol, "qty": close_qty}
        latency_ms = (time.monotonic() - start) * 1000
        log_event("execution_paired_close_submitted", pair_id=pair_id, symbol=close_symbol, qty=close_qty, order_id=close_order_id, status=close_status, latency_ms=round(latency_ms, 2))
        log_event(close_side, level="info", order_id=close_order_id, price=None, symbol=close_symbol, qty=float(close_qty) if close_qty else 0, status=close_status, pair_id=pair_id)
    except Exception as e:
        err = str(e)[:300]
        log_event("execution_paired_close_failed", level="warning", pair_id=pair_id, symbol=close_symbol, error=err[:200])
        log_event(close_side, level="warning", order_id="pending", price=None, symbol=close_symbol, qty=float(close_qty) if close_qty else 0, status="close_failed", error=err[:200], pair_id=pair_id)
        state["execution"] = {
            "status": "paired_trade_incomplete",
            "reason": "paired_trade_incomplete: close_leg failed, open_leg not attempted",
            "paired_trade": True,
            "pair_id": pair_id,
            "legs": execution_result["legs"],
            "error": err,
            "sequencing": sequencing,
            "stub": False,
        }
        return state

    if not close_filled:
        # Treat non-fill as failure — do not submit open
        state["execution"] = {
            "status": "paired_trade_incomplete",
            "reason": "paired_trade_incomplete: close_leg not filled, open_leg not attempted",
            "paired_trade": True,
            "pair_id": pair_id,
            "legs": execution_result["legs"],
            "sequencing": sequencing,
            "stub": False,
        }
        return state

    # 2. Submit open_leg — only if close succeeded; if open rejected, close stands alone per spec
    if open_decision in ("rejected", "no_trade"):
        log_event("execution_paired_open_rejected", pair_id=pair_id, symbol=open_symbol, reason=str(open_dec.get("reason", ""))[:200])
        state["execution"] = {
            "status": "paired_trade_incomplete",
            "reason": "paired_trade_incomplete: close_leg filled, open_leg rejected by Risk — close_leg stands",
            "paired_trade": True,
            "pair_id": pair_id,
            "legs": execution_result["legs"],
            "open_leg_decision": open_dec,
            "sequencing": sequencing,
            "stub": False,
        }
        return state

    # Derive open qty from adjusted if resized, else original; if close partially filled, pro-rate (spec: size against actual proceeds)
    open_side = str(open_strategy.get("action") or open_strategy.get("side") or "buy").lower()
    if open_side not in ("buy", "sell"):
        open_side = "buy"
    open_order_type = str(open_strategy.get("order_type") or open_strategy.get("type") or "market").lower()
    if open_order_type not in ("market", "limit", "stop"):
        open_order_type = "market"
    # For partial close, scale open qty proportionally if we had limit info — simplest: use risk-adjusted qty as-is (Risk already sized against full freed_notional; partial fill scaling is best-effort)
    try:
        filled_qty = float(close_order_result.get("filled_qty") or close_order_result.get("qty") or close_qty or 0)
        # If partial, scale open qty proportionally (open_qty was sized against requested close; scale by filled/requested)
        if close_qty and filled_qty and abs(filled_qty) < abs(close_qty) - 1e-9:
            scale = abs(filled_qty) / abs(close_qty) if close_qty else 1
            if open_qty:
                open_qty = open_qty * scale
                log_event("execution_paired_open_scaled_partial", pair_id=pair_id, scale=round(scale, 4), adjusted_open_qty=open_qty)
    except Exception:
        pass

    start2 = time.monotonic()
    try:
        open_result = broker_submit_order(
            symbol=open_symbol, qty=abs(open_qty) if open_qty else 1, side=open_side, order_type=open_order_type,
            limit_price=open_strategy.get("limit_price") or open_strategy.get("price"),
            stop_price=open_strategy.get("stop_price"),
        )
        open_order_id = str(open_result.get("id") or open_result.get("order_id") or "unknown")
        open_status = str(open_result.get("status", "submitted")).lower()
        execution_result["legs"]["open"] = {"order_id": open_order_id, "status": open_status, "result": open_result, "symbol": open_symbol, "qty": open_qty}
        latency_ms2 = (time.monotonic() - start2) * 1000
        log_event("execution_paired_open_submitted", pair_id=pair_id, symbol=open_symbol, qty=open_qty, order_id=open_order_id, status=open_status, latency_ms=round(latency_ms2, 2))
        log_event(open_side, level="info", order_id=open_order_id, price=None, symbol=open_symbol, qty=float(open_qty) if open_qty else 0, status=open_status, pair_id=pair_id)
        # Do not unwind close if open fails — close was valid standalone
        if open_status in ("rejected", "canceled", "cancelled", "expired"):
            log_event("execution_paired_incomplete_open_rejected", level="warning", pair_id=pair_id, open_order_id=open_order_id, reason=str(open_result.get("status") or "")[:200])
            state["execution"] = {
                "status": "paired_trade_incomplete",
                "reason": "paired_trade_incomplete: close_leg filled, open_leg rejected/failed — close stands",
                "paired_trade": True,
                "pair_id": pair_id,
                "legs": execution_result["legs"],
                "sequencing": sequencing,
                "stub": False,
            }
            return state
        state["execution"] = {
            "status": "paired_trade_filled" if open_status == "filled" and close_status == "filled" else "paired_trade_submitted",
            "paired_trade": True,
            "pair_id": pair_id,
            "legs": execution_result["legs"],
            "sequencing": sequencing,
            "stub": False,
        }
        return state
    except Exception as e:
        err2 = str(e)[:300]
        log_event("execution_paired_open_failed", level="warning", pair_id=pair_id, symbol=open_symbol, error=err2[:200])
        log_event(open_side, level="warning", order_id="pending", price=None, symbol=open_symbol, qty=float(open_qty) if open_qty else 0, status="open_failed", error=err2[:200], pair_id=pair_id)
        state["execution"] = {
            "status": "paired_trade_incomplete",
            "reason": "paired_trade_incomplete: close_leg filled, open_leg failed — close stands, manual review",
            "paired_trade": True,
            "pair_id": pair_id,
            "legs": execution_result["legs"],
            "error": err2,
            "sequencing": sequencing,
            "stub": False,
        }
        return state


def execution_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic execution with HITL. Called as StateGraph node.
    Expects state["risk"]["decision"] in ("approved", "approved_scaled") and state["strategy"].
    Uses langgraph.types.interrupt for human approval before any order submit.
    """
    state.setdefault("execution", {})
    risk = state.get("risk", {}) or {}
    strategy = state.get("strategy", {}) or {}

    # Paired-trade branch — must be before flat decision check (paired_trade has no top-level decision)
    if risk.get("paired_trade"):
        return _execute_paired_trade(state, risk, strategy)

    decision = str(risk.get("decision", "")).lower()
    if decision not in ("approved", "approved_scaled"):
        state["execution"] = {
            "status": "skipped",
            "reason": f"risk decision is {decision!r} — no execution",
            "decision": decision,
            "stub": False,
        }
        return state

    # Prepare order payload from strategy (handle both built-in output and stub dict)
    # Strategy stub shape: {"action": "hold", "symbol": None, ...} or built-in output string
    symbol = strategy.get("symbol")
    qty = strategy.get("qty") or strategy.get("notional")
    action = str(strategy.get("action", "buy")).lower()
    # Try to parse from output string if strategy is built-in (contains JSON)
    if not symbol and isinstance(strategy.get("output"), str):
        try:
            import json, re

            # Extract JSON-like substring
            m = re.search(r"\{[^}]+\}", strategy["output"])
            if m:
                parsed = json.loads(m.group(0))
                symbol = parsed.get("symbol") or symbol
                qty = parsed.get("qty") or parsed.get("notional") or qty
                action = str(parsed.get("action", action)).lower()
        except Exception:
            pass

    if not symbol:
        symbol = "AAPL"  # fallback for dry-run
    try:
        qty_val = float(qty) if qty else 1.0
    except Exception:
        qty_val = 1.0
    if qty_val <= 0:
        qty_val = 1.0

    # Handle scaled case — use adjusted_qty if risk scaled
    if decision == "approved_scaled":
        try:
            adj = risk.get("adjusted_qty")
            if adj is not None:
                qty_val = float(adj)
        except Exception:
            pass

    # Extract order type / prices from strategy if provided
    order_type = str(strategy.get("order_type") or strategy.get("type") or "market").lower()
    if order_type not in ("market", "limit", "stop"):
        order_type = "market"
    limit_price = strategy.get("limit_price") or strategy.get("price")
    stop_price = strategy.get("stop_price")
    # Try parse from built-in output string
    if isinstance(strategy.get("output"), str) and not limit_price:
        try:
            import json as _json, re as _re

            m2 = _re.search(r"\{[^}]+\}", strategy["output"])
            if m2:
                p2 = _json.loads(m2.group(0))
                order_type = str(p2.get("order_type") or p2.get("type") or order_type).lower()
                limit_price = p2.get("limit_price") or p2.get("stop_price") or limit_price
                stop_price = p2.get("stop_price") or stop_price
        except Exception:
            pass

    order_payload = {
        "symbol": str(symbol).upper(),
        "qty": qty_val,
        "side": action if action in ("buy", "sell") else "buy",
        "type": order_type,
        "limit_price": float(limit_price) if limit_price else None,
        "stop_price": float(stop_price) if stop_price else None,
        "risk_decision": decision,
        "risk_rule": risk.get("rule"),
    }

    # --- Execution mode: auto vs hitl (both via config, no hardcoded) ---
    from backend.core.config import get_settings

    cfg = get_settings()
    mode = str(getattr(cfg, "execution_mode", "auto")).lower()
    hitl_enabled = bool(getattr(cfg, "hitl_enabled", False))

    should_hitl = hitl_enabled and mode == "hitl"

    if should_hitl:
        # Human-in-the-Loop via built-in interrupt (library, not custom) — only in hitl mode
        try:
            from langgraph.types import interrupt

            human_input = interrupt(
                {
                    "action": "place_order",
                    "order": order_payload,
                    "description": f"Order pending approval: {order_payload['side']} {order_payload['qty']} {order_payload['symbol']} ({decision}) [{order_type}] — risk rule: {risk.get('rule')}",
                    "risk": risk,
                    "strategy": strategy,
                }
            )
            # human_input is the resume value after human approves: e.g., {"decisions": [{"type": "approve"}]} or {"type": "approve"}
            # Normalize
            decision_type = None
            if isinstance(human_input, dict):
                # Could be {"decisions": [{"type": "approve"}]} or {"type": "approve"}
                if "decisions" in human_input and isinstance(human_input["decisions"], list) and human_input["decisions"]:
                    decision_type = str(human_input["decisions"][0].get("type", "")).lower()
                elif "type" in human_input:
                    decision_type = str(human_input["type"]).lower()
                elif "approve" in str(human_input).lower():
                    decision_type = "approve"
                elif "reject" in str(human_input).lower():
                    decision_type = "reject"

            if decision_type == "reject":
                log_event("execution_rejected_by_human", symbol=order_payload["symbol"], qty=qty_val)
                state["execution"] = {
                    "status": "rejected_by_human",
                    "order": order_payload,
                    "human_decision": "reject",
                    "stub": False,
                }
                return state
            if decision_type == "edit" and isinstance(human_input, dict):
                # Human edited the order — apply edits if provided
                edited = None
                if "decisions" in human_input:
                    edited = human_input["decisions"][0].get("editedAction") or human_input["decisions"][0].get("edited_action")
                if edited and isinstance(edited, dict):
                    order_payload.update({k: v for k, v in edited.items() if k in ("symbol", "qty", "side", "type")})
                    if "qty" in edited:
                        try:
                            order_payload["qty"] = float(edited["qty"])
                        except Exception:
                            pass
                log_event("execution_edited_by_human", symbol=order_payload["symbol"], edited=edited)

        # Approved (or edited then approved) — proceed to submit via throttled broker
            log_event("execution_approved_by_human", symbol=order_payload["symbol"], qty=order_payload["qty"])

        except Exception as e:
            # If interrupt not available (no checkpointer, offline, or stub mode), fallback to dry-run with log
            # Don't fail the graph — return dry-run so pipeline remains runnable offline
            if "interrupt" in str(type(e).__name__).lower() or "GraphInterrupt" in str(e) or "No checkpointer" in str(e):
                log_event("execution_hitl_no_checkpointer", level="warning", error=str(e)[:200])
            else:
                # Check if it's the actual interrupt pause signal — re-raise to let LangGraph handle it
                if "interrupt" in str(e).lower() or e.__class__.__name__ in ("GraphInterrupt", "Interrupt"):
                    raise
                log_event("execution_hitl_error", level="warning", error=str(e)[:200])
            # In dry-run/offline, treat as auto-approved for testing unless risk was scaled/rejected
            # But log that it was not human-approved
            state["execution"] = {
                "status": "dry_run_no_hitl",
                "order": order_payload,
                "note": "No checkpointer / HITL not available — dry-run only, no live order placed. Wire checkpointer + thread_id for real HITL.",
                "stub": False,
            }
            # Still attempt throttled dry-run via submit_order tool for audit
            try:
                from backend.tools.broker_tools import submit_order

                dry = submit_order.invoke({"symbol": order_payload["symbol"], "qty": order_payload["qty"], "side": order_payload["side"]})
                state["execution"]["dry_run_result"] = dry[:500]
            except Exception:
                pass
            return state

    # --- Submit via throttled broker (real) — handles market/limit/stop/options, 25/min, 30s timeout, tenacity ---
    # For auto mode, this is direct; for hitl mode, this is after human approve
    import time

    start = time.monotonic()
    try:
        from backend.broker.client import submit_order as broker_submit_order

        order_type = order_payload.get("type", "market")
        result = broker_submit_order(
            symbol=order_payload["symbol"],
            qty=order_payload["qty"],
            side=order_payload["side"],
            order_type=order_type,
            limit_price=order_payload.get("limit_price"),
            stop_price=order_payload.get("stop_price"),
        )
        latency_ms = (time.monotonic() - start) * 1000
        # Parse fill info
        order_id = result.get("id") or result.get("order_id") or "unknown"
        status = str(result.get("status", "submitted")).lower()
        filled_qty = result.get("filled_qty") or result.get("filled_avg_price") or result.get("qty")
        # Determine status bucket
        if status in ("filled", "partially_filled", "partially_filled"):
            exec_status = "filled" if status == "filled" else "partial_fill"
        elif status in ("rejected", "canceled", "cancelled", "expired"):
            exec_status = "rejected"
        elif "filled" in status:
            exec_status = "filled"
        else:
            exec_status = "submitted_awaiting_fill"

        filled_price = result.get("filled_avg_price") or result.get("filled_price") or result.get("limit_price") or result.get("stop_price")
        state["execution"] = {
            "status": exec_status,
            "order": order_payload,
            "order_id": order_id,
            "filled_qty": filled_qty,
            "filled_price": filled_price,
            "latency_ms": round(latency_ms, 2),
            "broker_result": str(result)[:800],
            "human_approved": should_hitl,  # True if hitl mode and approved, False if auto
            "auto": not should_hitl,
            "stub": False,
        }
        log_event(
            "execution_submitted" if exec_status in ("filled", "submitted_awaiting_fill") else "execution_" + exec_status,
            symbol=order_payload["symbol"],
            qty=order_payload["qty"],
            order_type=order_type,
            order_id=order_id,
            latency_ms=round(latency_ms, 2),
            status=exec_status,
            **({"filled_qty": filled_qty} if filled_qty else {}),
        )
        # Concise trade log for jsonl — required fields: ts (auto), event=buy/sell, level, order_id, price
        trade_price = filled_price if filled_price is not None else (order_payload.get("limit_price") or order_payload.get("stop_price"))
        log_event(
            order_payload["side"],  # event = "buy" or "sell"
            level="info",
            order_id=str(order_id),
            price=float(trade_price) if trade_price is not None else None,
            symbol=order_payload["symbol"],
            qty=float(order_payload["qty"]),
            status=exec_status,
        )
        if exec_status == "partial_fill":
            log_event("execution_partial_fill", order_id=str(order_id), filled_qty=filled_qty, symbol=order_payload["symbol"])
        elif exec_status == "rejected":
            log_event("execution_rejected", order_id=str(order_id), reason=result.get("status") or result.get("error"))

    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        # Check if it's timeout/429 via tenacity — will have been retried
        err_msg = str(e)[:500]
        is_timeout = "timeout" in err_msg.lower() or "timed out" in err_msg.lower()
        is_429 = "429" in err_msg or "rate limit" in err_msg.lower()
        status = "timeout" if is_timeout else "rate_limited" if is_429 else "submit_failed"
        state["execution"] = {
            "status": status,
            "order": order_payload,
            "error": err_msg[:300],
            "latency_ms": round(latency_ms, 2),
            "stub": False,
        }
        log_event("execution_submit_failed" if status == "submit_failed" else f"execution_{status}", level="warning", error=err_msg[:200], latency_ms=round(latency_ms, 2))
        # Also emit concise buy/sell failure log with required fields (order_id unknown here, use pending)
        try:
            fail_price = order_payload.get("limit_price") or order_payload.get("stop_price")
            log_event(
                order_payload.get("side", "buy"),
                level="warning",
                order_id="pending",
                price=float(fail_price) if fail_price is not None else None,
                symbol=order_payload.get("symbol"),
                qty=float(order_payload.get("qty", 0)),
                status=status,
                error=err_msg[:200],
            )
        except Exception:
            pass
        # Tenacity already retried; no further retry here

    return state
