"""
Strategy agent — stub for import stability (VULN 5 fix).

Full implementation is Phase 9 (GPT-4o via LLM_PROVIDER).
Expose `strategy_agent` for `from backend.agents.strategy import strategy_agent`.
Model selector-driven via `LLM_MODEL_STRATEGY` — no hardcoded model here.
"""


def strategy_agent(state: dict) -> dict:
    """
    Stub — returns hold with zero size and stub rationale.
    Real implementation will combine research + technicals + account state,
    enforce <1000 token prompt, and ensure options in every decision.
    """
    state.setdefault("strategy", {})
    state["strategy"] = {
        "action": "hold",
        "symbol": None,
        "qty": 0,
        "rationale": "stub — Phase 9 not yet implemented",
        "stub": True,
    }
    return state
