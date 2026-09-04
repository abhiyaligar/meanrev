"""
Graph build — built-in LangChain create_agent + LangGraph StateGraph per docs.

Per langchain-docs MCP (oss/python/langchain/overview#create-an-agent, releases/langchain-v1):
- Standard way is create_agent(model="openrouter:...", tools=[...], system_prompt=..., middleware=[...])
  which is built on the core loop: model → tool choice → ToolNode → finish
- LangGraph StateGraph is for multi-agent wiring with deterministic nodes (risk, execution) and
  conditional routing (risk approved → execution, else END)
- Middleware: ToolCallLimitMiddleware for loop guard, custom wrap_tool_call for error handling
- Persistence: InMemorySaver checkpointer for thread_id support

Wiring (DOC.md §3): research (built-in) → strategy (built-in) → risk (deterministic) → execution (deterministic)
Models selector-driven via LLM_MODEL_* — no hardcoded strings.
No useless custom dict handling — each node maps built-in agent output directly to GraphState.
"""

from typing import Any, Dict

from backend.core.config import get_settings
from backend.core.logging import log_event
from backend.core.system_prompt import GRAPH_RESEARCH_PROMPT, GRAPH_STRATEGY_PROMPT_TEMPLATE
from backend.core.utils import get_model_id


def _model_id(agent: str) -> str:
    return get_model_id(agent)


