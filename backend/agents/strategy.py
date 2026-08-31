"""
Strategy agent — built-in LangChain create_agent per docs + Phase 9 enhancements.

DOC.md §3: GPT-4o via LLM_PROVIDER, model ID from LLM_MODEL_STRATEGY selector (compulsory from .env).
Uses create_agent (LangChain 1.0) with ToolNode + HumanInTheLoopMiddleware + ToolCallLimitMiddleware.
Enhancements per Phase 9:
- 9.1: Token counter <1000 via tiktoken (already in requirements)
- 9.2: Options guarantee — every decision includes option leg via get_option_chain
- 9.3: ATR-based sizing (qty, stop/target tied to ATR via data/market)
- 9.4: Natural-language instruction hook (conservative/aggressive)
"""

from typing import Any, Dict, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ToolCallLimitMiddleware

from backend.core.logging import log_event
from backend.core.system_prompt import GRAPH_STRATEGY_PROMPT_TEMPLATE, STRATEGY_SYSTEM_PROMPT
from backend.core.utils import count_tokens, enforce_token_limit, get_model_id, handle_tool_errors
from backend.tools.broker_tools import get_account, get_clock, get_orders, get_positions, submit_order
from backend.tools.market_tools import align_timeframes_tool, detect_arbitrage, get_market_snapshot, get_ohlcv, get_option_chain

# HITL per docs: interrupt on sensitive write tool submit_order, reads auto-approved; detect_arbitrage is read-only
_HITL_MIDDLEWARE = HumanInTheLoopMiddleware(
    interrupt_on={
        "submit_order": {"allowed_decisions": ["approve", "edit", "reject"]},
        "get_account": False,
        "get_positions": False,
        "get_orders": False,
        "get_clock": False,
        "get_ohlcv": False,
        "get_market_snapshot": False,
        "get_option_chain": False,
        "align_timeframes_tool": False,
        "detect_arbitrage": False,
    },
    description_prefix="Order submission pending human approval",
)

_MIDDLEWARE = [ToolCallLimitMiddleware(thread_limit=30, run_limit=15), handle_tool_errors, _HITL_MIDDLEWARE]


def _model_id() -> str:
    return get_model_id("strategy")

def apply_instruction(state: Dict[str, Any], instruction: Optional[str] = None) -> Dict[str, Any]:
    """
    Parse natural language instruction like 'be more conservative' / 'more aggressive' / 'explain last trade'.
    Sets state['strategy_conservatism'] multiplier for sizing in 9.3.
    Returns updated state.
    """
    if not instruction:
        # Check messages for instruction-like content
        msgs = state.get("messages", [])
        if msgs:
            last = msgs[-1]
            content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
            instruction = str(content) if content else ""
    if not instruction:
        return state

    instr_lower = str(instruction).lower()
    current = float(state.get("strategy_conservatism", 1.0))

    if any(phrase in instr_lower for phrase in ("more conservative", "be conservative", "conservative", "reduce risk", "risk off")):
        state["strategy_conservatism"] = 0.5
        log_event("strategy_instruction_conservative", instruction=instruction[:100])
    elif any(phrase in instr_lower for phrase in ("more aggressive", "be aggressive", "aggressive", "increase risk", "risk on")):
        state["strategy_conservatism"] = 1.5
        log_event("strategy_instruction_aggressive", instruction=instruction[:100])
    elif "explain" in instr_lower and "trade" in instr_lower:
        # Short-circuit: return last rationale without new trade
        last_strat = state.get("strategy", {})
        state["strategy_explain"] = last_strat.get("rationale") or last_strat.get("output") or "No prior trade to explain."
        log_event("strategy_instruction_explain", instruction=instruction[:100])
    elif instr_lower.strip() in ("conservative", "aggressive"):
        # Already handled
        pass

    # Store raw instruction for audit
    state["last_instruction"] = str(instruction)[:500]
    return state


# --- 9.2: Options guarantee ---

