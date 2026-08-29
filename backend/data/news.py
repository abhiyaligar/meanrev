"""
News and macro catalyst data — Phase 4.3

- News fetch (Alpaca News API when configured, else mock for offline dev)
- Sentiment scoring (headline sentiment, social velocity stub, keyword extraction)
- Macro calendar (Fed speeches, NFP, CPI, earnings, benchmark revisions)

All functions return normalized dicts ready for Research agent (Claude).
Rate-limited and cached to respect free-tier limits; never logs secrets.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from backend.core.logging import log_event

# Simple cache
_NEWS_CACHE: Dict[str, tuple[float, List[Dict[str, str]]]] = {}
_MACRO_CACHE: tuple[float, List[Dict[str, str]]] | None = None
CACHE_TTL = 300  # 5 min


def _sentiment_score(text: str) -> float:
    """
    Lightweight headline sentiment in [-1,1].
    Uses keyword lexicon (no external call) — deterministic, fast, testable.
    Positive words +1, negative -1, normalized by word count.
    """
    if not text:
        return 0.0
    positive = {
        "beat",
        "beats",
        "surge",
        "surged",
        "rally",
        "gains",
        "gain",
        "up",
        "rise",
        "rises",
        "record",
        "high",
        "growth",
        "strong",
        "bullish",
        "buy",
        "upgrade",
        "optimism",
    }
    negative = {
        "miss",
        "misses",
        "plunge",
        "drop",
        "drops",
        "fall",
        "falls",
        "down",
        "decline",
        "weak",
        "bearish",
        "sell",
        "downgrade",
        "cut",
        "loss",
        "losses",
        "fear",
        "recession",
    }
    words = re.findall(r"\w+", text.lower())
    if not words:
        return 0.0
    score = 0
    for w in words:
        if w in positive:
            score += 1
        elif w in negative:
            score -= 1
    # Normalize to [-1,1] with dampening
    return max(-1.0, min(1.0, score / max(5, len(words) * 0.3)))


def fetch_news(
    symbols: Optional[List[str]] = None,
    limit: int = 20,
) -> List[Dict[str, str]]:
    """
    Fetch news headlines. Tries Alpaca NewsClient if configured, else returns
    deterministic mock headlines so Research agent can run offline.

    Returns list of {headline, symbol, timestamp, source, sentiment, sentiment_score}
    """
    # Clamp
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 20
    if lim < 1:
        lim = 1
    lim = min(lim, 100)

    sym_key = ",".join(sorted(s.upper() for s in symbols)) if symbols else "general"
    cache_key = f"{sym_key}:{lim}"
    entry = _NEWS_CACHE.get(cache_key)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return entry[1][:lim]

    headlines: List[Dict[str, str]] = []
    tried_alpaca = False

    # Try Alpaca NewsClient (requires same paper creds)
    try:
        from backend.core.config import get_settings

        s = get_settings()
        key = s.get_key()
        secret = s.get_secret()
        if key and secret:
            tried_alpaca = True
            from alpaca.data.historical import NewsClient  # type: ignore
            from alpaca.data.requests import NewsRequest  # type: ignore

            client = NewsClient(api_key=key, secret_key=secret)
            # NewsRequest expects symbols as comma string or single; handle both list and None
            try:
                if symbols:
                    req = NewsRequest(symbols=",".join(s.upper() for s in symbols), limit=lim)  # type: ignore
                else:
                    req = NewsRequest(limit=lim)  # type: ignore
            except Exception:
                req = NewsRequest(symbols=symbols or None, limit=lim)  # type: ignore fallback
            # Timeout per VULN 3 pattern
            import concurrent.futures

            def _do():
                return client.get_news(req)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                resp = ex.submit(_do).result(timeout=15)

            # Normalize — resp.news is list of News objects
            news_list = getattr(resp, "news", None) or getattr(resp, "data", {}).values() or []
            if isinstance(news_list, dict):
                news_list = list(news_list.values())
            for n in list(news_list)[:lim]:
                if hasattr(n, "model_dump"):
                    d = n.model_dump()
                elif hasattr(n, "dict"):
                    d = n.dict()
                elif isinstance(n, dict):
                    d = n
                else:
                    d = {"headline": str(n)}
                headline = d.get("headline") or d.get("title") or d.get("summary") or ""
                headlines.append(
                    {
                        "headline": str(headline)[:300],
                        "symbol": str(d.get("symbols") or d.get("symbol") or sym_key),
                        "timestamp": str(d.get("created_at") or d.get("updated_at") or datetime.now(timezone.utc).isoformat()),
                        "source": str(d.get("source") or "alpaca"),
                        "sentiment_score": str(_sentiment_score(str(headline))),
                    }
                )
            if headlines:
                log_event("news_fetch_alpaca_ok", symbols=sym_key, count=len(headlines))
    except Exception as e:
        if tried_alpaca:
            log_event("news_fetch_alpaca_error", level="warning", error=str(e)[:200], symbols=sym_key)

    # Fallback mock — deterministic so tests and offline dev work
    if not headlines:
        mock_base = [
            "Market holds steady ahead of Fed speech",
            "NFP preview: labor market resilience eyed",
            "CPI release could sway rate path expectations",
            "Earnings season lifts sentiment for large caps",
            "Options flow mixed into expiry week",
        ]
        for i in range(lim):
            h = mock_base[i % len(mock_base)]
            if symbols:
                h = f"{symbols[0].upper()}: {h}"
            headlines.append(
                {
                    "headline": h,
                    "symbol": sym_key,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "mock",
                    "sentiment_score": str(_sentiment_score(h)),
                }
            )
        log_event("news_fetch_mock", symbols=sym_key, count=len(headlines))

    # Enrich with string sentiment label
    for h in headlines:
        try:
            score = float(h.get("sentiment_score", 0))
        except Exception:
            score = 0
        if score > 0.2:
            h["sentiment"] = "bullish"
        elif score < -0.2:
            h["sentiment"] = "bearish"
        else:
            h["sentiment"] = "neutral"

    _NEWS_CACHE[cache_key] = (time.time(), headlines)
    return headlines[:lim]


def get_macro_calendar(days_ahead: int = 7) -> List[Dict[str, str]]:
    """
    Return upcoming macro catalysts: Fed speeches, NFP, CPI, earnings, benchmark revisions.
    For v1, returns a deterministic mock calendar (no external calendar API required)
    so Research agent has catalyst data even offline. Cache 5 min.

    Each entry: {event, date, importance, description}
    """
    global _MACRO_CACHE
    if _MACRO_CACHE and time.time() - _MACRO_CACHE[0] < CACHE_TTL:
        return _MACRO_CACHE[1]

    now = datetime.now(timezone.utc)
    # Mock calendar — dates are relative to now for demo stability
    calendar = [
        {
            "event": "Fed Speech",
            "date": (now + timedelta(days=1)).date().isoformat(),
            "importance": "high",
            "description": "Scheduled Fed commentary — rate path watch",
        },
        {
            "event": "NFP",
            "date": (now + timedelta(days=3)).date().isoformat(),
            "importance": "high",
            "description": "Non-Farm Payrolls — labor market health",
        },
        {
            "event": "CPI",
            "date": (now + timedelta(days=5)).date().isoformat(),
            "importance": "high",
            "description": "Consumer Price Index — inflation gauge",
        },
        {
            "event": "Earnings",
            "date": (now + timedelta(days=2)).date().isoformat(),
            "importance": "medium",
            "description": "Large-cap earnings batch",
        },
        {
            "event": "Benchmark Revision",
            "date": (now + timedelta(days=6)).date().isoformat(),
            "importance": "medium",
            "description": "Index rebalancing and benchmark revision",
        },
    ]
    # Filter to days_ahead
    cutoff = now.date() + timedelta(days=days_ahead)
    filtered = [e for e in calendar if e["date"] <= cutoff.isoformat()]

    _MACRO_CACHE = (time.time(), filtered)
    log_event("macro_calendar_mock", count=len(filtered), days_ahead=days_ahead)
    return filtered


def extract_keywords(headlines: List[Dict[str, str]], top_k: int = 10) -> List[str]:
    """
    Simple NLP keyword extraction — top frequent non-stopwords from headlines.
    Used for Research agent social velocity / keyword signals.
    """
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
        "ahead",
        "could",
        "market",
        "fed",
    }
    freq: Dict[str, int] = {}
    for h in headlines:
        text = h.get("headline", "")
        for w in re.findall(r"\w+", text.lower()):
            if w in stop or len(w) < 3:
                continue
            freq[w] = freq.get(w, 0) + 1
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:top_k]]
