"""
Exa Web Search tools — LangChain @tool wrappers for https://api.exa.ai/search

Per https://exa.ai/docs/reference/search-api-guide-for-coding-agents:
- POST https://api.exa.ai/search Authorization: Bearer EXA_API_KEY
- Best for agents: contents.highlights true (10x token saving), type auto (1s), maxAgeHours 0 for fresh news
- Python SDK pip exa-py snake_case (num_results), but we use raw requests for determinism/stub friendliness

Three tools:
- exa_search: generic web search (company|news|publication|personal site|financial report + domains + date)
- exa_search_news: news-focused wrapper (category news + recent days_back -> startPublishedDate)
- exa_get_contents: fetch full content for given URLs (POST /contents) with highlights/text

All respect EXA_API_KEY via core/config, TTLCache, bucket 25/min, never mock — returns error JSON if not configured.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from backend.core.logging import log_event
from backend.core.utils import TTLCache, clamp_limit

# Caches: 5 min search, 10 min contents
_SEARCH_CACHE = TTLCache(maxsize=200, ttl=300)
_CONTENTS_CACHE = TTLCache(maxsize=200, ttl=600)


def _exa_config() -> Dict[str, Any]:
    """Resolve Exa config from settings + env."""
    try:
        from backend.core.config import get_settings

        s = get_settings()
        return s.exa_config()
    except Exception:
        import os

        return {
            "api_key": os.getenv("EXA_API_KEY"),
            "base_url": os.getenv("EXA_BASE_URL") or "https://api.exa.ai",
            "default_type": os.getenv("EXA_DEFAULT_TYPE") or "auto",
            "default_num": int(os.getenv("EXA_DEFAULT_NUM") or 5),
        }


def _require_key() -> Optional[str]:
    cfg = _exa_config()
    key = (cfg.get("api_key") or "").strip() if cfg.get("api_key") else ""
    if not key:
        return None
    return key


def _iso_date_days_back(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=int(days))
    return dt.date().isoformat()


def _do_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute POST /search, returns parsed JSON or error dict."""
    cfg = _exa_config()
    api_key = _require_key()
    base_url = cfg.get("base_url", "https://api.exa.ai").rstrip("/")

    if not api_key:
        return {
            "error": "EXA_API_KEY not set in .env — get free key at https://dashboard.exa.ai/api-keys",
            "hint": "Set EXA_API_KEY=... in backend/.env and restart. Free tier covers search.",
        }

    # Throttle via same bucket as broker/fred
    try:
        from backend.broker.rate_limit import bucket

        bucket.consume(1)
    except Exception:
        pass

    import requests

    url = f"{base_url}/search"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    # Exa docs: contents nested, highlights true for agents, maxAgeHours omitted defaults livecrawl fallback
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        log_event("exa_search_request_failed", level="warning", error=str(e)[:200])
        return {"error": f"Exa request failed: {e}", "type": type(e).__name__}

    if not r.ok:
        try:
            err = r.json()
        except Exception:
            err = r.text[:500]
        log_event("exa_search_error", level="warning", status=r.status_code, error=str(err)[:300], query=str(payload.get("query"))[:80])
        return {"error": f"Exa search failed {r.status_code}: {err}", "query": payload.get("query")}

    try:
        data = r.json()
    except Exception as e:
        return {"error": f"Exa invalid JSON: {e}", "raw": r.text[:500]}

    return data


