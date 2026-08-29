"""
Strategy agent — built-in LangChain create_agent per docs.

DOC.md §3: GPT-4o via LLM_PROVIDER, model ID from LLM_MODEL_STRATEGY selector.
Uses create_agent (LangChain 1.0) with ToolNode + middleware, not custom dict logic.

Docs: https://docs.langchain.com/oss/python/releases/langchain-v1#create_agent
- Built on basic loop: model → tool choice → execute via ToolNode → finish when no tool calls
- Middleware handles tool limits and error handling (replaces custom loops)
"""

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ToolCallLimitMiddleware

from backend.core.system_prompt import STRATEGY_SYSTEM_PROMPT
from backend.core.utils import get_model_id, handle_tool_errors
from backend.tools.broker_tools import get_account, get_clock, get_orders, get_positions, submit_order
from backend.tools.market_tools import align_timeframes_tool, get_market_snapshot, get_ohlcv, get_option_chain


# HITL per docs: interrupt on sensitive write tool submit_order (requires approve/edit/reject), reads auto-approved
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
    },
    description_prefix="Order submission pending human approval",
)

_MIDDLEWARE = [ToolCallLimitMiddleware(thread_limit=30, run_limit=15), handle_tool_errors, _HITL_MIDDLEWARE]


def _model_id() -> str:
    return get_model_id("strategy")


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
            tools=[get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, get_account, get_positions, get_orders, get_clock, submit_order],
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
                    tools=[get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, get_account, get_positions, get_orders, get_clock, submit_order],
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
    """
    try:
        from backend.core.config import get_settings

        if get_settings().is_llm_configured():
            agent = get_strategy_agent()
            research_out = state.get("research", {})
            prompt = f"Research: {research_out}. Now synthesize with market data (use get_ohlcv for AAPL/SPY) and options (get_option_chain) to propose a trade. Every strategy must consider options."
            msgs = state.get("messages", []) + [{"role": "user", "content": prompt}]
            result = agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            state["strategy"] = {"output": str(content), "agent": "strategy", "model": _model_id(), "built_in": True}
            return state
    except Exception:
        pass

    state.setdefault("strategy", {})
    state["strategy"] = {
        "action": "hold",
        "symbol": None,
        "qty": 0,
        "rationale": "stub — LLM not configured or strategy agent unavailable",
        "stub": True,
        "model": _model_id(),
    }
    return state