def ensure_options_in_decision(decision: Dict[str, Any], underlying: Optional[str] = None) -> Dict[str, Any]:
    """
    Ensure every TradeDecision includes an option leg (hackathon requirement).
    If decision has no option symbols (no 'option', 'call', 'put', 'strike', 'expiration'), auto-inject via get_option_chain.
    """
    if not decision:
        decision = {}
    text = str(decision).lower()
    has_option = any(k in text for k in ("option", "call", "put", "strike", "expiration", "delta", "gamma")) or "option_leg" in decision or "option_symbol" in decision
    if has_option:
        return decision

    # Need to inject
    sym = underlying or decision.get("symbol") or "AAPL"
    # Clean underlying: if it's an option symbol already, extract underlying
    try:
        sym = str(sym).strip().upper().split()[0][:6]  # crude underlying extraction
        if sym.startswith(("SPXW", "XSP")):
            sym = sym[:4]
        else:
            # If symbol is like AAPL, keep
            sym = sym[:5].strip()
            if not sym:
                sym = "AAPL"
    except Exception:
        sym = "AAPL"

    # If underlying looks like option (long), try to get spot underlying
    if len(sym) > 6 and any(c.isdigit() for c in sym):
        sym = "AAPL"

    try:
        # Call get_option_chain tool directly (bypasses LLM tool call, deterministic)
        chain_str = get_option_chain.invoke({"underlying": sym, "limit": 2})
        import json

        chain_data = json.loads(chain_str) if isinstance(chain_str, str) else {}
        chain = chain_data.get("chain", []) if isinstance(chain_data, dict) else []
        if chain:
            # Pick first call as leg
            leg = chain[0]
            decision["option_leg"] = leg
            decision["option_symbol"] = leg.get("symbol")
            decision["option_greeks"] = leg.get("greeks")
            log_event("strategy_options_injected", underlying=sym, leg=leg.get("symbol"))
        else:
            decision["option_leg"] = {"error": "No data available for this underlying/expiration (OPRA subscription required, free-tier has no options chain)"}
            log_event("strategy_options_no_data", underlying=sym, reason="No data available for this")
    except Exception as e:
        log_event("strategy_options_inject_failed", level="warning", error=str(e)[:200])
        decision["option_leg"] = {"error": str(e)[:100]}

    return decision


# --- 9.3: ATR-based sizing ---

def compute_sizing(
    atr: Optional[float],
    equity: float,
    price: float,
    conservatism: float = 1.0,
    risk_per_trade_pct: float = 0.01,
) -> Dict[str, Optional[float]]:
    """
    Compute qty, stop, target tied to ATR.
    qty = min(equity * risk_per_trade_pct / ATR, equity * 0.15 / price) * conservatism
    stop = close - 1.5*ATR, target = close + 2.5*ATR (long) or inverse for short
    Returns {qty, stop_price, target_price, atr}
    """
    try:
        atr_val = float(atr) if atr and float(atr) > 0 else 1.0
    except (TypeError, ValueError):
        atr_val = 1.0
    try:
        eq = float(equity) if equity > 0 else 100000.0
    except (TypeError, ValueError):
        eq = 100000.0
    try:
        p = float(price) if price and float(price) > 0 else 100.0
    except (TypeError, ValueError):
        p = 100.0

    # Position limit 15% of equity
    max_position_notional = eq * 0.15
    max_qty_by_position = max_position_notional / p if p > 0 else 0
    # Risk-based qty
    risk_qty = (eq * risk_per_trade_pct / atr_val) if atr_val > 0 else max_qty_by_position
    # Conservatism multiplier: 0.5 conservative, 1.5 aggressive
    try:
        cons = float(conservatism)
    except (TypeError, ValueError):
        cons = 1.0
    cons = max(0.1, min(3.0, cons))
    qty = min(risk_qty, max_qty_by_position) * cons
    qty = max(1.0, round(qty, 2))  # at least 1 share/contract

    # Stop/target: 1.5 ATR stop, 2.5 ATR target
    stop_price = p - 1.5 * atr_val
    target_price = p + 2.5 * atr_val
    # For short, invert (handled by caller sign)

    log_event("strategy_sizing", atr=atr_val, equity=eq, price=p, qty=qty, stop=stop_price, target=target_price, conservatism=cons)
    return {"qty": qty, "stop_price": round(stop_price, 2), "target_price": round(target_price, 2), "atr": atr_val}


