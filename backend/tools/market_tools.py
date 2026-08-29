"""
Market tools — LangChain @tool wrappers around backend/data/market.py.

Per langchain-docs MCP: @tool + type hints + docstring.
All market data respects 25/min bucket, 30s timeout, and cache; options chain
provides indicative Greeks so every strategy can include options.
"""

import json
from langchain.tools import tool

from backend.data.market import align_timeframes as _align_timeframes, fetch_ohlcv as _fetch_ohlcv, fetch_option_chain as _fetch_option_chain, get_market_snapshot as _get_market_snapshot_data


@tool
def get_ohlcv(symbol: str, timeframe: str = "1Day", limit: int = 50) -> str:
    """Fetch OHLCV bars with VWAP and indicators (RSI, MACD, EMA 20/50/200, Bollinger, ATR). Args: symbol e.g. 'AAPL' (required), timeframe 1Day|1Hour|5Min|1Min (default 1Day), limit 1..500 (default 50). Returns JSON records."""
    try:
        sym = symbol.strip().upper()
        if not sym:
            return json.dumps({"error": "symbol required"})
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 50
        if lim < 1:
            lim = 1
        lim = min(lim, 500)
        df = _fetch_ohlcv(sym, timeframe=timeframe, limit=lim)
        if df.empty:
            return json.dumps({"symbol": sym, "timeframe": timeframe, "count": 0, "bars": []})
        # Tail to limit and convert to records with ISO timestamps
        tail = df.tail(lim)
        tail.index = tail.index.map(lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x))
        records = tail.reset_index().to_dict(orient="records")
        # Redact to last 5 for token discipline
        sample = records[-5:]
        return json.dumps({"symbol": sym, "timeframe": timeframe, "count": len(df), "sample_bars": sample}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def get_market_snapshot(symbol: str, timeframes: str = "1Day,1Hour") -> str:
    """Fetch OHLCV at multiple timeframes for a symbol. Args: symbol e.g. 'AAPL', timeframes comma list e.g. '1Day,1Hour' or '1Day,1Hour,5Min'. Returns counts per timeframe."""
    try:
        sym = symbol.strip().upper()
        if not sym:
            return json.dumps({"error": "symbol required"})
        tfs = [t.strip() for t in timeframes.split(",") if t.strip()] if timeframes else ["1Day", "1Hour"]
        frames = _get_market_snapshot_data(sym, timeframes=tfs)
        summary = {tf: {"rows": len(df), "has_indicators": not df.empty and "rsi" in df.columns} for tf, df in frames.items()}
        return json.dumps({"symbol": sym, "timeframes": summary}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def get_option_chain(underlying: str, expiration: str = "", limit: int = 10) -> str:
    """Fetch indicative option chain with Greeks for underlying. Args: underlying e.g. 'AAPL' (required), expiration YYYY-MM-DD (optional, default ~30d), limit 1..100 (default 10). Every strategy must use options — this tool provides delta/gamma/theta/vega."""
    try:
        sym = underlying.strip().upper()
        if not sym:
            return json.dumps({"error": "underlying required"})
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = 10
        if lim < 1:
            lim = 1
        lim = min(lim, 100)
        exp = expiration.strip() if expiration.strip() else None
        chain = _fetch_option_chain(sym, expiration=exp, limit=lim)
        return json.dumps({"underlying": sym, "count": len(chain), "chain": chain}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def align_timeframes_tool(symbol: str, timeframes: str = "1Day,1Hour") -> str:
    """Time-align multi-timeframe features onto single timestamp index via asof join. Args: symbol, timeframes comma list. Returns aligned shape and sample columns."""
    try:
        sym = symbol.strip().upper()
        if not sym:
            return json.dumps({"error": "symbol required"})
        tfs = [t.strip() for t in timeframes.split(",") if t.strip()] if timeframes else ["1Day", "1Hour"]
        frames = _get_market_snapshot_data(sym, timeframes=tfs)
        aligned = _align_timeframes(frames)
        if aligned.empty:
            return json.dumps({"symbol": sym, "aligned": False, "reason": "no data"})
        return json.dumps(
            {
                "symbol": sym,
                "rows": len(aligned),
                "cols": len(aligned.columns),
                "sample_cols": list(aligned.columns)[:8],
                "sample": aligned.tail(2).to_dict(orient="records") if len(aligned) >= 2 else [],
            },
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})
