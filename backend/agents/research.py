"""
Research agent — built-in LangChain create_agent per docs.

DOC.md §3: Claude 3.5 Sonnet via LLM_PROVIDER, model ID from LLM_MODEL_MARKET_RESEARCH selector.
Uses create_agent (LangChain 1.0 built-in, runs on LangGraph) with ToolNode + middleware, not custom dict logic.

Docs: https://docs.langchain.com/oss/python/langchain/overview#create-an-agent
- @tool + type hints + docstring defines schema (backend/tools/news_tools)
- create_agent(model=_model_id(), tools=[fetch_news,...], system_prompt=...)  # _model_id() reads LLM_MODEL_MARKET_RESEARCH from .env — no hardcoded model
"""

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware, wrap_tool_call
from langchain.messages import ToolMessage

from backend.core.config import get_settings
from backend.core.system_prompt import RESEARCH_SYSTEM_PROMPT
from backend.tools.news_tools import extract_keywords, fetch_news, get_macro_calendar


@wrap_tool_call
def _handle_tool_errors(request, handler):
    """Per docs: handle tool execution errors with custom message — do not swallow network failures."""
    try:
        return handler(request)
    except Exception as e:
        return ToolMessage(content=f"Tool error: Please check input and try again. ({str(e)})", tool_call_id=request.tool_call["id"])


# Built-in middleware per docs: limits + error handling
_RESEARCH_MIDDLEWARE = [ToolCallLimitMiddleware(thread_limit=20, run_limit=10), _handle_tool_errors]


def _model_id() -> str:
    try:
        s = get_settings()
        provider = s.llm_provider
        model = s.get_model("research")
        if ":" in model and model.split(":")[0] in ("openrouter", "groq", "modal", "openai", "anthropic", "google_genai"):
            return model
        return model if provider == "modal" else f"{provider}:{model}"
    except Exception as e:
        # Compulsory from .env — return error placeholder for stub mode, never hardcoded model
        return f"missing:{str(e)[:60]}"


def get_research_agent():
    """
    Factory — returns built-in LangChain agent (create_agent) per docs.
    Caller invokes via agent.invoke({"messages": [{"role":"user","content": "..."}]}).
    Handles missing provider package (e.g., langchain-openrouter) by trying
    init_chat_model fallback, else returns None for graph stub fallback.
    """
    try:
        return create_agent(
            model=_model_id(),
            tools=[fetch_news, get_macro_calendar, extract_keywords],
            system_prompt=RESEARCH_SYSTEM_PROMPT,
            middleware=_RESEARCH_MIDDLEWARE,
        )
    except Exception as e:
        # Fallback for missing provider package — try generic init_chat_model with base_url
        if "langchain-openrouter" in str(e) or "langchain-groq" in str(e).lower():
            try:
                from langchain.chat_models import init_chat_model

                from backend.core.config import get_settings

                s = get_settings()
                cfg = s.llm_provider_config()
                # Use openai-compatible base_url for OpenRouter/Groq
                model_name = s.get_model("research")
                fallback = init_chat_model(
                    model_name,
                    model_provider="openai",
                    api_key=cfg.get("api_key"),
                    base_url=cfg.get("base_url"),
                    temperature=0.5,
                )
                return create_agent(
                    model=fallback,
                    tools=[fetch_news, get_macro_calendar, extract_keywords],
                    system_prompt=RESEARCH_SYSTEM_PROMPT,
                    middleware=_RESEARCH_MIDDLEWARE,
                )
            except Exception as e2:
                from backend.core.logging import log_event

                log_event("research_agent_fallback_failed", level="warning", error=str(e2)[:200])
                return None
        from backend.core.logging import log_event

        log_event("research_agent_create_failed", level="warning", error=str(e)[:200])
        return None


# Backward compat for graph stub that imports `research_agent` as callable(state)
# This wrapper preserves old import `from backend.agents.research import research_agent`
# but now delegates to built-in agent when LLM is configured, else falls back to stub dict.
def research_agent(state: dict) -> dict:
    """
    Adapter for StateGraph nodes that expect research_agent(state: dict) -> dict.
    If LLM is configured, invokes built-in agent; else returns stub shape so graph never fails.
    """
    try:
        from backend.core.config import get_settings

        if get_settings().is_llm_configured():
            agent = get_research_agent()
            msgs = state.get("messages", [{"role": "user", "content": "Perform market research: fetch news and macro calendar, output sentiment and regime."}])
            result = agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            state["research"] = {"output": str(content), "agent": "research", "model": _model_id(), "built_in": True}
            return state
    except Exception:
        pass

    # Fallback stub — keeps pipeline runnable offline (no LLM key)
    state.setdefault("research", {})
    state["research"] = {
        "sentiment": "neutral",
        "regime": "unknown",
        "catalyst_summary": "stub — LLM not configured or research agent unavailable",
        "stub": True,
        "model": _model_id(),
    }
    return state
