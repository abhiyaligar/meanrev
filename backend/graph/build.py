"""
Graph build — stub for import stability (VULN 5 fix).

Full implementation is Phase 5 (wire 5 agents into LangGraph with
conditional risk branch: approved → execution, rejected/scaled → stop).
Expose `build_graph` so `from backend.graph.build import build_graph` succeeds.
"""


def build_graph():
    """
    Stub — returns a minimal callable that runs research → strategy → risk → execution
    via the stub agents, then reporting. Real implementation will use LangGraph
    StateGraph with GraphState and proper conditional edges.
    """
    from backend.agents.research import research_agent
    from backend.agents.strategy import strategy_agent
    from backend.agents.risk import risk_agent
    from backend.agents.execution import execution_agent

    def _stub_graph(state: dict | None = None) -> dict:
        s: dict = dict(state or {})
        s = research_agent(s)
        s = strategy_agent(s)
        s = risk_agent(s)
        # Conditional stub: only call execution if risk approved
        if s.get("risk", {}).get("decision") == "approved":
            s = execution_agent(s)
        return s

    # Mimic LangGraph's compiled graph interface (.invoke)
    _stub_graph.invoke = _stub_graph  # type: ignore
    return _stub_graph
