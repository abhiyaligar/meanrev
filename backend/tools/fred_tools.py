"""
FRED tools — free macro search per https://api.stlouisfed.org/fred/series/search?api_key=...&search_text=...

Exposes FRED series search to AI (research agent) for discovering macro series like CPI, NFP, Canada etc.
Docs: search_text required, api_key required, file_type json, limit, order_by, etc.
Free tier: FRED_API_KEY from https://fred.stlouisfed.org/docs/api/api_key.html (instant).

Wired to research agent via TOOLS. Uses core/config FRED_API_URL/FRED_API_KEY and the shared
TTLCache from core/utils to cache search results for 10 minutes (search results aren't time-sensitive).
"""

import json
import os
from typing import Any, Dict, List

import requests
from langchain.tools import tool

from backend.broker.rate_limit import bucket
from backend.core.config import get_settings
from backend.core.logging import log_event
from backend.core.utils import TTLCache

# Cache search results 10 min (FRED search is not time-sensitive)
_SEARCH_CACHE = TTLCache(maxsize=100, ttl=600)

_VALID_ORDER_BY = {
    "popularity", "search_rank", "series_id", "title", "units", "frequency",
    "seasonal_adjustment", "realtime_start", "realtime_end", "last_updated",
    "observation_start", "observation_end", "vintage_date", "vintage_dates",
}
_DEFAULT_ORDER_BY = "popularity"


def _parse_limit(limit: Any, default: int = 10, lo: int = 1, hi: int = 100) -> int:
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, lim))


def _parse_order_by(order_by: str) -> str:
    candidate = (order_by or "").strip()
    return candidate if candidate in _VALID_ORDER_BY else _DEFAULT_ORDER_BY


def _consume_rate_limit_token() -> None:
    """Best-effort rate limiting. Missing/broken limiter is fine to ignore;
    an actual rate-limit rejection is logged, not silently swallowed."""
    try:
        bucket.consume(1)
    except Exception as e:
        log_event("fred_search_rate_limit_skipped", level="warning", error=str(e)[:200])


@tool
def search_fred_series(search_text: str, limit: int = 10, order_by: str = "popularity") -> str:
    """
    Search FRED series by text — free, no paid tier.

    Per https://api.stlouisfed.org/fred/series/search?api_key=YOUR_KEY&search_text=canada
    Args:
        search_text: query text e.g. "canada", "CPI", "unemployment", "GDP" (required, docs search_text)
        limit: 1..100 results to return (default 10, docs limit)
        order_by: popularity|search_rank|series_id|title|units|frequency|seasonal_adjustment|realtime_start|realtime_end|last_updated|observation_start|observation_end|vintage_date|vintage_dates (default popularity per docs; invalid values fall back to popularity)
    Returns JSON with seriess list (each has id, title, units, frequency, last_updated, etc.) or error if FRED_API_KEY missing.

    Example: search_fred_series(search_text="canada", limit=5) → [{id:"CPALTT01CAM659N", title:"CPI Canada", ...}]
    Use after to fetch observations via FRED_API_URL/fred/series/observations?series_id=...&api_key=...
    """
    try:
        query = str(search_text).strip()
        if not query:
            return json.dumps({"error": "search_text required, e.g. 'canada', 'CPI', 'NFP'"})

        lim = _parse_limit(limit)
        order = _parse_order_by(order_by)

        cache_key = f"{query}:{lim}:{order}"
        cached = _SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return json.dumps({"search_text": query, "count": len(cached), "seriess": cached, "cached": True}, default=str)

        settings = get_settings()
        api_key = os.getenv("FRED_API_KEY") or getattr(settings, "fred_api_key", None)
        if not api_key:
            return json.dumps(
                {
                    "error": "FRED_API_KEY not set in .env — get free key at https://fred.stlouisfed.org/docs/api/api_key.html",
                    "hint": "Set FRED_API_KEY=d7c0... in backend/.env",
                }
            )

        base_url = str(getattr(settings, "fred_api_url", None) or os.getenv("FRED_API_URL") or "https://api.stlouisfed.org/fred").rstrip("/")
        params: Dict[str, Any] = {
            "search_text": query,
            "api_key": api_key,
            "file_type": "json",
            "limit": lim,
            "order_by": order,
        }

        _consume_rate_limit_token()

        resp = requests.get(f"{base_url}/series/search", params=params, timeout=10)
        if not resp.ok:
            try:
                err = resp.json()
            except Exception:
                err = resp.text[:300]
            log_event("fred_search_error", level="warning", search_text=query, status=resp.status_code, error=str(err)[:200])
            return json.dumps({"error": f"FRED search failed {resp.status_code}: {err}", "search_text": query}, default=str)

        # FRED's actual response key is "seriess" (their spelling, not a typo here).
        seriess: List[Dict[str, Any]] = (resp.json().get("seriess") or [])[:lim]

        _SEARCH_CACHE.set(cache_key, seriess)
        log_event("fred_search_ok", search_text=query, count=len(seriess))
        return json.dumps({"search_text": query, "count": len(seriess), "seriess": seriess}, default=str)

    except Exception as e:
        log_event("fred_search_failed", level="warning", error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__})


__all__ = ["search_fred_series"]