@tool
def exa_search(
    query: str,
    num_results: int = 5,
    type: str = "auto",
    category: str = "",
    include_domains: str = "",
    exclude_domains: str = "",
    days_back: Optional[int] = None,
) -> str:
    """
    Web search via Exa — public perception + general web context for research.

    Per https://exa.ai/docs/reference/search-api-guide-for-coding-agents POST /search Authorization: Bearer EXA_API_KEY
    Use for: company news, public sentiment, policy updates, earnings chatter, crypto narrative beyond Alpaca News.

    Args:
        query: natural language query, e.g. "TSLA earnings public perception reddit", "Fed rate decision impact BTC" (required)
        num_results: 1..10 results (default 5, Exa supports 1..100 but we clamp 1..10 for agent context)
        type: auto|fast|instant|deep-lite|deep|deep-reasoning (default auto 1s balanced; use fast 450ms for latency-sensitive, deep 4-15s for synthesis)
        category: company|people|publication|news|personal site|financial report (optional, filters content type)
        include_domains: comma list e.g. "reuters.com,nytimes.com" (optional, max 1200 domains)
        exclude_domains: comma list e.g. "reddit.com" (optional)
        days_back: only links published within last N days (optional, 1..365, sets startPublishedDate)
    Returns JSON {"count": n, "results": [{"title","url","publishedDate","highlights":[...],"summary"}], "costDollars":...} or {"error": ...}
    Token-efficient: uses contents.highlights true (not full text) per Exa guide for agents.
    """
    try:
        q = str(query or "").strip()
        if not q:
            return json.dumps({"error": "query required, e.g. 'TSLA earnings perception', 'CPI Canada impact'"}, default=str)

        n = clamp_limit(num_results, default=5, min_val=1, max_val=10)
        t = str(type or "auto").strip().lower()
        if t not in ("auto", "fast", "instant", "deep-lite", "deep", "deep-reasoning"):
            t = "auto"
        cat = str(category or "").strip().lower()
        if cat and cat not in ("company", "people", "publication", "news", "personal site", "financial report"):
            # map common aliases
            if cat in ("person", "persons"):
                cat = "people"
            else:
                cat = ""

        inc = [d.strip() for d in str(include_domains or "").split(",") if d.strip()] or None
        exc = [d.strip() for d in str(exclude_domains or "").split(",") if d.strip()] or None

        start_date = None
        if days_back is not None:
            try:
                db = int(days_back)
                db = max(1, min(365, db))
                start_date = _iso_date_days_back(db)
            except Exception:
                start_date = None

        # Cache key includes all params
        cache_key = f"search:{q}:{n}:{t}:{cat}:{','.join(inc or [])}:{','.join(exc or [])}:{start_date}"
        cached = _SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return json.dumps({"query": q, "count": len(cached.get("results", [])), **cached, "cached": True}, default=str)

        payload: Dict[str, Any] = {
            "query": q,
            "type": t,
            "numResults": n,
            "contents": {"highlights": True, "extras": {"links": 1}},
        }
        if cat:
            payload["category"] = cat
        if inc:
            payload["includeDomains"] = inc
        if exc:
            payload["excludeDomains"] = exc
        if start_date:
            payload["startPublishedDate"] = start_date
        # For fresh news-like queries, force livecrawl if days_back <=2
        if start_date and days_back is not None and int(days_back) <= 2:
            payload["contents"]["maxAgeHours"] = 0

        data = _do_search(payload)
        if "error" in data:
            return json.dumps(data, default=str)

        # Normalize shape: ensure results list
        results = data.get("results") or []
        # Trim highlights to keep context small already, keep as is
        _SEARCH_CACHE.set(cache_key, data)
        log_event("exa_search_ok", query=q[:80], count=len(results), type=t, category=cat or "none")
        # Return trimmed for agent consumption (full data cached)
        return json.dumps(
            {"query": q, "count": len(results), "results": results, "costDollars": data.get("costDollars")},
            default=str,
        )

    except Exception as e:
        log_event("exa_search_failed", level="warning", error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__}, default=str)


@tool
def exa_search_news(query: str, num_results: int = 5, days_back: int = 7) -> str:
    """
    News-focused web search via Exa (category news, recent).

    Per Exa guide: category news + startPublishedDate + highlights true + maxAgeHours 0 for freshness when days_back <=2.
    Use for: public perception, headlines beyond Alpaca News, sentiment on earnings/policy/crypto.

    Args:
        query: news query e.g. "Fed decision impact BTC", "NVDA earnings beats" (required)
        num_results: 1..10 (default 5)
        days_back: only news in last N days 1..365 (default 7, sets startPublishedDate)
    Returns JSON {"count": n, "results": [{"title","url","publishedDate","highlights":[...]}], "category":"news"} or error
    """
    try:
        q = str(query or "").strip()
        if not q:
            return json.dumps({"error": "query required, e.g. 'BTC ETF approval news'"}, default=str)

        n = clamp_limit(num_results, default=5, min_val=1, max_val=10)
        db = int(days_back) if str(days_back).isdigit() else 7
        db = max(1, min(365, db))

        # Reuse generic search with news category
        # Inline to preserve news-specific cache key and forced freshness
        cfg = _exa_config()
        api_key = _require_key()
        if not api_key:
            return json.dumps(
                {"error": "EXA_API_KEY not set in .env — get free key at https://dashboard.exa.ai/api-keys"},
                default=str,
            )

        cache_key = f"news:{q}:{n}:{db}"
        cached = _SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return json.dumps({"query": q, "count": len(cached.get("results", [])), **cached, "cached": True, "category": "news"}, default=str)

        payload: Dict[str, Any] = {
            "query": q,
            "type": "auto",
            "numResults": n,
            "category": "news",
            "startPublishedDate": _iso_date_days_back(db),
            "contents": {"highlights": True, "extras": {"links": 1}},
        }
        if db <= 2:
            payload["contents"]["maxAgeHours"] = 0

        data = _do_search(payload)
        if "error" in data:
            return json.dumps(data, default=str)

        results = data.get("results") or []
        _SEARCH_CACHE.set(cache_key, data)
        log_event("exa_search_news_ok", query=q[:80], count=len(results), days_back=db)
        return json.dumps(
            {"query": q, "count": len(results), "results": results, "category": "news", "days_back": db, "costDollars": data.get("costDollars")},
            default=str,
        )

    except Exception as e:
        log_event("exa_search_news_failed", level="warning", error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__}, default=str)


