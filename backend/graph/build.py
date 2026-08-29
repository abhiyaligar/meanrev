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
from backend.core.system_prompt import GRAPH_RESEARCH_PROMPT, GRAPH_STRATEGY_PROMPT_TEMPLATE, REPORTING_SYSTEM_PROMPT, RESEARCH_SYSTEM_PROMPT, STRATEGY_SYSTEM_PROMPT


def _model_id(agent: str) -> str:
    try:
        s = get_settings()
        provider = s.llm_provider
        model = s.get_model(agent)
        if ":" in model and model.split(":")[0] in ("openrouter", "groq", "modal", "openai", "anthropic", "google_genai"):
            return model
        return model if provider == "modal" else f"{provider}:{model}"
    except Exception as e:
        return f"missing:{str(e)[:60]}"


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

        def research_node(state: Dict[str, Any]) -> Dict[str, Any]:
            """Built-in research agent node — single invoke, map output to GraphState."""
            msgs = state.get("messages", [{"role": "user", "content": GRAPH_RESEARCH_PROMPT}])
            result = research_agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            # Map built-in agent output directly to state — no duplicate stub call (useless custom logic removed)
            return {
                **state,
                "research": {"output": str(content), "agent": "research", "model": _model_id("research"), "built_in": True},
                "messages": result.get("messages", msgs),
            }

        def strategy_node(state: Dict[str, Any]) -> Dict[str, Any]:
            research_out = state.get("research", {})
            prompt = GRAPH_STRATEGY_PROMPT_TEMPLATE.format(research=research_out)
            msgs = state.get("messages", []) + [{"role": "user", "content": prompt}]
            result = strategy_agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            return {
                **state,
                "strategy": {"output": str(content), "agent": "strategy", "model": _model_id("strategy"), "built_in": True},
                "messages": result.get("messages", msgs),
            }

        def risk_node(state: Dict[str, Any]) -> Dict[str, Any]:
            # Deterministic — not LLM, stays as plain function node
            return risk_agent(state)

        def execution_node(state: Dict[str, Any]) -> Dict[str, Any]:
            return execution_agent(state)

        def risk_router(state: Dict[str, Any]) -> str:
            decision = state.get("risk", {}).get("decision", "no_trade")
            return "execution" if decision == "approved" else "end"

        graph = StateGraph(GraphState)
        graph.add_node("research", research_node)
        graph.add_node("strategy", strategy_node)
        graph.add_node("risk", risk_node)
        graph.add_node("execution", execution_node)

        graph.add_edge(START, "research")
        graph.add_edge("research", "strategy")
        graph.add_edge("strategy", "risk")
        graph.add_conditional_edges("risk", risk_router, {"execution": "execution", "end": END})
        graph.add_edge("execution", END)

        compiled = graph.compile(checkpointer=checkpointer)
        log_event("graph_build_ok", model_research=_model_id("research"), model_strategy=_model_id("strategy"), checkpointer="InMemorySaver")
        return compiled

    except Exception as e:
        log_event("graph_build_fallback", level="warning", error=str(e)[:400])
        return _build_stub_graph()


def _build_stub_graph():
    """Deterministic stub chain (no LLM) — always importable per VULN 5."""
    from backend.agents.research import research_agent
    from backend.agents.strategy import strategy_agent
    from backend.agents.risk import risk_agent
    from backend.agents.execution import execution_agent

    def _stub_graph(state: dict | None = None, *args, **kwargs) -> dict:
        # Accept *args/**kwargs for compat with compiled graph .invoke(state, config={...})
        _ = args, kwargs
        s: dict = dict(state or {})
        s = research_agent(s)
        s = strategy_agent(s)
        s = risk_agent(s)
        if s.get("risk", {}).get("decision") == "approved":
            s = execution_agent(s)
        return s

    _stub_graph.invoke = _stub_graph  # type: ignore
    return _stub_graph
