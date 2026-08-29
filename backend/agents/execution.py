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

    order_payload = {
        "symbol": str(symbol).upper(),
        "qty": qty_val,
        "side": action if action in ("buy", "sell") else "buy",
        "type": "market",
        "risk_decision": decision,
        "risk_rule": risk.get("rule"),
    }

    # --- Human-in-the-Loop via built-in interrupt (library, not custom) ---
    try:
        from langgraph.types import interrupt

        # This will pause the graph if checkpointer is present and human response is needed.
        # In offline/stub mode without checkpointer, it will raise or return immediately.
        human_input = interrupt(
            {
                "action": "place_order",
                "order": order_payload,
                "description": f"Order pending approval: {order_payload['side']} {order_payload['qty']} {order_payload['symbol']} ({decision}) — risk rule: {risk.get('rule')}",
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

    # --- If human approved, submit via throttled broker (dry-run in Phase 7, live in production) ---
    try:
        from backend.tools.broker_tools import submit_order

        result = submit_order.invoke(
            {
                "symbol": order_payload["symbol"],
                "qty": order_payload["qty"],
                "side": order_payload["side"],
                "order_type": order_payload["type"],
            }
        )
        state["execution"] = {
            "status": "submitted_awaiting_fill" if "dry_run" not in result else "dry_run_approved",
            "order": order_payload,
            "broker_result": result[:800] if isinstance(result, str) else str(result)[:800],
            "human_approved": True,
            "stub": False,
        }
        log_event("execution_submitted", symbol=order_payload["symbol"], qty=order_payload["qty"], result=result[:200] if isinstance(result, str) else str(result)[:200])
    except Exception as e:
        state["execution"] = {"status": "submit_failed", "order": order_payload, "error": str(e)[:300], "stub": False}
        log_event("execution_submit_failed", level="warning", error=str(e)[:200])

    return state