@tool
def exa_get_contents(urls: str, query: str = "") -> str:
    """
    Fetch full content for URLs via Exa /contents (highlights/text).

    Use after exa_search to deep-dive a specific URL when highlights insufficient.
    Per Exa docs: POST /contents with ids/urls + contents {highlights:true} or {text:{maxCharacters:8000}}

    Args:
        urls: comma-separated URLs e.g. "https://reuters.com/article/...,https://sec.gov/..." (1..5, required)
        query: optional highlight query to focus excerpts, e.g. "CPI impact on SPY" (if empty, highlights default to page relevance)
    Returns JSON {"count": n, "results": [{"url","title","highlights":[...],"text":"..."}]} or error
    Uses maxAgeHours 0 for fresh content when query implies recency.
    """
    try:
        url_list = [u.strip() for u in str(urls or "").split(",") if u.strip()]
        if not url_list:
            return json.dumps({"error": "urls required, comma-separated e.g. 'https://example.com/a,https://example.com/b'"}, default=str)
        url_list = url_list[:5]  # clamp to 5 to respect token budget
        q = str(query or "").strip()

        cache_key = f"contents:{','.join(url_list)}:{q}"
        cached = _CONTENTS_CACHE.get(cache_key)
        if cached is not None:
            return json.dumps({"count": len(cached.get("results", [])), **cached, "cached": True}, default=str)

        cfg = _exa_config()
        api_key = _require_key()
        base_url = cfg.get("base_url", "https://api.exa.ai").rstrip("/")
        if not api_key:
            return json.dumps(
                {"error": "EXA_API_KEY not set in .env — get free key at https://dashboard.exa.ai/api-keys"},
                default=str,
            )

        try:
            from backend.broker.rate_limit import bucket

            bucket.consume(1)
        except Exception:
            pass

        import requests

        url = f"{base_url}/contents"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload: Dict[str, Any] = {
            "ids": url_list,
            "contents": {"highlights": True, "text": {"maxCharacters": 8000}},
        }
        if q:
            payload["contents"]["highlights"] = {"query": q}
            # also set summary query if useful
            # payload["query"] = q

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
        except Exception as e:
            return json.dumps({"error": f"Exa contents request failed: {e}", "type": type(e).__name__}, default=str)

        if not r.ok:
            try:
                err = r.json()
            except Exception:
                err = r.text[:500]
            log_event("exa_contents_error", level="warning", status=r.status_code, error=str(err)[:300])
            return json.dumps({"error": f"Exa contents failed {r.status_code}: {err}"}, default=str)

        try:
            data = r.json()
        except Exception as e:
            return json.dumps({"error": f"Exa contents invalid JSON: {e}", "raw": r.text[:500]}, default=str)

        results = data.get("results") or []
        _CONTENTS_CACHE.set(cache_key, data)
        log_event("exa_contents_ok", count=len(results), urls=str(url_list)[:120])
        return json.dumps({"count": len(results), "results": results, "query": q}, default=str)

    except Exception as e:
        log_event("exa_contents_failed", level="warning", error=str(e)[:200])
        return json.dumps({"error": str(e), "type": type(e).__name__}, default=str)


EXA_TOOLS = [exa_search, exa_search_news, exa_get_contents]

__all__ = ["exa_search", "exa_search_news", "exa_get_contents", "EXA_TOOLS"]
