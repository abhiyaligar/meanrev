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
from backend.core.utils import TTLCache

# Library-backed TTL cache via core/utils (cachetools) — replaces custom dict tuple
_NEWS_CACHE = TTLCache(maxsize=200, ttl=300)
_MACRO_CACHE = TTLCache(maxsize=10, ttl=300)  # single key "macro"
CACHE_TTL = 300  # kept for compatibility, actual TTL is in TTLCache


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
    cached = _NEWS_CACHE.get(cache_key)
    if cached is not None:
        return cached[:lim]

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

    # No mock fallback — if Alpaca returns no data, return empty and let caller handle "No data available"
    if not headlines:
        log_event("news_no_data", symbols=sym_key, reason="No data available for this symbols/timeframe (Alpaca returned 0 headlines)")
        # Return empty list — tools and research will output "No data available for this"
        _NEWS_CACHE.set(cache_key, [])
        return []

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

    _NEWS_CACHE.set(cache_key, headlines)
    return headlines[:lim]


def get_macro_calendar(days_ahead: int = 7) -> List[Dict[str, str]]:
    """
    Return upcoming macro catalysts: Fed speeches, NFP, CPI, earnings, benchmark revisions.
    Free sources (no paid tier):
      1) Finnhub economic calendar (US, next 7d) — FINNHUB_API_KEY (free 60/min)
      2) FRED observations for CPI/NFP (CPIAUCSL, PAYEMS) — FRED_API_KEY (free) or CSV fallback without key
      3) Zero-key ICS fallback via FRED CSV if no keys set — still returns CPI level for research
    Caller should handle empty as "No data available for this".
    Uses library-backed TTLCache (cachetools) via core/utils.
    """
    cached = _MACRO_CACHE.get("macro")
    if cached is not None:
        return cached

    events: List[Dict[str, str]] = []

    # 1) Finnhub economic calendar (closest to docs: Fed speeches, NFP, CPI)
    try:
        from backend.core.config import get_settings
        import os
        import requests

        s = get_settings()
        # Use FRED_API_URL base if needed for search, but for calendar use Finnhub
        finnhub_key = os.getenv("FINNHUB_API_KEY") or getattr(s, "finnhub_api_key", None)
        if finnhub_key:
            # Finnhub calendar returns {economicCalendar:[{event,country,time,actual,estimate,prev}]}
            r = requests.get("https://finnhub.io/api/v1/calendar/economic", params={"token": finnhub_key}, timeout=8)
            if r.ok:
                data = r.json()
                raw = data.get("economicCalendar") or data.get("economic_calendar") or []
                # Filter US next 7d
                cutoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)
                for e in raw:
                    if str(e.get("country", "")).upper() not in ("US", "USA", ""):
                        continue
                    # Parse time
                    t = e.get("time") or e.get("date") or ""
                    try:
                        dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt > cutoff:
                            continue
                    except Exception:
                        pass
                    events.append(
                        {
                            "event": str(e.get("event") or e.get("name") or "Macro"),
                            "time": str(t),
                            "country": "US",
                            "actual": str(e.get("actual") or ""),
                            "estimate": str(e.get("estimate") or ""),
                            "prev": str(e.get("prev") or ""),
                            "source": "finnhub",
                        }
                    )
                if events:
                    events = events[:10]
                    _MACRO_CACHE.set("macro", events)
                    log_event("macro_calendar_finnhub_ok", count=len(events))
                    return events
    except Exception as e:
        log_event("macro_calendar_finnhub_error", level="warning", error=str(e)[:200])

    # 2) FRED observations for CPI/NFP levels (free, no calendar but latest print)
    try:
        import os
        import requests

        from backend.core.config import get_settings

        s = get_settings()
        fred_key = os.getenv("FRED_API_KEY") or getattr(s, "fred_api_key", None)
        if fred_key:
            for series_id, name in [("CPIAUCSL", "CPI"), ("PAYEMS", "NFP"), ("UNRATE", "Unemployment"), ("FEDFUNDS", "Fed Funds")]:
                try:
                    # Use configurable FRED_API_URL
                    base = getattr(s, "fred_api_url", None) or os.getenv("FRED_API_URL") or "https://api.stlouisfed.org/fred"
                    base = str(base).rstrip("/")
                    r = requests.get(
                        f"{base}/series/observations",
                        params={"series_id": series_id, "api_key": fred_key, "file_type": "json", "limit": 1, "sort_order": "desc"},
                        timeout=8,
                    )
                    if r.ok:
                        obs = (r.json().get("observations") or [{}])[0]
                        events.append(
                            {
                                "event": name,
                                "time": str(obs.get("date") or ""),
                                "value": str(obs.get("value") or ""),
                                "source": "fred",
                                "series_id": series_id,
                            }
                        )
                except Exception:
                    continue
            if events:
                _MACRO_CACHE.set("macro", events[:10])
                log_event("macro_calendar_fred_ok", count=len(events))
                return events[:10]
    except Exception as e:
        log_event("macro_calendar_fred_error", level="warning", error=str(e)[:200])

    # 3) Zero-key fallback: FRED CSV (no API key, public) for CPI level — 15s timeout (FRED can be slow)
    try:
        import os
        import requests

        from backend.core.config import get_settings

        _s = get_settings()
        csv_base = getattr(_s, "fred_csv_url", None) or os.getenv("FRED_CSV_URL") or "https://fred.stlouisfed.org/graph/fredgraph.csv"
        r = requests.get(csv_base, params={"id": "CPIAUCSL"}, timeout=15, headers={"User-Agent": "meanrev/1.0"})
        if r.ok and "DATE" in r.text:
            lines = r.text.strip().splitlines()
            if len(lines) >= 2:
                last = lines[-1].split(",")
                if len(last) >= 2 and last[1] not in (".", ""):
                    events.append({"event": "CPI", "time": last[0], "value": last[1], "source": "fred_csv"})
                    _MACRO_CACHE.set("macro", events)
                    log_event("macro_calendar_fred_csv_ok", count=len(events))
                    return events
    except Exception as e:
        log_event("macro_calendar_fred_csv_error", level="warning", error=str(e)[:200])

    # No free source configured or all failed — return empty with log (research handles as "No data available")
    log_event("macro_calendar_no_data", reason="No data available for macro calendar (set FRED_API_KEY or FINNHUB_API_KEY in .env for free calendar; zero-key CSV also tried)")
    _MACRO_CACHE.set("macro", [])
    return []


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