def build_graph(checkpointer=None):
    """
    Build LangGraph with built-in agents per docs.

    Returns compiled graph with .invoke(state, config={"configurable": {"thread_id": ...}}).
    Uses InMemorySaver if no checkpointer provided.
    Falls back to stub chain if LLM not configured, so imports never fail.
    """
    # Fallback to stub if LLM not configured — keeps pipeline runnable offline per VULN 5
    try:
        if not get_settings().is_llm_configured():
            log_event("graph_build_stub_no_llm", level="warning", reason="LLM not configured, using stub chain")
            return _build_stub_graph()
    except Exception:
        pass

    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph

        from backend.agents.execution import execution_agent
        from backend.agents.risk import risk_agent
        from backend.graph.state import GraphState

        # Built-in agents per docs — create via factory that uses create_agent + ToolNode internally
        from backend.agents.research import get_research_agent
        from backend.agents.strategy import get_strategy_agent

        research_agent = get_research_agent()
        strategy_agent = get_strategy_agent()

        if research_agent is None or strategy_agent is None:
            log_event("graph_build_agents_missing", level="warning", reason="create_agent returned None")
            return _build_stub_graph()

        if checkpointer is None:
            checkpointer = InMemorySaver()

        def _to_dict(s: Any) -> Dict[str, Any]:
            # Normalize GraphState object or dict to plain dict for ** spread and .get
            if hasattr(s, "model_dump"):
                try:
                    return s.model_dump()
                except Exception:
                    pass
            if isinstance(s, dict):
                return dict(s)
            try:
                return dict(s)
            except Exception:
                # Fallback via get
                return {k: s.get(k) for k in ("messages", "research", "strategy", "risk", "execution", "market_snapshot", "account_state", "reporting_context") if s.get(k) is not None}

        def _human_research_summary(content: str, research_dict: Dict[str, Any]) -> str:
            """Build human readable research summary from validated output."""
            try:
                # Try to parse JSON from content
                import json, re

                # Extract sentiment/regime/catalyst if research_dict has them
                sentiment = research_dict.get("sentiment") or "neutral"
                regime = research_dict.get("regime") or "neutral"
                catalyst = research_dict.get("catalyst_summary") or research_dict.get("summary") or ""
                if not catalyst and content:
                    # Try to extract from raw content
                    m = re.search(r"\{[^}]+\}", content, re.DOTALL)
                    if m:
                        try:
                            j = json.loads(m.group(0))
                            sentiment = j.get("sentiment", sentiment)
                            regime = j.get("regime", regime)
                            catalyst = j.get("catalyst_summary", catalyst)
                        except Exception:
                            pass
                    if not catalyst:
                        # Strip code fences and take first 300 chars of content
                        clean = re.sub(r"```[a-z]*\n?", "", content).replace("```", "").strip()
                        # Remove JSON braces artifacts
                        clean = clean.replace("{", "").replace("}", "").replace("\\", "").strip()
                        catalyst = clean[:400] if clean else "No catalyst summary"
                # Human readable
                return f"Sentiment: {sentiment} | Regime: {regime}\nCatalyst: {catalyst[:400]}"
            except Exception:
                return content[:500].replace("{", "").replace("}", "").replace("\\", "").strip()

        def _human_strategy_summary(content: str, s: Dict[str, Any]) -> Dict[str, Any]:
            """Ensure strategy output is human readable and not empty; fallback to deterministic if empty."""
            text = (content or "").strip()
            # Clean code fences and braces for display
            import re, json

            if not text or len(text) < 5:
                # Empty LLM output — generate deterministic fallback trade proposal
                try:
                    from backend.data.market import fetch_ohlcv
                    import pandas as pd

                    # Use BTC/USD if user requested crypto in prompt, else AAPL
                    prompt_text = str(s.get("messages", [])[-1].get("content") if isinstance(s.get("messages", [])[-1], dict) else getattr(s.get("messages", [])[-1], "content", "")) if s.get("messages") else ""
                    symbol = "BTC/USD" if "BTC" in prompt_text.upper() else "AAPL"
                    df = fetch_ohlcv(symbol, limit=5)
                    close = float(df["close"].iloc[-1]) if not df.empty and "close" in df.columns and not df["close"].isna().all() else (85000 if "BTC" in symbol else 150.0)
                    atr = float(df["atr"].iloc[-1]) if not df.empty and "atr" in df.columns and not df["atr"].isna().all() else (800 if "BTC" in symbol else 2.0)
                    # Simple ATR sizing
                    qty = round(min(100000 * 0.01 / atr, 100000 * 0.15 / close), 2) if atr else 1
                    return {
                        "output": f"Buy {qty} {symbol} @ ~{close:.2f} (ATR {atr:.2f}) | Stop {close - 1.5*atr:.2f} | Target {close + 2.5*atr:.2f} | Fallback deterministic (LLM empty)",
                        "action": "buy",
                        "symbol": symbol,
                        "qty": qty,
                        "stop_price": round(close - 1.5 * atr, 2),
                        "target_price": round(close + 2.5 * atr, 2),
                        "rationale": "Fallback deterministic: LLM returned empty, using ATR-based sizing",
                        "fallback": True,
                    }
                except Exception:
                    return {
                        "output": "Buy 1 AAPL @ 150.00 — fallback deterministic",
                        "action": "buy",
                        "symbol": "AAPL",
                        "qty": 1,
                        "fallback": True,
                    }
            # Clean for human readable
            clean = re.sub(r"```[a-z]*\n?", "", text).replace("```", "").strip()
            # Try to extract JSON and format nicely — handle nested paired_trade/arb correctly (full json.loads first, like risk.py _parse_strategy_payload)
            try:
                j = None
                # Try full parse first (handles nested close_leg/open_leg)
                try:
                    j = json.loads(clean)
                    if not isinstance(j, dict):
                        j = None
                except Exception:
                    j = None
                if j is None:
                    m = re.search(r"\{[^{}]+\}", clean, re.DOTALL)
                    if m:
                        j = json.loads(m.group(0))
                if j and isinstance(j, dict):
                    # Paired-trade shape — preserve as-is, build human output from legs
                    if j.get("paired_trade"):
                        close = j.get("close_leg") or {}
                        open_leg = j.get("open_leg") or {}
                        return {
                            "output": f"PAIRED {close.get('symbol','?')}->{open_leg.get('symbol','?')} | {j.get('rationale','')[:200]}",
                            **j,
                        }
                    if j.get("arb"):
                        return {"output": f"ARB {j.get('arb_pct','?')}% | {j.get('rationale','')[:200]}", **j}
                    # Single-trade flat shape
                    action = j.get("action", "hold")
                    sym = j.get("symbol", "AAPL")
                    qty = j.get("qty", "?")
                    return {
                        "output": f"{action.upper()} {qty} {sym} | {j.get('rationale', '')[:200]}",
                        "action": action,
                        "symbol": sym,
                        "qty": qty,
                        **j,
                    }
            except Exception:
                pass
            # Return cleaned text as output
            human = clean.replace("{", "").replace("}", "").replace("\\", "").replace('"', "").strip()
            # Also try to ensure we have action/symbol/qty for risk
            return {"output": human[:600], "action": "buy" if "buy" in human.lower() else "hold", "symbol": "BTC/USD" if "BTC" in human else "AAPL", "qty": 1}

        def research_node(state: Any) -> Dict[str, Any]:
            """Built-in research agent node — single invoke, map output to human readable GraphState."""
            s = _to_dict(state)
            msgs = s.get("messages", [{"role": "user", "content": GRAPH_RESEARCH_PROMPT}])
            result = research_agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            raw = str(content)
            # Build human readable via helper
            human = _human_research_summary(raw, s.get("research", {}))
            # Also try to get structured research from agent if available
            structured = {}
            try:
                import json, re

                m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
                if m:
                    j = json.loads(m.group(0))
                    structured = j
            except Exception:
                pass
            research_val = {
                "output": human,
                "raw_output": raw[:1000],
                "sentiment": structured.get("sentiment", "neutral"),
                "regime": structured.get("regime", "neutral"),
                "catalyst_summary": structured.get("catalyst_summary", human[:400]),
                "agent": "research",
                "model": _model_id("research"),
                "built_in": True,
            }
            return {
                **s,
                "research": research_val,
                "messages": result.get("messages", msgs),
            }

        def strategy_node(state: Any) -> Dict[str, Any]:
            s = _to_dict(state)
            research_out = s.get("research", {})
            # Extract user_request for template (Top25 list) — fallback to last message
            user_req = ""
            try:
                msgs = s.get("messages", [])
                if msgs:
                    last = msgs[-1]
                    user_req = last.get("content") if isinstance(last, dict) else getattr(last, "content", "") or ""
                    user_req = str(user_req)[:1500]
                    # If last is already the research prompt, go one back
                    if "Research output:" in user_req and len(msgs) > 1:
                        prev = msgs[-2]
                        user_req = prev.get("content") if isinstance(prev, dict) else getattr(prev, "content", "") or ""
                        user_req = str(user_req)[:1500]
            except Exception:
                user_req = ""
            # Safely format — handle both old {research} only and new {research,user_request}
            try:
                if "{user_request}" in GRAPH_STRATEGY_PROMPT_TEMPLATE:
                    prompt = GRAPH_STRATEGY_PROMPT_TEMPLATE.format(research=research_out, user_request=user_req or "general")
                else:
                    prompt = GRAPH_STRATEGY_PROMPT_TEMPLATE.format(research=research_out)
            except KeyError as e:
                # Fallback if template missing keys
                prompt = f"Research: {research_out}. User request: {user_req}. Synthesize and propose trade."
            msgs = s.get("messages", []) + [{"role": "user", "content": prompt}]
            result = strategy_agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            raw = str(content)
            human_dict = _human_strategy_summary(raw, s)
            # Merge human_dict into strategy
            strategy_val = {
                **human_dict,
                "agent": "strategy",
                "model": _model_id("strategy"),
                "built_in": True,
                "raw_output": raw[:1000],
            }
            # Ensure required fields for risk
            if "action" not in strategy_val:
                strategy_val["action"] = human_dict.get("action", "hold")
            if "symbol" not in strategy_val:
                strategy_val["symbol"] = human_dict.get("symbol", "AAPL")
            if "qty" not in strategy_val:
                strategy_val["qty"] = human_dict.get("qty", 1)
            return {
                **s,
                "strategy": strategy_val,
                "messages": result.get("messages", msgs),
            }

        def reallocate_node(state: Dict[str, Any]) -> Dict[str, Any]:
            """
            Deterministic capital reallocation shim — Option B.
            If strategy proposed a lone buy that would breach exposure, rewrite it to a paired_trade
            (close weakest holding → open new) before Risk sees it. No LLM, pure math.
            Mirrors risk.py helpers so exposure math never drifts.
            """
            s = _to_dict(state)
            strat = s.get("strategy") or {}
            # Already paired/arb/hold → no rewrite
            if strat.get("paired_trade") or strat.get("arb"):
                return s
            action = str(strat.get("action", "")).lower()
            if action not in ("buy",):
                return s  # only buys need freeing; sells already reduce exposure
            raw_symbol = strat.get("symbol")
            raw_qty = strat.get("qty") if strat.get("qty") is not None else strat.get("notional")
            if not raw_symbol or raw_qty is None:
                return s
            try:
                qty = float(raw_qty)
                if qty <= 0:
                    return s
            except Exception:
                return s
            symbol = str(raw_symbol).strip().upper()
            # For sells, qty sign handled in risk; here keep positive
            try:
                # Reuse risk's helpers for consistent equity/exposure/price math
                from backend.agents.risk import _existing_exposure, _load_account_state, _load_positions, _resolve_price

                account_state = _load_account_state(s)
                equity = float(account_state.get("equity") or account_state.get("portfolio_value") or 0)
                if equity <= 0:
                    return s
                cfg = get_settings()
                max_exp_pct = float(getattr(cfg, "risk_max_exposure_pct", 0.6))
                positions = _load_positions()
                if not positions:
                    return s
                existing_exposure = _existing_exposure(positions)
                # Resolve price same way risk does
                price = _resolve_price(symbol, strat, s)
                if price is None or price <= 0:
                    return s
                new_notional = abs(qty * price)
                # Would it breach?
                gross = abs(existing_exposure) + abs(new_notional)
                ratio = gross / equity if equity else 999
                if ratio <= max_exp_pct:
                    return s  # fits, no need to reallocate
                # Find weakest holding: lowest unrealized_pl, but ensure it frees enough to fit new trade if possible
                # Sort by unrealized_pl ascending (most negative first), then check exposure fit
                candidates = []
                for p in positions:
                    p_sym = str(p.get("symbol", "")).upper()
                    if not p_sym or p_sym == symbol:
                        continue
                    try:
                        pl = float(p.get("unrealized_pl") or 0)
                    except Exception:
                        pl = 0
                    try:
                        mv = abs(float(p.get("market_value") or 0))
                    except Exception:
                        mv = 0
                    candidates.append((pl, mv, p))
                if not candidates:
                    return s
                candidates.sort(key=lambda x: x[0])  # weakest first
                # Pick first candidate that frees enough (post_close + new <= cap), else fallback to weakest
                weakest = None
                weakest_pl = None
                freed_needed = (existing_exposure + new_notional) - (equity * max_exp_pct)
                # freed_needed >0 is amount we must free at minimum
                for pl, mv, p in candidates:
                    if mv >= freed_needed - 1e-6:
                        weakest = p
                        weakest_pl = pl
                        break
                if weakest is None:
                    # No single position frees enough — pick weakest anyway (close will still execute, open may be rejected but reposition still happens)
                    weakest, weakest_pl = candidates[0][2], candidates[0][0]
                weak_sym = str(weakest.get("symbol", "")).strip().upper()
                weak_qty = weakest.get("qty") or weakest.get("quantity")
                try:
                    weak_qty_f = abs(float(weak_qty))
                    if weak_qty_f <= 0:
                        return s
                except Exception:
                    return s
                # Build paired_trade proposal preserving open leg's stop/target/rationale
                open_leg: Dict[str, Any] = {
                    "action": "buy",
                    "symbol": symbol,
                    "qty": qty,
                }
                # Preserve notional if strategy used it instead of qty
                if strat.get("notional") is not None and strat.get("qty") is None:
                    open_leg = {"action": "buy", "symbol": symbol, "notional": strat.get("notional")}
                for k in ("stop_price", "target_price", "limit_price", "price", "notional"):
                    if strat.get(k) is not None:
                        open_leg[k] = strat.get(k)
                close_leg = {"action": "sell", "symbol": weak_sym, "qty": weak_qty_f}
                # Rationale: explain why rewrite happened
                orig_rationale = str(strat.get("rationale") or strat.get("output") or "")[:180]
                new_rationale = (
                    f"Reallocated: weakest holding {weak_sym} (uPL {weakest_pl:.2f}) → {symbol} "
                    f"(existing {existing_exposure:.0f}+new {new_notional:.0f}/{equity:.0f}={ratio:.2%} > cap {max_exp_pct:.0%}); "
                    f"orig: {orig_rationale}"
                )
                paired = {
                    "paired_trade": True,
                    "close_leg": close_leg,
                    "open_leg": open_leg,
                    "rationale": new_rationale,
                }
                s["strategy"] = {**strat, **paired, "reallocated_from_single": True, "reallocate_reason": f"exposure {ratio:.3f} > {max_exp_pct:.3f}"}
                # Keep messages in sync so downstream nodes see the rewrite
                try:
                    import json as _json

                    s["messages"] = s.get("messages", []) + [{"role": "user", "content": f"[reallocate shim] Converted lone buy {symbol} qty {qty} to paired_trade close {weak_sym} qty {weak_qty_f} -> open {symbol} qty {qty} due to exposure breach"}]
                except Exception:
                    pass
                log_event(
                    "strategy_rewritten_paired",
                    level="info",
                    symbol=symbol,
                    qty=qty,
                    price=price,
                    new_notional=new_notional,
                    existing_exposure=existing_exposure,
                    equity=equity,
                    ratio=round(ratio, 4),
                    cap=max_exp_pct,
                    weakest=weak_sym,
                    weakest_pl=weakest_pl,
                    close_qty=weak_qty_f,
                )
            except Exception as e:
                log_event("reallocate_shim_error", level="warning", error=str(e)[:200])
            return s

        def risk_node(state: Dict[str, Any]) -> Dict[str, Any]:
            # Deterministic — not LLM, stays as plain function node
            return risk_agent(state)

        def execution_node(state: Dict[str, Any]) -> Dict[str, Any]:
            return execution_agent(state)

        def risk_router(state: Dict[str, Any]) -> str:
            """
            Conditional per PHASES.md Phase 5: approved → execution, approved_scaled → execution, rejected → end.
            Paired-trade-aware: risk.get("paired_trade") => branch on close_leg_decision/open_leg_decision.
            Retry is handled via strategy re-invoke if retry_count < max (reserved for v2).
            """
            risk = state.get("risk", {}) or {}
            # Paired-trade shape: {paired_trade:true, close_leg_decision:{decision}, open_leg_decision:{decision}} — handle before flat decision
            if risk.get("paired_trade"):
                close = (risk.get("close_leg_decision") or {}).get("decision", "")
                open_d = (risk.get("open_leg_decision") or {}).get("decision", "")
                close_ok = str(close).lower() in ("approved", "approved_scaled", "approve", "resize")
                open_ok = str(open_d).lower() in ("approved", "approved_scaled", "resize")
                # Per execution spec: close_then_open — if close approved, must go to execution even if open rejected (close is valid risk-reduction)
                if close_ok or open_ok:
                    log_event("graph_route_paired_trade", close_decision=str(close), open_decision=str(open_d), route="execution")
                    return "execution"
                log_event("graph_route_paired_trade", close_decision=str(close), open_decision=str(open_d), route="end")
                return "end"
            decision = str(risk.get("decision", "no_trade")).lower()
            if decision in ("approved", "approved_scaled"):
                return "execution"
            # rejected, no_trade, or unknown → stop (or retry in future if retry_count < 1)
            return "end"

        graph = StateGraph(GraphState)
        graph.add_node("research", research_node)
        graph.add_node("strategy", strategy_node)
        graph.add_node("reallocate", reallocate_node)
        graph.add_node("risk", risk_node)
        graph.add_node("execution", execution_node)
        # Reporting is on-demand via CLI /report (not per-cycle per DOC.md), but register for completeness
        # graph.add_node("reporting", reporting_node)  # reserved for Phase 8

        graph.add_edge(START, "research")
        graph.add_edge("research", "strategy")
        graph.add_edge("strategy", "reallocate")
        graph.add_edge("reallocate", "risk")
        graph.add_conditional_edges("risk", risk_router, {"execution": "execution", "end": END})
        graph.add_edge("execution", END)

        compiled = graph.compile(checkpointer=checkpointer)
        log_event("graph_build_ok", model_research=_model_id("research"), model_strategy=_model_id("strategy"), checkpointer="InMemorySaver")
        return compiled

    except Exception as e:
        log_event("graph_build_fallback", level="warning", error=str(e)[:400])
        return _build_stub_graph()


