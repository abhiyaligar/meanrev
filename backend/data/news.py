import os
import re
import requests
import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from backend.core.logging import log_event
from backend.core.utils import TTLCache
from backend.core.config import get_settings

# Library-backed TTL cache via core/utils (cachetools)
_NEWS_CACHE = TTLCache(maxsize=200, ttl=300)
_MACRO_CACHE = TTLCache(maxsize=10, ttl=300)  # keyed by days_ahead
CACHE_TTL = 300  # kept for compatibility, actual TTL lives in TTLCache

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "is",
    "are", "was", "were", "will", "be", "as", "at", "by", "with", "from",
    "after", "amid", "over", "into", "its", "it", "this", "that", "than",
    "ahead", "could", "market", "fed",
}

# --- Sentiment backend -------------------------------------------------
#
# Primary: Loughran-McDonald finance lexicon via `pysentiment2` — a word
# list built from actual financial filings, so finance-neutral words like
# "tax", "cost", "liability" aren't misread as negative the way they would
# be with a general-purpose lexicon. No model download, still fast (word
# lookup, not inference).
#
# Fallback: small hand-rolled keyword lexicon, used only if pysentiment2
# isn't installed, so this module keeps working without the dependency.

_LM = None
_LM_LOAD_FAILED = False

_FALLBACK_POSITIVE = {
    "beat", "beats", "surge", "surged", "rally", "gains", "gain", "up",
    "rise", "rises", "record", "high", "growth", "strong", "bullish",
    "buy", "upgrade", "optimism",
}
_FALLBACK_NEGATIVE = {
    "miss", "misses", "plunge", "drop", "drops", "fall", "falls", "down",
    "decline", "weak", "bearish", "sell", "downgrade", "cut", "loss",
    "losses", "fear", "recession",
}


def _get_lm():
    """Lazily load the Loughran-McDonald lexicon. Cached after first call."""
    global _LM, _LM_LOAD_FAILED
    if _LM is not None or _LM_LOAD_FAILED:
        return _LM
    try:
        import pysentiment2 as ps  # type: ignore

        _LM = ps.LM()
        log_event("sentiment_backend_ready", backend="loughran_mcdonald")
    except Exception as e:
        _LM_LOAD_FAILED = True
        log_event("sentiment_backend_fallback", level="warning", reason=str(e)[:200])
    return _LM


def _sentiment_score_fallback(text: str) -> float:
    """General-purpose keyword lexicon. Used only if pysentiment2 is unavailable."""
    words = re.findall(r"\w+", text.lower())
    if not words:
        return 0.0
    score = sum(
        1 if w in _FALLBACK_POSITIVE else -1 if w in _FALLBACK_NEGATIVE else 0
        for w in words
    )
    return max(-1.0, min(1.0, score / max(5, len(words) * 0.3)))


def _sentiment_score(text: str) -> float:
    """
    Headline sentiment in [-1, 1], using the Loughran-McDonald finance
    lexicon when available (polarity = (pos - neg) / (pos + neg) over
    LM-tagged words), falling back to a generic keyword lexicon otherwise.
    """
    if not text:
        return 0.0

    lm = _get_lm()
    if lm is not None:
        try:
            tokens = lm.tokenize(text)
            if not tokens:
                return 0.0
            score = lm.get_score(tokens)
            polarity = score.get("Polarity", 0.0)
            return max(-1.0, min(1.0, float(polarity)))
        except Exception as e:
            log_event("sentiment_score_lm_error", level="warning", error=str(e)[:200])
            # fall through to fallback lexicon for this call

    return _sentiment_score_fallback(text)


def _sentiment_label(score: float) -> str:
    if score > 0.2:
        return "bullish"
    if score < -0.2:
        return "bearish"
    return "neutral"


# --- News fetch ----------------------------------------------------------

def _normalize_symbols(symbols: Optional[List[str]]) -> Optional[List[str]]:
    if not symbols:
        return None
    return sorted(s.upper() for s in symbols)


