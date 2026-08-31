"""
Market hours guard for scheduler — uses Alpaca clock (paper) with TTL cache.

PHASES.md 12b: Handle pre-market, regular (09:30-16:00 ET), post-market, closed.
Uses backend/broker/client.get_clock() (throttled 25/min) with 60s cache so tick
does not burn rate limit on every 5min check.

is_market_open() returns normalized dict:
  {is_open: bool, timestamp: str, next_open: str, next_close: str, now: datetime}
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.core.logging import log_event
from backend.core.utils import TTLCache

_cache = TTLCache(maxsize=10, ttl=60)


def get_market_clock() -> Dict[str, Any]:
    """Fetch clock via broker (cached 60s). Returns raw clock dict or empty on error."""
    cached = _cache.get("clock")
    if cached is not None:
        return cached
    try:
        from backend.broker.client import get_clock

        clock = get_clock()
        if isinstance(clock, dict):
            _cache.set("clock", clock)
            return clock
        return {}
    except Exception as e:
        log_event("scheduler_clock_error", level="warning", error=str(e)[:200])
        return {}


def is_market_open() -> Dict[str, Any]:
    """
    Normalized market hours check.
    Returns {is_open: bool, timestamp, next_open, next_close, now_iso, raw}.
    If clock fetch fails, returns is_open=False with now for safe fallback (skip tick).
    """
    raw = get_market_clock()
    now_iso = datetime.now(timezone.utc).isoformat()
    if not raw:
        return {"is_open": False, "timestamp": now_iso, "next_open": None, "next_close": None, "now_iso": now_iso, "raw": {}}
    # Alpaca clock shape: {is_open: bool, timestamp, next_open, next_close} — all ISO strings
    is_open = bool(raw.get("is_open", False))
    # Normalize keys (some SDKs return camelCase, but our _dump uses mode=json so snakes)
    ts = raw.get("timestamp") or raw.get("next_open") or now_iso
    nxt_open = raw.get("next_open") or raw.get("nextOpen")
    nxt_close = raw.get("next_close") or raw.get("nextClose")
    return {
        "is_open": is_open,
        "timestamp": ts,
        "next_open": nxt_open,
        "next_close": nxt_close,
        "now_iso": now_iso,
        "raw": raw,
    }


def seconds_until_next_open() -> Optional[float]:
    """Seconds from now until next_open, or None if unknown."""
    mh = is_market_open()
    nxt = mh.get("next_open")
    if not nxt:
        return None
    try:
        # Parse ISO (handles +00:00, Z)
        nxt_s = str(nxt).replace("Z", "+00:00")
        nxt_dt = datetime.fromisoformat(nxt_s)
        if nxt_dt.tzinfo is None:
            nxt_dt = nxt_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = (nxt_dt - now).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None