def _build_stub_graph():
    """Deterministic stub chain (no LLM) — always importable per VULN 5. Mirrors StateGraph logic."""
    from backend.agents.execution import execution_agent
    from backend.agents.research import research_agent
    from backend.agents.risk import risk_agent
    from backend.agents.strategy import strategy_agent

    def _stub_graph(state: dict | None = None, *args, **kwargs) -> dict:
        # Accept *args/**kwargs for compat with compiled graph .invoke(state, config={...})
        _ = args, kwargs
        s: dict = dict(state or {})
        s = research_agent(s)
        s = strategy_agent(s)
        # Deterministic shim for stub as well (mirrors reallocate_node with enough-free logic)
        try:
            strat = s.get("strategy") or {}
            if not strat.get("paired_trade") and not strat.get("arb") and str(strat.get("action", "")).lower() == "buy" and strat.get("symbol") and strat.get("qty"):
                from backend.agents.risk import _existing_exposure, _load_account_state, _load_positions, _resolve_price
                from backend.core.config import get_settings as _get_settings

                account_state = _load_account_state(s)
                equity = float(account_state.get("equity") or account_state.get("portfolio_value") or 0)
                if equity > 0:
                    cfg = _get_settings()
                    max_exp_pct = float(getattr(cfg, "risk_max_exposure_pct", 0.6))
                    positions = _load_positions()
                    if positions:
                        existing_exposure = _existing_exposure(positions)
                        symbol = str(strat.get("symbol")).strip().upper()
                        qty = float(strat.get("qty"))
                        price = _resolve_price(symbol, strat, s)
                        if price and price > 0:
                            new_notional = abs(qty * price)
                            gross = abs(existing_exposure) + abs(new_notional)
                            ratio = gross / equity if equity else 999
                            if ratio > max_exp_pct:
                                candidates = []
                                for p in positions:
                                    p_sym = str(p.get("symbol", "")).upper()
                                    if not p_sym or p_sym == symbol:
                                        continue
                                    try:
                                        pl = float(p.get("unrealized_pl") or 0)
                                        mv = abs(float(p.get("market_value") or 0))
                                    except Exception:
                                        pl, mv = 0, 0
                                    candidates.append((pl, mv, p))
                                if candidates:
                                    candidates.sort(key=lambda x: x[0])
                                    freed_needed = (existing_exposure + new_notional) - (equity * max_exp_pct)
                                    weakest = None
                                    for pl, mv, p in candidates:
                                        if mv >= freed_needed - 1e-6:
                                            weakest = p
                                            break
                                    if weakest is None:
                                        weakest = candidates[0][2]
                                    weak_sym = str(weakest.get("symbol", "")).strip().upper()
                                    weak_qty = abs(float(weakest.get("qty") or 0))
                                    if weak_qty > 0:
                                        open_leg = {"action": "buy", "symbol": symbol, "qty": qty}
                                        for k in ("stop_price", "target_price", "limit_price", "price"):
                                            if strat.get(k) is not None:
                                                open_leg[k] = strat.get(k)
                                        close_leg = {"action": "sell", "symbol": weak_sym, "qty": weak_qty}
                                        s["strategy"] = {**strat, "paired_trade": True, "close_leg": close_leg, "open_leg": open_leg, "reallocated_from_single": True}
                        # no log in stub to keep offline quiet
        except Exception:
            pass
        s = risk_agent(s)
        risk = s.get("risk", {}) or {}
        should_exec = False
        if risk.get("paired_trade"):
            close = (risk.get("close_leg_decision") or {}).get("decision", "")
            open_d = (risk.get("open_leg_decision") or {}).get("decision", "")
            should_exec = str(close).lower() in ("approved", "approved_scaled", "approve", "resize") or str(open_d).lower() in ("approved", "approved_scaled", "resize")
        else:
            should_exec = str(risk.get("decision", "")).lower() in ("approved", "approved_scaled")
        if should_exec:
            s = execution_agent(s)
        return s

    _stub_graph.invoke = _stub_graph  # type: ignore
    return _stub_graph