def _fetch_from_alpaca(symbols: Optional[List[str]], limit: int) -> List[Dict[str, str]]:
    """Attempt to fetch headlines from Alpaca's NewsClient. Returns [] on any failure."""
    settings = get_settings()
    key = settings.get_key()
    secret = settings.get_secret()
    if not (key and secret):
        return []

    from alpaca.data.historical import NewsClient  # type: ignore
    from alpaca.data.requests import NewsRequest  # type: ignore

    client = NewsClient(api_key=key, secret_key=secret)
    try:
        req = NewsRequest(symbols=",".join(symbols), limit=limit) if symbols else NewsRequest(limit=limit)  # type: ignore
    except Exception:
        req = NewsRequest(symbols=symbols or None, limit=limit)  # type: ignore

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(client.get_news, req)
        resp = future.result(timeout=15)

    news_list = getattr(resp, "news", None)
    if news_list is None:
        news_list = list(getattr(resp, "data", {}).values())
    elif isinstance(news_list, dict):
        news_list = list(news_list.values())

    sym_key = ",".join(symbols) if symbols else "general"
    headlines: List[Dict[str, str]] = []
    for n in list(news_list)[:limit]:
        if hasattr(n, "model_dump"):
            d = n.model_dump()
        elif hasattr(n, "dict"):
            d = n.dict()
        elif isinstance(n, dict):
            d = n
        else:
            d = {"headline": str(n)}

        headline = str(d.get("headline") or d.get("title") or d.get("summary") or "")[:300]
        raw_symbols = d.get("symbols") or d.get("symbol")
        if isinstance(raw_symbols, (list, tuple)):
            symbol_display = ",".join(str(x) for x in raw_symbols) or sym_key
        else:
            symbol_display = str(raw_symbols) if raw_symbols else sym_key

        score = _sentiment_score(headline)
        headlines.append(
            {
                "headline": headline,
                "symbol": symbol_display,
                "timestamp": str(d.get("created_at") or d.get("updated_at") or datetime.now(timezone.utc).isoformat()),
                "source": str(d.get("source") or "alpaca"),
                "sentiment_score": score,
                "sentiment": _sentiment_label(score),
            }
        )
    print(f"From The Alpaca: {headlines}")
    return headlines


def fetch_news(symbols: Optional[List[str]] = None, limit: int = 20) -> List[Dict[str, str]]:
    """
    Fetch news headlines from Alpaca's NewsClient, if configured.

    Returns a list of dicts: {headline, symbol, timestamp, source, sentiment, sentiment_score}.
    Returns [] (with a logged reason) if no data source is configured or none returns data —
    callers should render this as "No data available for this".
    """
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 20
    lim = max(1, min(lim, 100))

    norm_symbols = _normalize_symbols(symbols)
    sym_key = ",".join(norm_symbols) if norm_symbols else "general"
    cache_key = f"{sym_key}:{lim}"

    cached = _NEWS_CACHE.get(cache_key)
    if cached is not None:
        return cached[:lim]

    headlines: List[Dict[str, str]] = []
    try:
        headlines = _fetch_from_alpaca(norm_symbols, lim)
        if headlines:
            log_event("news_fetch_alpaca_ok", symbols=sym_key, count=len(headlines))
    except Exception as e:
        log_event("news_fetch_alpaca_error", level="warning", error=str(e)[:200], symbols=sym_key)

    if not headlines:
        log_event(
            "news_no_data",
            symbols=sym_key,
            reason="No data available for this symbols/timeframe (Alpaca returned 0 headlines or is unconfigured)",
        )
        _NEWS_CACHE.set(cache_key, [])
        return []

    _NEWS_CACHE.set(cache_key, headlines)
    return headlines[:lim]


# --- Macro calendar --------------------------------------------------------
#
# get_macro_calendar returns a dict keyed by source, so callers always know
# where each event came from instead of a flat list with a buried "source"
# field:
#
#   {
#       "finnhub": [ {event, time, country, actual, estimate, prev, source}, ... ],
#       "fred":    [ {event, time, value, series_id, source}, ... ],
#   }
#
# - "finnhub": forward-looking US calendar entries in [now, now + days_ahead].
# - "fred":    latest confirmed print for CPI/NFP/Unemployment/Fed Funds
#              (not forward-looking — FRED has no release calendar, only
#              realized values). Included as context even when Finnhub's
#              own actual/estimate/prev fields are populated.
#
# Either list may be empty (e.g. no events due in the window, or that
# source isn't configured / failed) — an empty list is a valid, meaningful
# result and is not treated as an error.

