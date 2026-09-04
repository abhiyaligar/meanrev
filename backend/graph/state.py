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
        description="Risk verdict (RiskVerdict): single {decision: approved|approved_scaled|rejected|no_trade, rule, adjusted_qty} or paired_trade {paired_trade:true, close_leg_decision:{decision}, open_leg_decision:{decision,adjusted}, sequencing:close_then_open}",
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

    # --- Dict compatibility for CLI/graph nodes that use state.get(...) / state["key"] ---
    # LangGraph returns a GraphState object, but many nodes were written for dict. Add get/__getitem__/__contains__ so both work.

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        if self.__pydantic_extra__ and key in self.__pydantic_extra__:
            return self.__pydantic_extra__[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            if self.__pydantic_extra__ is None:
                self.__pydantic_extra__ = {}
            self.__pydantic_extra__[key] = value
            # Also set as attribute for direct access
            object.__setattr__(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            if hasattr(self, key):
                val = getattr(self, key)
                # For BaseModel fields, getattr returns the value even if None; treat as present
                return val if val is not None or key in self.model_fields else default
            if self.__pydantic_extra__ and key in self.__pydantic_extra__:
                return self.__pydantic_extra__[key]
            return default
        except Exception:
            return default

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key) or (self.__pydantic_extra__ is not None and key in self.__pydantic_extra__)

    def setdefault(self, key: str, default: Any = None) -> Any:
        if key in self:
            val = self.get(key)
            if val is not None:
                return val
            # For explicit None, treat as missing for setdefault semantics
            # Check if key is a field with None vs missing extra
            if key in self.model_fields and getattr(self, key) is None and default is not None:
                self[key] = default
                return default
            return val
        self[key] = default
        return default

    def update(self, other: Any = None, **kwargs: Any) -> None:
        if other is not None:
            if isinstance(other, dict):
                for k, v in other.items():
                    self[k] = v
            elif hasattr(other, "items"):
                for k, v in other.items():  # type: ignore
                    self[k] = v
            elif hasattr(other, "model_dump"):
                for k, v in other.model_dump().items():
                    if v is not None:
                        self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def keys(self):
        # Merge fields and extra
        base = list(self.model_fields.keys())
        if self.__pydantic_extra__:
            base.extend(k for k in self.__pydantic_extra__.keys() if k not in base)
        # Also include any dynamically set attrs that are not fields
        for k in self.__dict__.keys():
            if k not in base and not k.startswith("_"):
                base.append(k)
        return base

    def items(self):
        return [(k, self.get(k)) for k in self.keys()]

    def values(self):
        return [self.get(k) for k in self.keys()]

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
        # Paired trade: approved if either leg is approved/resize (close_then_open); else flat decision
        # Risk emits "approve" (without d) for close_leg; accept both forms
        if self.risk and self.risk.get("paired_trade"):
            close = str((self.risk.get("close_leg_decision") or {}).get("decision", "")).lower()
            open_d = str((self.risk.get("open_leg_decision") or {}).get("decision", "")).lower()
            return close in ("approved", "approved_scaled", "approve", "resize") or open_d in ("approved", "approved_scaled", "approve", "resize")
        return bool(self.risk and self.risk.get("decision") == "approved")

    def is_risk_scaled(self) -> bool:
        if self.risk and self.risk.get("paired_trade"):
            open_d = str((self.risk.get("open_leg_decision") or {}).get("decision", "")).lower()
            return open_d in ("resize", "approved_scaled")
        return bool(self.risk and self.risk.get("decision") == "approved_scaled")

    def is_risk_rejected(self) -> bool:
        if self.risk and self.risk.get("paired_trade"):
            close = str((self.risk.get("close_leg_decision") or {}).get("decision", "")).lower()
            open_d = str((self.risk.get("open_leg_decision") or {}).get("decision", "")).lower()
            # Both legs rejected/no_trade => rejected
            return close in ("rejected", "no_trade") and open_d in ("rejected", "no_trade", "")
        return bool(self.risk and self.risk.get("decision") in ("rejected", "no_trade"))

    def is_paired_trade(self) -> bool:
        return bool(self.risk and self.risk.get("paired_trade") is True)
