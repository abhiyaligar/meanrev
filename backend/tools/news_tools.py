"""
News tools — LangChain @tool wrappers around backend/data/news.py.

Per langchain-docs MCP: @tool + type hints + docstring.
All calls cached (5 min) and never log secrets; sentiment via lexicon.
"""

import json
from langchain.tools import tool
from backend.core.logging import log_event
from backend.data.news import (
    extract_keywords as _extract_keywords,
    fetch_news as _fetch_news,
    get_macro_calendar as _get_macro_calendar,
)

@tool
def fetch_news(symbols: str = "", limit: int = 10) -> str:
    """Fetch news headlines with sentiment. Args: symbols comma list e.g. 'AAPL,TSLA' (optional, default general), limit 1..100 (default 10). Returns headlines with sentiment bullish/bearish/neutral and score -1..1."""
    try:
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 10
        if lim < 1:
            lim = 1
        lim = min(lim, 100)
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols.strip() else None
        headlines = _fetch_news(symbols=syms, limit=lim)
        try:
            log_event("news_fetch", level="info", symbols=",".join(syms) if syms else "general", limit=lim, count=len(headlines) if isinstance(headlines, list) else 0, status="ok")
        except Exception:
            pass
        return json.dumps({"count": len(headlines), "headlines": headlines}, default=str)
    except Exception as e:
        try:
            log_event("news_fetch", level="warning", symbols=str(symbols)[:100], error=str(e)[:200], status="error")
        except Exception:
            pass
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def get_macro_calendar(days_ahead: int = 7) -> str:
    """Get upcoming/latest US macro catalysts, grouped by source. Args: days_ahead 1..30 (default 7) — only affects the finnhub window; fred is always the latest print.
    Returns {"finnhub": {"count": int, "events": [{event, time, country, actual, estimate, prev, source}]},
             "fred": {"count": int, "events": [{event, time, value, series_id, source}]}}.
    "finnhub" events are forward-looking (scheduled within days_ahead). "fred" events are the latest confirmed CPI/NFP/Unemployment/Fed Funds prints, not forward-looking. Either list may be empty — that's a valid result, not an error."""
    try:
        try:
            d = int(days_ahead)
        except (TypeError, ValueError):
            d = 7
        d = max(1, min(d, 30))
        cal = _get_macro_calendar(days_ahead=d)
        finnhub_events = cal.get("finnhub", [])
        fred_events = cal.get("fred", [])
        try:
            log_event("news_macro_calendar", level="info", days_ahead=d, finnhub_count=len(finnhub_events) if isinstance(finnhub_events, list) else 0, fred_count=len(fred_events) if isinstance(fred_events, list) else 0, status="ok")
        except Exception:
            pass
        return json.dumps(
            {
                "finnhub": {"count": len(finnhub_events), "events": finnhub_events},
                "fred": {"count": len(fred_events), "events": fred_events},
            },
            default=str,
        )
    except Exception as e:
        try:
            log_event("news_macro_calendar", level="warning", error=str(e)[:200], status="error")
        except Exception:
            pass
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def extract_keywords(symbols: str = "", top_k: int = 10) -> str:
    """Extract top keywords from recent headlines for sentiment velocity. Args: symbols comma list (optional), top_k 1..50 (default 10). Returns list of keywords."""
    try:
        try:
            k = int(top_k)
        except (TypeError, ValueError):
            k = 10
        k = max(1, min(k, 50))
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols.strip() else None
        # Reuse fetch_news for headlines
        headlines = _fetch_news(symbols=syms, limit=20)
        keywords = _extract_keywords(headlines, top_k=k)
        try:
            log_event("news_extract_keywords", level="info", symbols=",".join(syms) if syms else "general", top_k=k, count=len(keywords) if isinstance(keywords, list) else 0, status="ok")
        except Exception:
            pass
        return json.dumps({"keywords": keywords, "count": len(keywords)}, default=str)
    except Exception as e:
        try:
            log_event("news_extract_keywords", level="warning", symbols=str(symbols)[:100], error=str(e)[:200], status="error")
        except Exception:
            pass
        return json.dumps({"error": str(e), "type": type(e).__name__})