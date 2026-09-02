"""
Scheduler router — /api/v1/scheduler/status + /decisions
Strict API: reads logs/scheduler.json and logs/broker.jsonl, no mock.
Buffered for dashboard polling (30s), throttled via broker rate_limit not needed (local file).
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from backend.core.logging import log_broker_call
from backend.core.models import DecisionsResponse, SchedulerStatusResponse

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])

# Absolute paths — works regardless of cwd (uvicorn from project root vs backend/)
_ROOT = Path(__file__).resolve().parents[3]
_SCHEDULER_JSON = _ROOT / "backend" / "logs" / "scheduler.json"
_BROKER_JSONL = _ROOT / "backend" / "logs" / "broker.jsonl"


def _load_scheduler_state() -> Dict[str, Any]:
    try:
        if _SCHEDULER_JSON.exists():
            data = json.loads(_SCHEDULER_JSON.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


@router.get("/status", response_model=SchedulerStatusResponse, summary="Scheduler status (buffered)")
def get_scheduler_status():
    start = time.monotonic()
    state = _load_scheduler_state()
    # Also try market_hours for is_open
    mh: Optional[Dict[str, Any]] = None
    is_open: Optional[bool] = None
    try:
        from backend.scheduler.market_hours import is_market_open

        mh = is_market_open()
        is_open = bool(mh.get("is_open")) if isinstance(mh, dict) else None
    except Exception:
        mh = None
    latency = (time.monotonic() - start) * 1000
    log_broker_call("GET /api/v1/scheduler/status", latency, "200", run_count=state.get("run_count"))
    # started_at = first run or created_at if available, else last_run
    started_at = state.get("started_at") or state.get("last_run")
    # If run_count 0 and no last_run, started_at stays None (scheduler not started)
    return SchedulerStatusResponse(
        run_count=int(state.get("run_count", 0) or 0),
        last_run=state.get("last_run"),
        next_run=state.get("next_run"),
        last_status=state.get("last_status"),
        last_error=state.get("last_error"),
        thread_id=str(state.get("thread_id", "scheduler")),
        interval_min=int(state.get("interval_min", 5) or 5),
        updated_at=state.get("updated_at"),
        is_open=is_open,
        market_hours=mh,
        started_at=started_at,
    )


@router.get("/decisions", response_model=DecisionsResponse, summary="Recent decisions (buffered from broker.jsonl)")
def get_decisions(limit: int = Query(50, ge=1, le=200, description="1..200, default 50")):
    start = time.monotonic()
    decisions: List[Dict[str, Any]] = []
    try:
        if _BROKER_JSONL.exists():
            lines = _BROKER_JSONL.read_text(encoding="utf-8").splitlines()
            # Filter relevant events for dashboard feed
            relevant = {"scheduler_tick", "scheduler_tick_error", "scheduler_skip_closed", "scheduler_crypto_override", "scheduler_skip_duplicate", "risk_approved", "risk_rejected", "risk_no_trade", "execution_submitted", "buy", "sell", "scheduler_start"}
            for line in reversed(lines[-2000:]):  # last 2000 lines max
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ev = str(obj.get("event", ""))
                # Include scheduler_tick, risk_*, execution, buy/sell
                if ev in relevant or ev.startswith("risk_") or ev.startswith("execution_"):
                    decisions.append(obj)
                    if len(decisions) >= limit:
                        break
                # Also include any event with last_status
                elif "last_status" in obj:
                    decisions.append(obj)
                    if len(decisions) >= limit:
                        break
            # Already reversed, want newest first (we appended from reversed)
            # decisions is newest→oldest, keep as is for dashboard
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/scheduler/decisions", latency, "200", count=len(decisions), limit=limit)
        return DecisionsResponse(count=len(decisions), decisions=decisions)
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/scheduler/decisions", latency, "502", error=str(e))
        # Strict: no mock, return empty on error
        return DecisionsResponse(count=0, decisions=[])
