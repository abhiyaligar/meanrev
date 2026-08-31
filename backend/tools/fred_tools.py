"""
FRED tools — free macro search per https://api.stlouisfed.org/fred/series/search?api_key=...&search_text=...

Exposes FRED series search to AI (research agent) for discovering macro series like CPI, NFP, Canada etc.
Docs: search_text required, api_key required, file_type json, limit, order_by, etc.
Free tier: FRED_API_KEY from https://fred.stlouisfed.org/docs/api/api_key.html (instant).

Wired to research agent via TOOLS, uses core/config FRED_API_URL/FRED_API_KEY, TTLCache via utils (not committed until told).
"""

import json
from typing import Any, Dict, List

from langchain.tools import tool

from backend.core.logging import log_event
from backend.core.utils import TTLCache

# Cache search results 10 min (FRED search is not time-sensitive)
_SEARCH_CACHE = TTLCache(maxsize=100, ttl=600)


@tool
def search_fred_series(search_text: str, limit: int = 10, order_by: str = "popularity") -> str:
    """
    Search FRED series by text — free, no paid tier.

    Per https://api.stlouisfed.org/fred/series/search?api_key=YOUR_KEY&search_text=canada
    Args:
        search_text: query text e.g. "canada", "CPI", "unemployment", "GDP" (required, docs search_text)
        limit: 1..100 results to return (default 10, docs limit)
        order_by: popularity|search_rank|series_id|title|units|frequency|seasonal_adjustment|realtime_start|realtime_end|last_updated|observation_start|observation_end|vintage_date|vintage_dates (default popularity per docs)
    Returns JSON with seriess list (each has id, title, units, frequency, last_updated, etc.) or error if FRED_API_KEY missing.

    Example: search_fred_series(search_text="canada", limit=5) → [{id:"CPALTT01CAM659N", title:"CPI Canada", ...}]
    Use after to fetch observations via FRED_API_URL/fred/series/observations?series_id=...&api_key=...
    """
    try:
        q = str(search_text).strip()
        if not q:
            return json.dumps({"error": "search_text required, e.g. 'canada', 'CPI', 'NFP'"})
        lim = int(limit) if str(limit).isdigit() else 10
        lim = max(1, min(100, lim))
        order = order_by.strip() if order_by else "popularity"

        cache_key = f"{q}:{lim}:{order}"
        cached = _SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return json.dumps({"search_text": q, "count": len(cached), "seriess": cached, "cached": True}, default=str)

        # Resolve config
        from backend.core.config import get_settings
        import os

        s = get_settings()
        api_key = os.getenv("FRED_API_KEY") or getattr(s, "fred_api_key", None)
        base_url = getattr(s, "fred_api_url", None) or os.getenv("FRED_API_URL") or "https://api.stlouisfed.org/fred"
        base_url = str(base_url).rstrip("/")

        if not api_key:
            return json.dumps({"error": "FRED_API_KEY not set in .env — get free key at https://fred.stlouisfed.org/docs/api/api_key.html", "hint": "Set FRED_API_KEY=d7c0... in backend/.env"})

        import requests

        # Use get_settings base_url + /series/search
        url = f"{base_url}/series/search"
        params: Dict[str, Any] = {"search_text": q, "api_key": api_key, "file_type": "json", "limit": lim, "order_by": order}

        # Throttle via same bucket (optional)
        try:
            from backend.broker.rate_limit import bucket

            bucket.consume(1)
        except Exception:
            pass

        r = requests.get(url, params=params, timeout=10)
        if not r.ok:
            # Try to parse FRED error (often 400 with message)
            try:
                err = r.json()
            except Exception:
                err = r.text[:300]
            log_event("fred_search_error", level="warning", search_text=q, status=r.status_code, error=str(err)[:200])
            return json.dumps({"error": f"FRED search failed {r.status_code}: {err}", "search_text": q}, default=str)

        data = r.json()
        seriess: List[Dict[str, Any]] = data.get("seriess") or data.get("series") or []
        # Trim to limit
        seriess = seriess[:lim]
        _SEARCH_CACHE.set(cache_key, seriess)
        log_event("fred_search_ok", search_text=q, count=len(seriess))
        return json.dumps({"search_text": q, "count": len(seriess), "seriess": seriess}, default=str)

    except Exception as e:
        log_event("fred_search_failed", level="warning", error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__})


__all__ = ["search_fred_series"]
