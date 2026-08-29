"""
Research agent — stub for import stability (VULN 5 fix).

Full implementation is Phase 10 (Claude 3.5 Sonnet via LLM_PROVIDER).
This stub exposes `research_agent` so that `from backend.agents.research import research_agent`
does not raise ModuleNotFoundError before Phase 10 lands.
Model is selector-driven via `LLM_MODEL_MARKET_RESEARCH` in .env.example — no hardcoded model here.
"""


def research_agent(state: dict) -> dict:
    """
    Stub — returns state unchanged with a placeholder research output.
    Real implementation will call LLM via OpenRouter/Groq/Modal using
    `get_settings().get_model("research")` and enforce <1000 tokens.
    """
    # Placeholder output shape matches DOC.md §3 contract
    state.setdefault("research", {})
    state["research"] = {
        "sentiment": "neutral",
        "regime": "unknown",
        "catalyst_summary": "stub — Phase 10 not yet implemented",
        "stub": True,
    }
    return state
