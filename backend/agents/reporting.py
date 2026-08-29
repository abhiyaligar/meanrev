"""
Reporting agent — stub for import stability (VULN 5 fix).

Full implementation is Phase 8 (read logs/broker.jsonl, generate summary).
Expose `reporting_agent` for `from backend.agents.reporting import reporting_agent`.
Model selector-driven via `LLM_MODEL_REPORTING` — no hardcoded model here.
"""


def reporting_agent(state: dict | None = None) -> dict:
    """
    Stub — returns placeholder report structure.
    Real implementation will read structured JSON-line logs and produce
    catalyst → technicals → risk → execution → P&L narrative for /report.
    """
    return {
        "report": "stub — Phase 8 not yet implemented",
        "positions": [],
        "trades": [],
        "pnl": None,
        "stub": True,
    }
