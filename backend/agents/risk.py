"""
Risk agent — stub for import stability (VULN 5 fix).

Full implementation is Phase 6 (deterministic rules: position limits,
exposure caps, drawdown breaker, SPXW handling).
Expose `risk_agent` for `from backend.agents.risk import risk_agent`.
"""


def risk_agent(state: dict) -> dict:
    """
    Stub — approves with no scaling and stub verdict.
    Real implementation will enforce per-position limits, exposure caps,
    daily drawdown circuit breaker (> -3% → auto-pause), and close-before-expiry
    for SPXW/XSP.
    """
    state.setdefault("risk", {})
    # If strategy proposed a trade, stub approves; else no-op
    strategy = state.get("strategy", {})
    state["risk"] = {
        "decision": "approved" if strategy.get("action") in ("buy", "sell") else "no_trade",
        "rule": "stub — Phase 6 not yet implemented",
        "stub": True,
    }
    return state
