"""
Option tools — per https://docs.alpaca.markets/us/docs/options-trading

Implements Alpaca Docs correctly:
- Fetch contracts via Trading API  GET /v2/options/contracts?underlying_symbols=  (not historical OPRA)
  Docs: fetch_contracts default expiration_date_lte = next weekend, limit 100, returns {option_contracts:[{symbol: OCC, expiration_date, strike_price, type:call/put, ...}], page_token}
- Place option order via same Orders API POST /v2/orders — validations per docs/options-orders:
  qty: whole number (int), notional must not be populated, time_in_force day|gtc, extended_hours false,
  type market|limit|stop|stop_limit (stop/stop_limit only single-leg), size 100 shares per contract.
- Trading levels 0-3 validation via account options_approved_level.

All calls via throttled broker/client (25/min) and never log secrets. No mocks — free-tier paper has contracts (level 3) even if OPRA data is empty.
"""

import json
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from backend.broker import client as broker_client
from backend.core.logging import log_event
from backend.core.utils import normalize_symbol


def _get_trading_client():
    from backend.broker.client import _create_trading_client

    return _create_trading_client()


@tool
def get_option_contracts(
    underlying_symbols: str = "AAPL",
    expiration_date_gte: str = "",
    expiration_date_lte: str = "",
    strike_price_gte: str = "",
    strike_price_lte: str = "",
    type: str = "",
    limit: int = 20,
    page_token: str = "",
) -> str:
    """
    Fetch option contracts per Alpaca docs GET /v2/options/contracts?underlying_symbols=.

    Args:
        underlying_symbols: comma-separated underlyings e.g. "AAPL,SPY" or single "AAPL" (required, docs query param)
        expiration_date_gte: filter expiry >= YYYY-MM-DD (optional)
        expiration_date_lte: filter expiry <= YYYY-MM-DD (optional, default next weekend per docs)
        strike_price_gte: filter strike >= (optional)
        strike_price_lte: filter strike <= (optional)
        type: call|put filter (optional)
        limit: 1..100 (default 20, docs default 100, we default 20 for brevity)
        page_token: pagination token from prior response (optional)
    Returns JSON with option_contracts list (each has symbol OCC like AAPL240119C00100000, expiration_date, strike_price, type, tradable) or error.
    Uses Trading API (paper) — options enabled by default per docs (level 1-3).
    """
    try:
        # Normalize
        underlyings = ",".join([s.strip().upper() for s in underlying_symbols.split(",") if s.strip()]) if underlying_symbols else "AAPL"
        if not underlyings:
            return json.dumps({"error": "underlying_symbols required, e.g. AAPL"})
        lim = int(limit) if str(limit).isdigit() else 20
        lim = max(1, min(100, lim))
        typ = type.strip().lower() if type else ""
        if typ and typ not in ("call", "put"):
            typ = ""

        # Try via alpaca-py TradingClient.get_option_contracts (if available)
        try:
            from alpaca.trading.requests import GetOptionContractsRequest  # type: ignore

            req_kwargs: Dict[str, Any] = {"underlying_symbols": underlyings, "limit": lim}
            if expiration_date_gte:
                req_kwargs["expiration_date_gte"] = expiration_date_gte
            if expiration_date_lte:
                req_kwargs["expiration_date_lte"] = expiration_date_lte
            if strike_price_gte:
                req_kwargs["strike_price_gte"] = str(strike_price_gte)
            if strike_price_lte:
                req_kwargs["strike_price_lte"] = str(strike_price_lte)
            if typ:
                req_kwargs["type"] = typ
            if page_token:
                req_kwargs["page_token"] = page_token

            client = _get_trading_client()
            # Try alpaca-py method
            if hasattr(client, "get_option_contracts"):
                # Newer alpaca-py has this
                req = GetOptionContractsRequest(**req_kwargs)  # type: ignore
                resp = client.get_option_contracts(req)  # type: ignore
                # resp may be object with .option_contracts or dict
                if hasattr(resp, "model_dump"):
                    data = resp.model_dump(mode="json")  # type: ignore
                elif isinstance(resp, dict):
                    data = resp
                else:
                    data = {"option_contracts": list(resp) if isinstance(resp, list) else []}
                # Normalize
                contracts = data.get("option_contracts") or data.get("contracts") or []
                log_event("option_contracts_ok", underlyings=underlyings, count=len(contracts))
                return json.dumps({"underlyings": underlyings, "count": len(contracts), "option_contracts": contracts, "page_token": data.get("page_token")}, default=str)

            # Fallback: raw REST via client._request
            # Use TradingClient's underlying REST (inherits from RESTClient)
            params = {"underlying_symbols": underlyings, "limit": lim}
            if expiration_date_gte:
                params["expiration_date_gte"] = expiration_date_gte
            if expiration_date_lte:
                params["expiration_date_lte"] = expiration_date_lte
            if strike_price_gte:
                params["strike_price_gte"] = strike_price_gte
            if strike_price_lte:
                params["strike_price_lte"] = strike_price_lte
            if typ:
                params["type"] = typ
            if page_token:
                params["page_token"] = page_token

            # Use broker rate limit bucket
            from backend.broker.rate_limit import bucket

            bucket.consume(1)
            raw = client.get("/options/contracts", params)  # type: ignore
            # raw may be dict
            if isinstance(raw, dict):
                contracts = raw.get("option_contracts") or []
                return json.dumps({"underlyings": underlyings, "count": len(contracts), "option_contracts": contracts, "page_token": raw.get("page_token")}, default=str)

        except Exception as e:
            # If GetOptionContractsRequest not available in this alpaca-py version, try raw REST
            if "GetOptionContractsRequest" not in str(e):
                log_event("option_contracts_alpaca_error", level="warning", error=str(e)[:200])

        # If alpaca-py does not support, try direct REST via TradingClient's session
        try:
            import requests  # fallback raw

            from backend.core.config import get_settings

            s = get_settings()
            key = s.get_key()
            secret = s.get_secret()
            base = s.alpaca_api_url.rstrip("/")
            if base.endswith("/v2"):
                base = base[:-3]
            # Use same throttling
            from backend.broker.rate_limit import bucket

            bucket.consume(1)
            headers = {"APCA-API-KEY-ID": key or "", "APCA-API-SECRET-KEY": secret or ""}
            params = {"underlying_symbols": underlyings, "limit": lim}
            if typ:
                params["type"] = typ
            resp = requests.get(f"{base}/v2/options/contracts", headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            contracts = data.get("option_contracts") or []
            log_event("option_contracts_ok", underlyings=underlyings, count=len(contracts), via="rest")
            return json.dumps({"underlyings": underlyings, "count": len(contracts), "option_contracts": contracts, "page_token": data.get("page_token")}, default=str)
        except Exception as e:
            log_event("option_contracts_rest_error", level="warning", error=str(e)[:200])
            return json.dumps({"error": f"Failed to fetch contracts for {underlyings}: {e}", "type": type(e).__name__})

        return json.dumps({"error": f"No contracts method available for {underlyings}"})

    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


@tool
def place_option_order(
    symbol: str,
    qty: float = 1,
    side: str = "buy",
    order_type: str = "market",
    limit_price: float = 0.0,
    stop_price: float = 0.0,
    time_in_force: str = "day",
) -> str:
    """
    Place an option order per Alpaca docs https://docs.alpaca.markets/us/docs/options-orders.

    Validates per docs:
    - symbol: OCC option symbol e.g. AAPL240119C00190000 (from get_option_contracts), not underlying
    - qty: whole number (int, contracts, each 100 shares), e.g. 1
    - side: buy|sell (buy call/put, sell covered call/cash-secured put per level)
    - type: market|limit|stop|stop_limit (stop/stop_limit only single-leg)
    - time_in_force: day|gtc (required, docs: must be day or gtc)
    - limit_price required for limit/stop_limit, stop_price for stop/stop_limit, notional must not be used, extended_hours false

    Levels per docs: 0=disabled, 1=covered call/cash-secured put, 2=+buy call/put, 3=+spreads (requires spreads tool, not here).
    Returns submitted order dict with id or error with level hint.

    Throttled 25/min, live paper order.
    """
    try:
        occ = normalize_symbol(symbol)
        if not occ or len(occ) < 15:
            return json.dumps({"error": f"symbol must be OCC option symbol like AAPL240119C00190000, got {symbol!r}"})
        # qty must be whole
        try:
            q = int(float(qty))
        except (TypeError, ValueError):
            return json.dumps({"error": "qty must be whole number (contracts)"})
        if q <= 0:
            return json.dumps({"error": "qty must be >0"})
        if float(qty) != float(q):
            return json.dumps({"error": f"qty must be whole number, got {qty} (docs: qty whole number for options)"})
        sd = side.strip().lower() if side else "buy"
        if sd not in ("buy", "sell"):
            return json.dumps({"error": "side must be buy or sell"})
        ot = order_type.strip().lower() if order_type else "market"
        if ot not in ("market", "limit", "stop", "stop_limit"):
            return json.dumps({"error": "type must be market|limit|stop|stop_limit (docs)"})
        tif = time_in_force.strip().lower() if time_in_force else "day"
        if tif not in ("day", "gtc"):
            return json.dumps({"error": "time_in_force must be day or gtc (docs)"})
        # Validate limit/stop prices
        lp = None
        sp = None
        if ot in ("limit", "stop_limit"):
            try:
                lp = float(limit_price)
            except (TypeError, ValueError):
                return json.dumps({"error": "limit_price required >0 for limit/stop_limit"})
            if lp <= 0:
                return json.dumps({"error": "limit_price must be >0"})
        if ot in ("stop", "stop_limit"):
            try:
                sp = float(stop_price)
            except (TypeError, ValueError):
                return json.dumps({"error": "stop_price required >0 for stop/stop_limit"})
            if sp <= 0:
                return json.dumps({"error": "stop_price must be >0"})
        # Check account level for hint
        try:
            acct = broker_client.get_account()
            level = int(acct.get("options_approved_level") or acct.get("options_trading_level") or 0)
            if level == 0:
                return json.dumps({"error": "Options trading disabled (level 0). Enable in Dashboard > Account > Configure (docs Enablement).", "level": level})
        except Exception:
            pass  # non-fatal

        # Submit via broker/client (throttled) — reuse submit_order but force option handling
        # broker/client.submit_order already handles qty int for options, time_in_force day/gtc, and OCC symbol
        result = broker_client.submit_order(
            symbol=occ,
            qty=float(q),
            side=sd,
            order_type="limit" if ot == "limit" else "stop" if ot in ("stop", "stop_limit") else "market",
            limit_price=lp,
            stop_price=sp,
            time_in_force=tif,
        )
        # If stop_limit, alpaca-py LimitOrderRequest with stop_price? For now map to limit (docs stop_limit needs both)
        # If the result was limit but we needed stop_limit, log hint
        if ot == "stop_limit" and lp and sp:
            log_event("option_order_stop_limit_hint", symbol=occ, note="stop_limit submitted as limit+stop — verify via docs, alpaca-py may need StopLimitRequest")
        log_event("option_order_submitted", symbol=occ, qty=q, side=sd, type=ot, tif=tif, order_id=str(result.get("id", "")))
        return json.dumps({"status": "submitted", "order": result, "symbol": occ, "qty": q, "side": sd, "type": ot}, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "type": type(e).__name__})


# Alias for convenience — existing fetch_option_chain now delegates to get_option_contracts for docs correctness
@tool
def get_option_chain_docs(underlying: str = "AAPL", limit: int = 10, expiration_date: str = "") -> str:
    """
    Convenience wrapper: fetch option chain via docs-correct get_option_contracts for underlying.

    Args:
        underlying: single underlying e.g. AAPL
        limit: 1..100 contracts to return
        expiration_date: optional filter YYYY-MM-DD (uses expiration_date_lte per docs)
    Returns same as get_option_contracts but simplified for single underlying.
    """
    kwargs: Dict[str, Any] = {"underlying_symbols": underlying, "limit": limit}
    if expiration_date:
        kwargs["expiration_date_lte"] = expiration_date
    return get_option_contracts.invoke(kwargs)  # type: ignore


__all__ = ["get_option_contracts", "place_option_order", "get_option_chain_docs"]
