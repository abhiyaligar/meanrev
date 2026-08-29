"""
Graph shared state — stub for import stability (VULN 5 fix).

Full implementation is Phase 5 (LangGraph state schema with all agent fields).
Expose `GraphState` so `from backend.graph.state import GraphState` succeeds.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class GraphState(BaseModel):
    """
    Stub shared state. Real Phase 5 will include:
    - market_snapshot + indicators
    - research output (sentiment, regime, catalyst_summary)
    - strategy decision (TradeDecision)
    - risk verdict (RiskVerdict)
    - execution result
    - reporting context
    """

    market_snapshot: Optional[Dict[str, Any]] = None
    research: Optional[Dict[str, Any]] = None
    strategy: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    execution: Optional[Dict[str, Any]] = None
    reporting: Optional[Dict[str, Any]] = None
    # Allow extra fields for forward compat before full schema lands
    model_config = {"extra": "allow"}

    # Helper: allow dict-like access for stub agents that use plain dicts
    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
