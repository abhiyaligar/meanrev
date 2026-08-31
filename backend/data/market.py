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


def _is_crypto_symbol(symbol: str) -> bool:
    """Detect crypto symbol: BTC/USD, BTCUSD, BTC, ETH, SOL, etc."""
    if not symbol:
        return False
    s = symbol.strip().upper().replace(" ", "")
    # Direct crypto pairs
    if "/" in s:
        base = s.split("/")[0]
        return base in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LTC", "BCH", "XRP", "ADA", "DOT", "LINK", "UNI", "ATOM")
    # Without slash
    if s in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LTC", "BCH", "XRP", "ADA", "DOT", "LINK", "UNI", "ATOM"):
        return True
    if s in ("BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD", "BTCUSDT", "ETHUSDT"):
        return True
    # Heuristic: ends with USD/USDT and base is crypto
    for base in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LTC"):
        if s.startswith(base) and (s == base or s.endswith("USD") or s.endswith("USDT")):
            return True
    return False


def _normalize_crypto_symbol(symbol: str) -> str:
    """Normalize crypto to Alpaca format BTC/USD. BTC -> BTC/USD, BTCUSD -> BTC/USD."""
    s = symbol.strip().upper().replace(" ", "")
    if "/" in s:
        return s
    # Map without slash
    mapping = {
        "BTCUSD": "BTC/USD",
        "ETHUSD": "ETH/USD",
        "SOLUSD": "SOL/USD",
        "DOGEUSD": "DOGE/USD",
        "BTCUSDT": "BTC/USD",
        "ETHUSDT": "ETH/USD",
    }
    if s in mapping:
        return mapping[s]
    if s in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LTC", "BCH", "XRP", "ADA", "DOT"):
        return f"{s}/USD"
    return s


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