def _parse_event_time(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _fetch_macro_finnhub(days_ahead: int) -> List[Dict[str, str]]:
    """US economic calendar entries scheduled within [now, now + days_ahead]."""
    settings = get_settings()
    finnhub_key = os.getenv("FINNHUB_API_KEY") or getattr(settings, "finnhub_api_key", None)
    if not finnhub_key:
        return []

    resp = requests.get(
        "https://finnhub.io/api/v1/calendar/economic",
        params={"token": finnhub_key},
        timeout=8,
    )
    if not resp.ok:
        return []

    raw_events = resp.json().get("economicCalendar") or resp.json().get("economic_calendar") or []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    events: List[Dict[str, str]] = []
    for e in raw_events:
        country = str(e.get("country", "")).upper()
        if country not in ("US", "USA"):
            continue

        dt = _parse_event_time(e.get("time") or e.get("date") or "")
        # Drop events with an unparseable time, and events outside [now, cutoff]
        if dt is None or dt < now or dt > cutoff:
            continue

        events.append(
            {
                "event": str(e.get("event") or e.get("name") or "Macro"),
                "time": str(e.get("time") or e.get("date") or ""),
                "country": "US",
                "actual": str(e.get("actual") or ""),
                "estimate": str(e.get("estimate") or ""),
                "prev": str(e.get("prev") or ""),
                "source": "finnhub",
            }
        )
    return events[:10]


def _fetch_macro_fred() -> List[Dict[str, str]]:
    """Latest confirmed print for a handful of key US series (not forward-looking)."""
    settings = get_settings()
    fred_key = os.getenv("FRED_API_KEY") or getattr(settings, "fred_api_key", None)
    if not fred_key:
        return []

    base = str(getattr(settings, "fred_api_url", None) or os.getenv("FRED_API_URL") or "https://api.stlouisfed.org/fred").rstrip("/")
    series = [("CPIAUCSL", "CPI"), ("PAYEMS", "NFP"), ("UNRATE", "Unemployment"), ("FEDFUNDS", "Fed Funds")]

    events: List[Dict[str, str]] = []
    for series_id, name in series:
        try:
            resp = requests.get(
                f"{base}/series/observations",
                params={"series_id": series_id, "api_key": fred_key, "file_type": "json", "limit": 1, "sort_order": "desc"},
                timeout=8,
            )
            if not resp.ok:
                continue
            obs = (resp.json().get("observations") or [{}])[0]
            events.append(
                {
                    "event": name,
                    "time": str(obs.get("date") or ""),
                    "value": str(obs.get("value") or ""),
                    "series_id": series_id,
                    "source": "fred",
                }
            )
        except Exception:
            continue
    print(f"From The Fred: {events}")
    return events


def get_macro_calendar(days_ahead: int = 7) -> Dict[str, List[Dict[str, str]]]:
    """
    Return upcoming/latest US macro catalysts, grouped by source:

        {"finnhub": [...forward-looking calendar events...],
         "fred":    [...latest confirmed CPI/NFP/Unemployment/Fed Funds prints...]}

    Each source is fetched independently — a failure or empty result in one
    doesn't affect the other, and both keys are always present. Callers
    that just want "is there anything at all" can check whether either
    list is non-empty; callers that care about provenance already have it
    via the top-level key (and the per-event "source" field, kept for
    convenience when events are flattened downstream).
    """
    try:
        days = max(0, int(days_ahead))
    except (TypeError, ValueError):
        days = 7

    cache_key = f"macro:{days}"
    cached = _MACRO_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result: Dict[str, List[Dict[str, str]]] = {"finnhub": [], "fred": []}

    try:
        result["finnhub"] = _fetch_macro_finnhub(days)
        log_event("macro_calendar_finnhub_ok", count=len(result["finnhub"]))
    except Exception as e:
        log_event("macro_calendar_finnhub_error", level="warning", error=str(e)[:200])

    try:
        result["fred"] = _fetch_macro_fred()
        log_event("macro_calendar_fred_ok", count=len(result["fred"]))
    except Exception as e:
        log_event("macro_calendar_fred_error", level="warning", error=str(e)[:200])

    if not result["finnhub"] and not result["fred"]:
        log_event(
            "macro_calendar_no_data",
            reason="No data available for macro calendar (set FRED_API_KEY and/or FINNHUB_API_KEY in .env)",
        )

    _MACRO_CACHE.set(cache_key, result)
    print(f"From The Macro Calendar: {result}")
    return result


def extract_keywords(headlines: List[Dict[str, str]], top_k: int = 10) -> List[str]:
    """
    Simple keyword extraction — top frequent non-stopwords across headlines.
    Used for Research agent social-velocity / keyword signals.
    """
    freq: Dict[str, int] = {}
    for h in headlines:
        for w in re.findall(r"\w+", h.get("headline", "").lower()):
            if len(w) < 3 or w in _STOPWORDS:
                continue
            freq[w] = freq.get(w, 0) + 1

    return [w for w, _ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:top_k]]

    