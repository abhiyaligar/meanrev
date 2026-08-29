"""
Graph shared state — LangGraph schema per Phase 5.

Holds all agent fields for the pipeline: market → research → strategy → risk → execution → reporting.
Used as StateGraph(GraphState) state_schema; supports both TypedDict and Pydantic usage.

Design per DOC.md §3, §5, and PHASES.md Phase 5:
- messages: Annotated with add_messages for LLM chat history (required for create_agent)
- market_snapshot: timeframe -> bar records (OHLCV+VWAP+indicators) via backend/data/market
- research: sentiment, regime, catalyst_summary (from research agent)
- strategy: TradeDecision shape (action, symbol, qty/notional, stop/target, rationale)
- risk: RiskVerdict shape (decision approved|approved_scaled|rejected, rule, adjusted_qty)
- execution: order result (order_id, status, filled_qty, latency, error)
- reporting_context: accumulates events for reporting agent
- account_state: cash, equity, buying_power, drawdown, margin usage
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

try:
    from langchain.messages import BaseMessage  # type: ignore
except ImportError:
    from langchain_core.messages import BaseMessage  # type: ignore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphState(BaseModel):
    """
    Shared LangGraph state — single schema that travels research → strategy → risk → execution.
    All fields optional to allow incremental building; at least messages is seeded per invoke.
    Extra fields allowed for forward compat (e.g., thread_id, CLI instruction).
    """

    # --- LLM chat history — required for create_agent invoke ---
    # Allow both BaseMessage objects and dicts {role, content} for flexibility (stub and real)
    messages: Annotated[List[Any], add_messages] = Field(default_factory=list, description="Chat history for built-in agents")

    # --- Market + account snapshot (from data/broker) ---
    market_snapshot: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Timeframe -> bar records dict; e.g., {'1Day': [{open,high,low,close,volume,vwap,rsi,ema_20,...}], '1Hour': [...]}",
    )
    account_state: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Account snapshot: {cash, portfolio_value, buying_power, equity, drawdown, margin_usage, unrealized_pl, realized_pl}",
    )

    # --- Agent outputs ---
    research: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Research output: {sentiment: bullish|bearish|neutral, regime: risk_on|risk_off|neutral, catalyst_summary: str, model: str, built_in: bool, stub: bool}",
    )
    strategy: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Strategy output (TradeDecision): {action: buy|sell|hold, symbol, qty, notional, stop_price, target_price, rationale, model, built_in}",
    )
    risk: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Risk verdict (RiskVerdict): {decision: approved|approved_scaled|rejected|no_trade, rule: str, adjusted_qty: float, original_qty: float, stub: bool}",
    )
    execution: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Execution result: {status: filled|rejected|skipped|partial, order_id, filled_qty, filled_price, latency_ms, error, stub: bool}",
    )
    reporting_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Reporting context: {events: list, positions, trades, pnl, reasoning_trail}",
    )

    # --- Control ---
    thread_id: Optional[str] = Field(default=None, description="LangGraph thread_id for checkpointer persistence")
    run_id: Optional[str] = Field(default=None, description="Unique run identifier for logging")
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)

    model_config = {"extra": "allow", "arbitrary_types_allowed": True}

    # --- Helpers ---

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphState":
        return cls(**data)

    def update_timestamp(self) -> None:
        self.updated_at = _utc_now_iso()

    def market_has_data(self) -> bool:
        return bool(self.market_snapshot and any(v for v in self.market_snapshot.values()))

    def is_risk_approved(self) -> bool:
        return bool(self.risk and self.risk.get("decision") == "approved")

    def is_risk_scaled(self) -> bool:
        return bool(self.risk and self.risk.get("decision") == "approved_scaled")

    def is_risk_rejected(self) -> bool:
        return bool(self.risk and self.risk.get("decision") in ("rejected", "no_trade"))