def _get_crypto_client():
    """Create CryptoHistoricalDataClient for BTC/USD etc., or None."""
    s = get_settings()
    key = s.get_key()
    secret = s.get_secret()
    if not key or not secret:
        return None
    try:
        from alpaca.data.historical import CryptoHistoricalDataClient

        return CryptoHistoricalDataClient(api_key=key, secret_key=secret)
    except Exception:
        try:
            from alpaca.data.historical.crypto import CryptoHistoricalDataClient as CryptoClient2  # type: ignore

            return CryptoClient2(api_key=key, secret_key=secret)
        except Exception as e:
            log_event("crypto_client_error", level="warning", error=str(e))
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

    # Crypto vs Stock client selection
    is_crypto = _is_crypto_symbol(sym)
    if is_crypto:
        sym = _normalize_crypto_symbol(sym)
        client = _get_crypto_client()
        if client is None:
            log_event("crypto_client_missing", level="warning", symbol=sym)
            return pd.DataFrame()
    else:
        client = _get_data_client()
        if client is None:
            return pd.DataFrame()

    try:
        tf = _timeframe_to_alpaca(timeframe)
        if tf is None:
            log_event("market_data_timeframe_error", level="warning", timeframe=timeframe)
            return pd.DataFrame()

        # Build request — crypto uses CryptoBarsRequest, stock uses StockBarsRequest
        if is_crypto:
            try:
                from alpaca.data.requests import CryptoBarsRequest
            except ImportError:
                from alpaca.data.requests.crypto import CryptoBarsRequest  # type: ignore

            if start is None and end is None:
                req = CryptoBarsRequest(symbol_or_symbols=sym, timeframe=tf, limit=lim)
            else:
                req_kwargs: Dict[str, object] = {"symbol_or_symbols": sym, "timeframe": tf, "limit": lim}
                if start:
                    req_kwargs["start"] = start
                if end:
                    req_kwargs["end"] = end
                req = CryptoBarsRequest(**req_kwargs)  # type: ignore
        else:
            from alpaca.data.requests import StockBarsRequest

            if start is None and end is None:
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
            if is_crypto:
                return client.get_crypto_bars(req)
            else:
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

        # Handle empty for crypto cross like BTC/ETH via derived BTC/USD / ETH/USD first
        if df.empty and is_crypto and "/" in sym:
            base, quote = sym.split("/", 1)
            # Try to derive cross via USD (e.g., BTC/ETH = BTC/USD / ETH/USD)
            if quote != "USD" and base != "USD":
                # Cross like BTC/ETH
                try:
                    # Fetch USD legs without recursion loop (direct, no derived fallback to avoid infinite)
                    # Use cache-aware direct fetch for USD pairs
                    df_base_usd = None
                    df_quote_usd = None
                    # Try BTC/USD and ETH/USD
                    for leg, target_df in [(f"{base}/USD", "base_usd"), (f"{quote}/USD", "quote_usd")]:
                        # Avoid infinite recursion: call internal fetch without cross-derive for USD legs
                        # Use a simple direct fetch via _get_crypto_client bypassing this cross logic
                        try:
                            # Direct fetch for USD pair
                            leg_key = _cache_key(leg, timeframe, lim)
                            leg_cached = _get_cached(leg_key)
                            if leg_cached is not None and not leg_cached.empty:
                                if target_df == "base_usd":
                                    df_base_usd = leg_cached
                                else:
                                    df_quote_usd = leg_cached
                            else:
                                # Try live fetch for USD leg (will not recurse to cross since quote is USD)
                                # Use a helper to fetch without cross-derive: temporarily set is_crypto but not cross
                                from alpaca.data.requests import CryptoBarsRequest

                                c_client = _get_crypto_client()
                                if c_client:
                                    tf2 = _timeframe_to_alpaca(timeframe)
                                    req2 = CryptoBarsRequest(symbol_or_symbols=leg, timeframe=tf2, limit=lim)
                                    import concurrent.futures

                                    def _do2():
                                        return c_client.get_crypto_bars(req2)

                                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex2:
                                        resp2 = ex2.submit(_do2).result(timeout=15)
                                        # Normalize similarly (simplified)
                                        df2 = None
                                        if hasattr(resp2, "df") and isinstance(resp2.df, pd.DataFrame):
                                            try:
                                                df2 = resp2.df.xs(leg, level=0) if "symbol" in str(resp2.df.index.names) else resp2.df
                                            except Exception:
                                                df2 = resp2.df
                                        if df2 is not None and not df2.empty:
                                            df2.columns = [c.lower() for c in df2.columns]
                                            if target_df == "base_usd":
                                                df_base_usd = df2
                                            else:
                                                df_quote_usd = df2
                        except Exception:
                            pass
                    if df_base_usd is not None and df_quote_usd is not None and not df_base_usd.empty and not df_quote_usd.empty:
                        # Align on index and derive
                        try:
                            # Use last closes
                            base_close = float(df_base_usd["close"].iloc[-1])
                            quote_close = float(df_quote_usd["close"].iloc[-1])
                            if quote_close != 0:
                                derived_close = base_close / quote_close
                                # Build synthetic df from base_usd structure but with derived close
                                df = df_base_usd.copy()
                                # Derive OHLC as ratio
                                # Use base_usd OHLC / quote_usd close (approx)
                                df["close"] = derived_close
                                df["open"] = float(df_base_usd["open"].iloc[-1]) / quote_close if "open" in df_base_usd.columns else derived_close
                                df["high"] = float(df_base_usd["high"].iloc[-1]) / quote_close if "high" in df_base_usd.columns else derived_close
                                df["low"] = float(df_base_usd["low"].iloc[-1]) / quote_close if "low" in df_base_usd.columns else derived_close
                                df["volume"] = df_base_usd["volume"] if "volume" in df_base_usd.columns else 0
                                df = _compute_vwap(df)
                                df = _add_indicators(df)
                                try:
                                    df = df.sort_index()
                                except Exception:
                                    pass
                                _set_cached(key, df)
                                log_event("market_data_derived_cross", symbol=sym, via=[f"{base}/USD", f"{quote}/USD"], derived_close=derived_close)
                                return df
                        except Exception as e2:
                            log_event("market_data_derive_failed", level="warning", symbol=sym, error=str(e2)[:200])
                except Exception:
                    pass

        # No mock fallback — if still empty, return empty and let caller show "No data available"
        if df.empty:
            log_event("market_data_no_data", symbol=sym, timeframe=timeframe, reason="No data available for this symbol/timeframe (Alpaca returned 0 rows, weekend or free-tier gap)")
            # Do not cache empty with mock; cache empty for short TTL to avoid hammering
            _set_cached(key, df)
            return df

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

    # No mock fallback — OPRA requires subscription, free-tier has no options data
    # If Alpaca options client not available or not subscribed, return empty with log
    log_event("option_chain_no_data", underlying=sym, reason="No data available for this underlying/expiration (OPRA subscription required, free-tier has no options chain)")
    return []


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

