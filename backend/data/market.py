"""
Market data — Phase 4.1 + 4.2

- OHLCV fetch via Alpaca StockHistoricalDataClient (free-tier) with throttling + cache
- VWAP at 1m/5m/1h/1d + indicators: RSI, MACD, EMA 20/50/200, Bollinger, ATR (pandas-ta)
- Time-aligned DataFrame output for research/strategy consumption
- Never logs secrets; respects 25/min bucket via broker/rate_limit
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

try:
    import pandas_ta as ta  # type: ignore
except Exception:
    ta = None  # fallback to manual

from backend.broker.rate_limit import bucket, RateLimitExceeded
from backend.core.config import get_settings
from backend.core.logging import log_event

# Simple in-memory cache with TTL (seconds) to respect free-tier limits
_CACHE: Dict[str, tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 60  # 1 min for intraday, 5 min for daily handled below
DAILY_TTL = 300


def _cache_key(symbol: str, timeframe: str, limit: int) -> str:
    return f"{symbol.upper()}:{timeframe}:{limit}"


def _get_cached(key: str) -> Optional[pd.DataFrame]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, df = entry
    ttl = DAILY_TTL if "Day" in key else CACHE_TTL
    if time.time() - ts > ttl:
        _CACHE.pop(key, None)
        return None
    return df.copy()


def _set_cached(key: str, df: pd.DataFrame) -> None:
    _CACHE[key] = (time.time(), df.copy())
    # Cap cache size
    if len(_CACHE) > 200:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)


def _get_data_client():
    """Create StockHistoricalDataClient from paper creds, or None if not configured."""
    s = get_settings()
    key = s.get_key()
    secret = s.get_secret()
    if not key or not secret:
        log_event("market_data_client_missing", level="warning", reason="no ALPACA_API_KEY")
        return None
    try:
        from alpaca.data.historical import StockHistoricalDataClient

        return StockHistoricalDataClient(api_key=key, secret_key=secret)
    except Exception as e:
        log_event("market_data_client_error", level="warning", error=str(e))
        return None


def _timeframe_to_alpaca(timeframe: str):
    """Map string timeframe to alpaca.data.timeframe.TimeFrame."""
    try:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        mapping = {
            "1Min": TimeFrame(1, TimeFrameUnit.Minute),
            "5Min": TimeFrame(5, TimeFrameUnit.Minute),
            "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
            "1Day": TimeFrame(1, TimeFrameUnit.Day),
            "1m": TimeFrame(1, TimeFrameUnit.Minute),
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "1h": TimeFrame(1, TimeFrameUnit.Hour),
            "1d": TimeFrame(1, TimeFrameUnit.Day),
        }
        return mapping.get(timeframe, TimeFrame(1, TimeFrameUnit.Day))
    except Exception:
        return None


def _compute_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """VWAP = cumulative (typical_price * volume) / cumulative volume."""
    if "vwap" in df.columns:
        return df
    if not {"high", "low", "close", "volume"}.issubset(df.columns):
        return df
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    # Protect against zero volume
    df["vwap"] = (typical * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)
    return df


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add RSI, MACD, EMA 20/50/200, Bollinger, ATR.
    Uses pandas_ta when available, else manual EMA fallback.
    """
    if df.empty or "close" not in df.columns:
        return df

    # Ensure numeric
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if ta is not None:
        try:
            # RSI 14
            df["rsi"] = ta.rsi(df["close"], length=14)
            # MACD (12,26,9) -> macd, signal, histogram
            macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd is not None and not macd.empty:
                # pandas_ta names columns like MACD_12_26_9, MACDs_12_26_9, MACDh_12_26_9
                for c in macd.columns:
                    if c.startswith("MACD_"):
                        df["macd"] = macd[c]
                    elif c.startswith("MACDs"):
                        df["macd_signal"] = macd[c]
                    elif c.startswith("MACDh"):
                        df["macd_hist"] = macd[c]
            # EMA 20/50/200
            df["ema_20"] = ta.ema(df["close"], length=20)
            df["ema_50"] = ta.ema(df["close"], length=50)
            df["ema_200"] = ta.ema(df["close"], length=200)
            # Bollinger 20,2
            bb = ta.bbands(df["close"], length=20, std=2)
            if bb is not None and not bb.empty:
                for c in bb.columns:
                    if c.startswith("BBL"):
                        df["bb_lower"] = bb[c]
                    elif c.startswith("BBM"):
                        df["bb_middle"] = bb[c]
                    elif c.startswith("BBU"):
                        df["bb_upper"] = bb[c]
            # ATR 14
            if {"high", "low", "close"}.issubset(df.columns):
                df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
        except Exception as e:
            log_event("indicator_pandas_ta_error", level="warning", error=str(e))

    # Manual fallback for EMAs if ta missing or failed
    for length, col in [(20, "ema_20"), (50, "ema_50"), (200, "ema_200")]:
        if col not in df.columns or df[col].isna().all():
            df[col] = df["close"].ewm(span=length, adjust=False, min_periods=1).mean()

    # Fallback RSI if missing
    if "rsi" not in df.columns or df["rsi"].isna().all():
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1)
        df["rsi"] = 100 - (100 / (1 + rs))

    return df


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1Day",
    limit: int = 100,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV bars for symbol at timeframe, with cache and throttling.
    Returns DataFrame indexed by timestamp with columns open/high/low/close/volume/vwap
    plus indicators (rsi, macd, ema_*, bb_*, atr) when available.

    Rate-limited via shared 25/min bucket; cached for 60s (intraday) / 300s (daily).
    On failure, returns empty DataFrame (caller handles fallback).
    """
    if not symbol or not symbol.strip():
        return pd.DataFrame()
    sym = symbol.strip().upper()
    # Explicit guard per VULN 7 pattern
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 100
    if lim < 1:
        lim = 1
    lim = min(lim, 1000)

    key = _cache_key(sym, timeframe, lim)
    cached = _get_cached(key)
    if cached is not None:
        return cached

    # Throttle
    try:
        bucket.consume(1)
    except RateLimitExceeded as e:
        log_event("market_data_rate_limited", level="warning", symbol=sym, timeframe=timeframe, retry_after=e.retry_after)
        # Return cached stale if available, else empty
        return cached if cached is not None else pd.DataFrame()

    client = _get_data_client()
    if client is None:
        return pd.DataFrame()

    try:
        from alpaca.data.requests import StockBarsRequest

        tf = _timeframe_to_alpaca(timeframe)
        if tf is None:
            log_event("market_data_timeframe_error", level="warning", timeframe=timeframe)
            return pd.DataFrame()

        # Build request — start defaults to limit* timeframe ago
        if start is None and end is None:
            # Let Alpaca default to recent bars via limit
            req = StockBarsRequest(symbol_or_symbols=sym, timeframe=tf, limit=lim)
        else:
            req_kwargs: Dict[str, object] = {"symbol_or_symbols": sym, "timeframe": tf, "limit": lim}
            if start:
                req_kwargs["start"] = start
            if end:
                req_kwargs["end"] = end
            req = StockBarsRequest(**req_kwargs)  # type: ignore

        # Timeout 30s per VULN 3
        import concurrent.futures

        def _do_fetch():
            return client.get_stock_bars(req)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_do_fetch)
            resp = future.result(timeout=30)

        # Normalize response — alpaca-py returns BarSet
        # Try common shapes: resp.df, resp.data, dict
        df: Optional[pd.DataFrame] = None
        if hasattr(resp, "df") and isinstance(resp.df, pd.DataFrame):
            # Multi-symbol df has symbol level
            try:
                df = resp.df.xs(sym, level=0) if "symbol" in str(resp.df.index.names) else resp.df
            except Exception:
                df = resp.df
        elif hasattr(resp, "data") and isinstance(resp.data, dict):
            bars = resp.data.get(sym, [])
            if bars:
                # bars are objects with .model_dump or dict
                rows = []
                for b in bars:
                    if hasattr(b, "model_dump"):
                        rows.append(b.model_dump())
                    elif hasattr(b, "dict"):
                        rows.append(b.dict())
                    elif isinstance(b, dict):
                        rows.append(b)
                    else:
                        rows.append({"close": getattr(b, "close", None)})
                df = pd.DataFrame(rows)
                if "timestamp" in df.columns:
                    df = df.set_index("timestamp")
        if df is None or df.empty:
            df = pd.DataFrame()

        # Standardize columns
        df.columns = [c.lower() for c in df.columns]
        # Map alpaca names: open, high, low, close, volume, vwap, trade_count
        # Ensure required
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df[col] = pd.NA

        # VWAP
        df = _compute_vwap(df)
        # Indicators
        df = _add_indicators(df)

        # Sort by index (timestamp)
        try:
            df = df.sort_index()
        except Exception:
            pass

        _set_cached(key, df)
        log_event("market_data_fetch_ok", symbol=sym, timeframe=timeframe, rows=len(df))
        return df

    except Exception as e:
        # Retryable check via broker rate_limit helper
        from backend.broker.rate_limit import is_retryable_exception

        if is_retryable_exception(e):
            log_event("market_data_retryable", level="warning", symbol=sym, error=str(e)[:200])
        else:
            log_event("market_data_error", level="warning", symbol=sym, error=str(e)[:200])
        return pd.DataFrame()


def get_market_snapshot(symbol: str, timeframes: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
    """
    Convenience — fetch OHLCV at multiple timeframes for a symbol.
    Default: 1Day (100 bars) + 1Hour (100 bars) for strategy/research.
    Returns dict timeframe -> DataFrame.
    """
    if timeframes is None:
        timeframes = ["1Day", "1Hour"]
    out: Dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        out[tf] = fetch_ohlcv(symbol, timeframe=tf, limit=100)
    return out


# --- Phase 4b: Options chain, Greeks, and time-aligned normalization ---

import math


def _black_scholes_greeks(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float = 0.3,
    risk_free_rate: float = 0.02,
    option_type: str = "call",
) -> Dict[str, float]:
    """
    Indicative Black-Scholes Greeks for hackathon (not trading-grade).
    Returns delta, gamma, theta, vega, rho. Time in years, vol annualized.
    """
    if spot <= 0 or strike <= 0 or time_to_expiry_years <= 0 or volatility <= 0:
        return {"delta": 0.5 if option_type == "call" else -0.5, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    try:
        from math import exp, log, sqrt

        # Use scipy.stats.norm when available (library-backed, more accurate), else math.erf fallback
        try:
            from scipy.stats import norm  # type: ignore

            def _norm_cdf(x):
                return float(norm.cdf(x))

            def _norm_pdf(x):
                return float(norm.pdf(x))

        except ImportError:
            from math import erf

            def _norm_cdf(x):
                return 0.5 * (1 + erf(x / sqrt(2)))

            def _norm_pdf(x):
                return exp(-0.5 * x * x) / sqrt(2 * math.pi)

        d1 = (log(spot / strike) + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry_years) / (volatility * sqrt(time_to_expiry_years))
        d2 = d1 - volatility * sqrt(time_to_expiry_years)

        N_d1 = _norm_cdf(d1)
        N_d2 = _norm_cdf(d2)
        n_d1 = _norm_pdf(d1)

        if option_type.lower() == "call":
            delta = N_d1
            theta = (-spot * n_d1 * volatility / (2 * sqrt(time_to_expiry_years)) - risk_free_rate * strike * exp(-risk_free_rate * time_to_expiry_years) * N_d2) / 365
            rho = strike * time_to_expiry_years * exp(-risk_free_rate * time_to_expiry_years) * N_d2 / 100
        else:
            delta = N_d1 - 1
            theta = (-spot * n_d1 * volatility / (2 * sqrt(time_to_expiry_years)) + risk_free_rate * strike * exp(-risk_free_rate * time_to_expiry_years) * (1 - N_d2)) / 365
            rho = -strike * time_to_expiry_years * exp(-risk_free_rate * time_to_expiry_years) * (1 - N_d2) / 100

        gamma = n_d1 / (spot * volatility * sqrt(time_to_expiry_years))
        vega = spot * n_d1 * sqrt(time_to_expiry_years) / 100

        return {
            "delta": round(float(delta), 4),
            "gamma": round(float(gamma), 4),
            "theta": round(float(theta), 4),
            "vega": round(float(vega), 4),
            "rho": round(float(rho), 4),
        }
    except Exception:
        return {"delta": 0.5, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}


def fetch_option_chain(
    underlying: str,
    expiration: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, object]]:
    """
    Fetch options chain for underlying. Tries Alpaca OptionHistoricalDataClient,
    falls back to deterministic mock with indicative Greeks so every strategy
    can include options (hackathon requirement).

    Returns list of {symbol, underlying, strike, expiration, type, last_price, greeks}
    """
    if not underlying or not underlying.strip():
        return []
    sym = underlying.strip().upper()
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 20
    if lim < 1:
        lim = 1
    lim = min(lim, 100)

    # Try Alpaca options client
    try:
        from backend.core.config import get_settings

        s = get_settings()
        key = s.get_key()
        secret = s.get_secret()
        if key and secret:
            # Attempt options client — package path varies by alpaca-py version
            OptionClient = None
            try:
                from alpaca.data.historical.option import OptionHistoricalDataClient  # type: ignore

                OptionClient = OptionHistoricalDataClient
            except Exception:
                try:
                    from alpaca.data.historical import OptionHistoricalDataClient  # type: ignore

                    OptionClient = OptionHistoricalDataClient
                except Exception:
                    OptionClient = None

            if OptionClient is not None:
                # Throttle shared bucket
                try:
                    bucket.consume(1)
                except RateLimitExceeded:
                    pass  # return mock below
                else:
                    client = OptionClient(api_key=key, secret_key=secret)  # type: ignore
                    # For hackathon, use mock shape if data not available; real fetch would be:
                    # from alpaca.data.requests import OptionChainRequest, OptionSnapshotRequest
                    # Real call omitted to avoid free-tier OPRA requirement — mock provides indicative data
                    raise NotImplementedError("OPRA options data requires subscription — using mock Greeks")
    except Exception as e:
        # Log once, then fall through to mock
        if "OPRA" not in str(e):
            log_event("option_chain_alpaca_error", level="warning", underlying=sym, error=str(e)[:200])

    # Deterministic mock chain — spot proxied via last close or 150 fallback
    spot = 150.0
    try:
        df = fetch_ohlcv(sym, timeframe="1Day", limit=1)
        if not df.empty and "close" in df.columns and not pd.isna(df["close"].iloc[-1]):
            spot = float(df["close"].iloc[-1])
    except Exception:
        pass

    # Generate strikes around spot, 30 days expiry if not provided
    if expiration is None:
        exp_date = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    else:
        exp_date = expiration
    try:
        exp_dt = datetime.fromisoformat(exp_date).replace(tzinfo=timezone.utc)
        tte = max(1 / 365, (exp_dt - datetime.now(timezone.utc)).total_seconds() / (365 * 24 * 3600))
    except Exception:
        tte = 30 / 365
        exp_date = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()

    strikes = [round(spot * (0.9 + 0.05 * i), 2) for i in range(5)]  # 90%..110%
    chain: List[Dict[str, object]] = []
    for strike in strikes[: lim // 2 + 1]:
        for opt_type in ("call", "put"):
            # Mock last price via intrinsic + time value
            intrinsic = max(0, spot - strike) if opt_type == "call" else max(0, strike - spot)
            time_val = spot * 0.02 * math.sqrt(tte)
            last_price = round(intrinsic + time_val, 2)
            greeks = _black_scholes_greeks(spot, strike, tte, volatility=0.3, option_type=opt_type)
            chain.append(
                {
                    "symbol": f"{sym}{exp_date.replace('-','')}{opt_type[0].upper()}{strike}",
                    "underlying": sym,
                    "strike": strike,
                    "expiration": exp_date,
                    "type": opt_type,
                    "last_price": last_price,
                    "spot": spot,
                    "greeks": greeks,
                    "source": "mock-indicative",
                }
            )
            if len(chain) >= lim:
                break
        if len(chain) >= lim:
            break

    log_event("option_chain_mock", underlying=sym, expiration=exp_date, count=len(chain), spot=spot)
    return chain[:lim]


def align_timeframes(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Time-aligned feature normalization — single timestamp index across all timeframes.
    Merges 1m/5m/1h/1d frames via asof join onto the finest timeframe's index.
    Returns DataFrame with prefixed columns (e.g., close_1Day, rsi_1Hour).
    """
    if not frames:
        return pd.DataFrame()
    # Find finest timeframe (smallest period) — prefer 1m > 5m > 1h > 1d
    order = {"1Min": 0, "1m": 0, "5Min": 1, "5m": 1, "1Hour": 2, "1h": 2, "1Day": 3, "1d": 3}
    sorted_tfs = sorted(frames.keys(), key=lambda k: order.get(k, 99))
    base_tf = sorted_tfs[0]
    base_df = frames[base_tf]
    if base_df.empty:
        # Fallback to first non-empty
        for tf in sorted_tfs:
            if not frames[tf].empty:
                base_df = frames[tf]
                base_tf = tf
                break
    if base_df.empty:
        return pd.DataFrame()

    aligned = base_df.copy()
    # Prefix base columns
    aligned.columns = [f"{c}_{base_tf}" for c in aligned.columns]

    for tf, df in frames.items():
        if tf == base_tf or df.empty:
            continue
        # As-of merge: for each base timestamp, take last known value from coarser tf
        df_sorted = df.sort_index()
        df_prefixed = df_sorted.copy()
        df_prefixed.columns = [f"{c}_{tf}" for c in df_prefixed.columns]
        # Use merge_asof (requires sorted)
        try:
            aligned = pd.merge_asof(
                aligned.sort_index(),
                df_prefixed.sort_index(),
                left_index=True,
                right_index=True,
                direction="backward",
                suffixes=("", f"_{tf}"),
            )
        except Exception:
            # Fallback to reindex ffill
            df_reindexed = df_prefixed.reindex(aligned.index, method="ffill")
            for c in df_reindexed.columns:
                aligned[c] = df_reindexed[c]

    # Forward-fill remaining NaNs for continuity
    aligned = aligned.ffill().bfill()
    log_event("timeframes_aligned", base=base_tf, frames=list(frames.keys()), rows=len(aligned), cols=len(aligned.columns))
    return aligned