def get_strategy_agent(checkpointer=None):
    """
    Factory — returns built-in LangChain agent per docs with HITL for submit_order.
    Uses InMemorySaver if no checkpointer provided (required for HITL to persist across interrupts).
    """
    try:
        if checkpointer is None:
            try:
                from langgraph.checkpoint.memory import InMemorySaver

                checkpointer = InMemorySaver()
            except Exception:
                checkpointer = None

        return create_agent(
            model=_model_id(),
            tools=[get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, detect_arbitrage, get_account, get_positions, get_orders, get_clock, submit_order],
            system_prompt=STRATEGY_SYSTEM_PROMPT,
            middleware=_MIDDLEWARE,
            checkpointer=checkpointer,
        )
    except Exception as e:
        if "langchain-openrouter" in str(e) or "langchain-groq" in str(e).lower():
            try:
                from langchain.chat_models import init_chat_model

                from backend.core.config import get_settings

                s = get_settings()
                cfg = s.llm_provider_config()
                model_name = s.get_model("strategy")
                fallback = init_chat_model(
                    model_name,
                    model_provider="openai",
                    api_key=cfg.get("api_key"),
                    base_url=cfg.get("base_url"),
                    temperature=0.5,
                )
                if checkpointer is None:
                    try:
                        from langgraph.checkpoint.memory import InMemorySaver

                        checkpointer = InMemorySaver()
                    except Exception:
                        checkpointer = None
                return create_agent(
                    model=fallback,
                    tools=[get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, detect_arbitrage, get_account, get_positions, get_orders, get_clock, submit_order],
                    system_prompt=STRATEGY_SYSTEM_PROMPT,
                    middleware=_MIDDLEWARE,
                    checkpointer=checkpointer,
                )
            except Exception as e2:
                from backend.core.logging import log_event

                log_event("strategy_agent_fallback_failed", level="warning", error=str(e2)[:200])
                return None
        from backend.core.logging import log_event

        log_event("strategy_agent_create_failed", level="warning", error=str(e)[:200])
        return None


