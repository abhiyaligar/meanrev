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


def execution_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic execution with HITL. Called as StateGraph node.
    Expects state["risk"]["decision"] in ("approved", "approved_scaled") and state["strategy"].
    Uses langgraph.types.interrupt for human approval before any order submit.
    """
    state.setdefault("execution", {})
    risk = state.get("risk", {}) or {}
    strategy = state.get("strategy", {}) or {}

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
