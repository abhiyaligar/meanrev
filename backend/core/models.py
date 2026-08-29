"""
Shared domain models and response envelopes.
Single contract for graph state, logging, and API — DOC.md §5 core/models.py
No business logic, no secrets.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Generic envelopes ---

class ErrorResponse(BaseModel):
    detail: Any = Field(..., description="Human-readable error or structured {error, type}")
    type: str = Field(default="APIError", description="Exception type name")


class AccountResponse(BaseModel):
    connected: bool = True
    account: Dict[str, Any] = Field(..., description="Raw alpaca-py account dump")
    ts: str = Field(default_factory=utc_now_iso)


class PositionsResponse(BaseModel):
    count: int
    positions: List[Dict[str, Any]]
    symbol_filter: Optional[str] = None
    ts: str = Field(default_factory=utc_now_iso)


class OrdersResponse(BaseModel):
    count: int
    orders: List[Dict[str, Any]]
    status_filter: str = Field(..., description="open|closed|all")
    limit: int
    symbols_filter: Optional[str] = None
    ts: str = Field(default_factory=utc_now_iso)


class ClockResponse(BaseModel):
    is_open: bool
    clock: Dict[str, Any] = Field(..., description="Raw alpaca-py clock dump")
    ts: str = Field(default_factory=utc_now_iso)


# --- Shared trade/risk models (for future graph use, reserved) ---

class TradeDecision(BaseModel):
    """Reserved for strategy agent output."""

    action: str = Field(..., description="buy|sell|hold")
    symbol: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    rationale: Optional[str] = None
    ts: str = Field(default_factory=utc_now_iso)


class RiskVerdict(BaseModel):
    """Reserved for risk agent output."""

    decision: str = Field(..., description="approved|approved_scaled|rejected")
    rule: Optional[str] = None
    adjusted_qty: Optional[float] = None
    ts: str = Field(default_factory=utc_now_iso)
