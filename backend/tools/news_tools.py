"""
News tools — LangChain @tool wrappers around backend/data/news.py.

Per langchain-docs MCP: @tool + type hints + docstring.
All calls cached (5 min) and never log secrets; sentiment via lexicon.
"""

import json
from langchain.tools import tool

from backend.data.news import extract_keywords as _extract_keywords, fetch_news as _fetch_news, get_macro_calendar as _get_macro_calendar


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
        return json.dumps({"count": len(headlines), "headlines": headlines}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def get_macro_calendar(days_ahead: int = 7) -> str:
    """Get upcoming macro catalysts (Fed speeches, NFP, CPI, earnings, benchmark revisions). Args: days_ahead 1..30 (default 7). Returns list of {event, date, importance, description}."""
    try:
        try:
            d = int(days_ahead)
        except (TypeError, ValueError):
            d = 7
        d = max(1, min(d, 30))
        cal = _get_macro_calendar(days_ahead=d)
        return json.dumps({"count": len(cal), "calendar": cal}, default=str)
    except Exception as e:
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
        return json.dumps({"keywords": keywords, "count": len(keywords)}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})