def strategy_agent(state: dict) -> dict:
    """
    Adapter for StateGraph nodes expecting strategy_agent(state) -> dict.
    Invokes built-in agent when LLM configured, else stub so graph never fails.
    Integrates 9.1-9.4 enhancements: instruction hook, token limit, ATR sizing, options guarantee.
    """
    # 9.4: Apply instruction hook first (conservative/aggressive)
    try:
        # Check for explicit instruction in state
        instr = state.get("instruction") or state.get("last_instruction")
        if not instr:
            # Check last message for instruction-like content
            msgs = state.get("messages", [])
            if msgs:
                last = msgs[-1]
                content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
                if any(kw in str(content).lower() for kw in ("conservative", "aggressive", "explain")):
                    instr = str(content)
        if instr:
            state = apply_instruction(state, instr)
    except Exception:
        pass

    # Try built-in LLM path
    try:
        from backend.core.config import get_settings

        if get_settings().is_llm_configured():
            agent = get_strategy_agent()
            if agent is None:
                raise RuntimeError("strategy agent not available")

            research_out = state.get("research", {})
            # Build prompt and enforce token limit (9.1) — central template from system_prompt.py, no hardcoded string
            base_prompt = GRAPH_STRATEGY_PROMPT_TEMPLATE.format(research=research_out)
            # Count tokens and truncate research if needed
            full_prompt = f"{STRATEGY_SYSTEM_PROMPT}\n\n{base_prompt}"
            model_id = _model_id()
            if count_tokens(full_prompt, model_id) > 1000:
                # Truncate research catalyst
                if isinstance(research_out, dict) and "catalyst_summary" in research_out:
                    research_out = dict(research_out)
                    research_out["catalyst_summary"] = str(research_out["catalyst_summary"])[:500] + "...[truncated]"
                    base_prompt = GRAPH_STRATEGY_PROMPT_TEMPLATE.format(research=research_out)
                    base_prompt = enforce_token_limit(base_prompt, 1000, model_id)

            msgs = state.get("messages", []) + [{"role": "user", "content": base_prompt}]
            result = agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            content_str = str(content)

            # Parse LLM output to dict for post-processing
            decision: Dict[str, Any] = {"output": content_str, "agent": "strategy", "model": _model_id(), "built_in": True}
            # Try to extract JSON from content
            try:
                import json, re

                m = re.search(r"\{[^}]+\}", content_str)
                if m:
                    parsed = json.loads(m.group(0))
                    decision.update(parsed)
            except Exception:
                pass

            # 9.2: Ensure options
            symbol_for_options = decision.get("symbol") or "AAPL"
            decision = ensure_options_in_decision(decision, underlying=symbol_for_options)

            # 9.3: ATR-based sizing override
            try:
                from backend.data.market import fetch_ohlcv

                df = fetch_ohlcv(decision.get("symbol") or "AAPL", timeframe="1Day", limit=20)
                atr = None
                price = None
                if not df.empty and "atr" in df.columns and not df["atr"].isna().all():
                    atr = float(df["atr"].dropna().iloc[-1])
                if not df.empty and "close" in df.columns:
                    price = float(df["close"].dropna().iloc[-1]) if not df["close"].isna().all() else None
                if atr is not None and price is not None:
                    # Get equity for sizing
                    equity = 100000.0
                    try:
                        from backend.broker.client import get_account

                        acct = get_account()
                        equity = float(acct.get("portfolio_value") or acct.get("equity") or 100000)
                    except Exception:
                        pass
                    conservatism = float(state.get("strategy_conservatism", 1.0))
                    sizing = compute_sizing(atr, equity, price, conservatism=conservatism)
                    # Override qty/stop/target with ATR-based values, keep side
                    decision["qty"] = sizing["qty"]
                    decision["stop_price"] = sizing["stop_price"]
                    decision["target_price"] = sizing["target_price"]
                    decision["atr"] = sizing["atr"]
                    decision["sizing_conservatism"] = conservatism
            except Exception as e:
                log_event("strategy_sizing_failed", level="warning", error=str(e)[:200])

            state["strategy"] = decision
            # Also update messages with LLM history
            state["messages"] = result.get("messages", msgs)
            return state
    except Exception as e:
        log_event("strategy_agent_invoke_failed", level="warning", error=str(e)[:300])
        # Fall through to stub

    # Fallback stub — keeps pipeline runnable offline, but now with 9.2/9.3 enhancements
    state.setdefault("strategy", {})
    # Use ATR sizing even for stub
    try:
        from backend.data.market import fetch_ohlcv

        df = fetch_ohlcv("AAPL", timeframe="1Day", limit=20)
        atr = float(df["atr"].dropna().iloc[-1]) if not df.empty and "atr" in df.columns and not df["atr"].isna().all() else 2.0
        price = float(df["close"].dropna().iloc[-1]) if not df.empty and "close" in df.columns else 150.0
        equity = 100000.0
        try:
            from backend.broker.client import get_account

            acct = get_account()
            equity = float(acct.get("portfolio_value") or 100000)
        except Exception:
            pass
        conservatism = float(state.get("strategy_conservatism", 1.0))
        sizing = compute_sizing(atr, equity, price, conservatism=conservatism)
        qty = sizing["qty"]
        stop_price = sizing["stop_price"]
        target_price = sizing["target_price"]
    except Exception:
        qty, stop_price, target_price, atr = 5, 147.0, 155.0, 2.0

    stub_decision = {
        "action": "hold",
        "symbol": "AAPL",
        "qty": qty,
        "stop_price": stop_price,
        "target_price": target_price,
        "atr": atr,
        "rationale": "stub — LLM not configured or strategy agent unavailable (ATR-sized hold)",
        "stub": True,
        "model": _model_id(),
        "sizing_conservatism": state.get("strategy_conservatism", 1.0),
    }
    # Ensure options
    stub_decision = ensure_options_in_decision(stub_decision, underlying="AAPL")
    state["strategy"] = stub_decision
    return state
