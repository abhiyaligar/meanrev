"""
Throttled Alpaca broker client — single integration point per DOC.md §5 / Backend_Architecture.md §6.4.

All Alpaca Trading API access funnels here.
- paper=True enforced, url_override normalized
- 25 req/min leaky bucket (broker/rate_limit.bucket)
- 429/5xx/timeout → exponential backoff + jitter (up to 3 retries)
- Never logs or returns secrets
"""

import logging
import concurrent.futures
from typing import Any, Dict, List, Optional

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from backend.broker.rate_limit import MAX_RETRIES, RateLimitExceeded, bucket, is_retryable_exception
from backend.core.config import get_settings
from backend.core.utils import clamp_limit, normalize_symbol


class AlpacaConnectionError(Exception):
    """Missing or invalid credentials — maps to 401."""

    pass


class BrokerRateLimitError(Exception):
    """Bucket empty or upstream 429 — maps to 429."""

    def __init__(self, message: str, retry_after: float = 2.0):
        super().__init__(message)
        self.retry_after = retry_after


def _create_trading_client() -> TradingClient:
    s = get_settings()
    key = s.get_key()
    secret = s.get_secret()
    if not key or not secret:
        raise AlpacaConnectionError("Set ALPACA_API_KEY and ALPACA_API_SECRET in backend/.env")
    url = s.alpaca_api_url.rstrip("/")
    if url.endswith("/v2"):
        url = url[:-3]
    return TradingClient(api_key=key, secret_key=secret, paper=True, url_override=url)


def _dump(obj: Any) -> Any:
    """Normalize alpaca-py model to dict; fallback to raw. Mode json ensures UUID/datetime -> str."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        try:
            # Pydantic v2: mode="json" converts UUID, datetime, Decimal to JSON-compatible primitives
            return obj.model_dump(mode="json")  # type: ignore
        except TypeError:
            return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()  # type: ignore
    if isinstance(obj, dict):
        return obj
    return obj


def _dump_list(objs: List[Any]) -> List[Dict[str, Any]]:
    return [_dump(o) for o in objs]


def _run_with_timeout(fn, timeout: float = 30.0, *args, **kwargs):
    """Run fn with a hard timeout (default 30s) to avoid hung worker threads — library-backed via concurrent.futures."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as e:
            future.cancel()
            raise TimeoutError(f"Alpaca call timed out after {timeout}s") from e


_logger = logging.getLogger(__name__)


def _tenacity_retry_predicate(exc: BaseException) -> bool:
    """For tenacity: retry only retryable, never on RateLimitExceeded/BrokerRateLimitError/AlpacaConnectionError."""
    if isinstance(exc, (RateLimitExceeded, BrokerRateLimitError, AlpacaConnectionError)):
        return False
    return is_retryable_exception(exc)  # type: ignore[arg-type]


def _call_with_retry(fn, *args, timeout: float = 30.0, **kwargs):
    """
    Wrapper: consume token, call fn with timeout, retry on retryable via tenacity (library) instead of custom loop.
    Uses tenacity.wait_exponential_jitter + stop_after_attempt + retry_if_exception.
    RateLimitExceeded → immediate BrokerRateLimitError (no retry).
    """
    try:
        bucket.consume(1)
    except RateLimitExceeded as e:
        raise BrokerRateLimitError(str(e), retry_after=e.retry_after)

    @retry(
        stop=stop_after_attempt(MAX_RETRIES + 1),
        wait=wait_exponential_jitter(initial=0.5, max=8, jitter=2),
        retry=retry_if_exception(_tenacity_retry_predicate),
        before_sleep=before_sleep_log(_logger, logging.WARNING),
        reraise=True,
    )
    def _do_with_tenacity():
        return _run_with_timeout(fn, timeout, *args, **kwargs)

    return _do_with_tenacity()


# --- Public broker surface ---

def get_account() -> Dict[str, Any]:
    """Fetch paper account. Single throttled entry point."""

    def _do():
        client = _create_trading_client()
        acct = client.get_account()
        return _dump(acct)

    return _call_with_retry(_do)


def get_positions(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List open positions. If symbol provided, returns single-element list or [].
    Symbol is normalized to upper-case per VULN 6 fix.
    """

    # Single source via core/utils
    norm_symbol = normalize_symbol(symbol)

    def _do():
        client = _create_trading_client()
        if norm_symbol:
            try:
                pos = client.get_open_position(norm_symbol)
                return [_dump(pos)]
            except Exception as e:
                # alpaca-py raises 404-like for missing position — map to empty list
                msg = str(e).lower()
                if "position does not exist" in msg or "404" in msg or "not found" in msg:
                    return []
                raise
        else:
            positions = client.get_all_positions()
            return _dump_list(list(positions))

    return _call_with_retry(_do)


def get_orders(
    status: str = "open",
    limit: int = 50,
    symbols: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    List orders.
    status: open | closed | all (maps to QueryOrderStatus)
    limit: 1..500 (clamped)
    symbols: comma-separated filter, e.g. "AAPL,SPY"
    """

    def _do():
        client = _create_trading_client()
        lim = clamp_limit(limit, default=50, min_val=1, max_val=500)
        # Map status
        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        q_status = status_map.get(status.lower(), QueryOrderStatus.OPEN)

        req = GetOrdersRequest(status=q_status, limit=lim)
        # alpaca-py GetOrdersRequest uses `symbols` in newer versions; fallback to raw filter
        # We handle symbol filtering post-fetch if SDK doesn't support it, to keep behavior stable
        orders = client.get_orders(filter=req)
        dumped = _dump_list(list(orders))
        if symbols:
            wanted = {s.strip().upper() for s in symbols.split(",") if s.strip()}
            if wanted:
                dumped = [o for o in dumped if str(o.get("symbol", "")).upper() in wanted]
                # respect limit after filtering
                dumped = dumped[:lim]
        return dumped

    return _call_with_retry(_do)


def get_clock() -> Dict[str, Any]:
    """Market clock — is_open, next_open/close, timestamp."""

    def _do():
        client = _create_trading_client()
        clock = client.get_clock()
        return _dump(clock)

    return _call_with_retry(_do)


def submit_order(
    symbol: str,
    qty: float,
    side: str = "buy",
    order_type: str = "market",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    time_in_force: str = "day",
) -> Dict[str, Any]:
    """
    Submit a paper order — throttled, 30s timeout, tenacity retry.
    Supports market, limit, stop, and options single-leg (via same Market/Limit with option symbol).
    Symbol is normalized to upper; qty clamped >0; side buy|sell; order_type market|limit|stop.
    Returns dumped order dict.
    """
    sym = normalize_symbol(symbol)
    if not sym:
        raise ValueError("symbol required for submit_order")
    try:
        q = float(qty)
    except (TypeError, ValueError):
        raise ValueError(f"qty {qty!r} must be numeric >0")
    if q <= 0:
        raise ValueError("qty must be >0")

    sd = side.strip().lower() if side else "buy"
    if sd not in ("buy", "sell"):
        sd = "buy"
    ot = order_type.strip().lower() if order_type else "market"
    if ot not in ("market", "limit", "stop"):
        ot = "market"
    tif = time_in_force.strip().lower() if time_in_force else "day"
    # Crypto requires GTC (not DAY); if user passes day for crypto, auto-correct to gtc
    if _is_crypto_symbol(sym) and tif == "day":
        tif = "gtc"
    # Map tif string to alpaca enum via lower
    # Alpaca TimeInForce: Day, GTC, OPG, CLS, IOC, FOK
    tif_map = {"day": "day", "gtc": "gtc", "opg": "opg", "cls": "cls", "ioc": "ioc", "fok": "fok"}
    tif_val = tif_map.get(tif, "day")

    def _do():
        client = _create_trading_client()
        # Lazy imports to avoid circular
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopOrderRequest

        side_enum = OrderSide.BUY if sd == "buy" else OrderSide.SELL
        # Map tif string to enum
        tif_enum = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "opg": TimeInForce.OPG,
            "cls": TimeInForce.CLS,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK,
        }.get(tif_val, TimeInForce.DAY)

        # Detect option symbol heuristic: contains expiry + C/P + strike (e.g., AAPL240830C00150000) or SPXW family
        is_option = len(sym) > 12 and any(c in sym for c in ("C", "P")) or _is_option_symbol(sym)

        # Build request — qty handling: stocks float shares, options int contracts, crypto float coins
        is_crypto = _is_crypto_symbol(sym)
        if is_crypto:
            order_qty = float(q)  # crypto fractional allowed
        else:
            order_qty = int(q) if is_option else q

        if ot == "limit":
            if limit_price is None or limit_price <= 0:
                raise ValueError("limit_price required >0 for limit orders")
            req = LimitOrderRequest(
                symbol=sym,
                qty=order_qty,
                side=side_enum,
                time_in_force=tif_enum,
                limit_price=float(limit_price),
            )
        elif ot == "stop":
            if stop_price is None or stop_price <= 0:
                raise ValueError("stop_price required >0 for stop orders")
            req = StopOrderRequest(
                symbol=sym,
                qty=order_qty,
                side=side_enum,
                time_in_force=tif_enum,
                stop_price=float(stop_price),
            )
        else:
            req = MarketOrderRequest(
                symbol=sym,
                qty=order_qty,
                side=side_enum,
                time_in_force=tif_enum,
            )

        order = client.submit_order(order_data=req)
        return _dump(order)

    return _call_with_retry(_do)


def cancel_order(order_id: str) -> Dict[str, Any]:
    """
    Cancel a single open order by its UUID/str id — throttled 25/min, 30s timeout.
    Returns {"cancelled": True, "order_id": str} or raises on not-found.
    """
    oid = str(order_id).strip()
    if not oid:
        raise ValueError("order_id required for cancel_order")

    def _do():
        client = _create_trading_client()
        # cancel_order_by_id returns None on success in alpaca-py
        client.cancel_order_by_id(oid)
        return {"cancelled": True, "order_id": oid}

    return _call_with_retry(_do)


def cancel_all_orders() -> List[Dict[str, Any]]:
    """
    Cancel all open orders — throttled 25/min.
    Returns list of CancelOrderResponse dicts (id + status code).
    """
    def _do():
        client = _create_trading_client()
        results = client.cancel_orders()
        # results may be list of CancelOrderResponse or dict; normalize via _dump_list
        if isinstance(results, list):
            return [_dump(r) if not isinstance(r, dict) else r for r in results]
        if isinstance(results, dict):
            return [results]
        return _dump_list(list(results)) if results else []

    return _call_with_retry(_do)  # type: ignore


def replace_order(
    order_id: str,
    qty: Optional[float] = None,
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    trail: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Modify (replace) an existing open order — throttled 25/min, 30s timeout.
    Only qty, limit_price, stop_price, trail are mutable per Alpaca ReplaceOrderRequest.
    Returns dumped updated Order. If no field provided, raises ValueError.
    """
    oid = str(order_id).strip()
    if not oid:
        raise ValueError("order_id required for replace_order")
    # At least one field must be set
    if qty is None and limit_price is None and stop_price is None and trail is None:
        raise ValueError("At least one of qty, limit_price, stop_price, trail required for replace")

    def _do():
        client = _create_trading_client()
        from alpaca.trading.requests import ReplaceOrderRequest

        # Build request with only provided fields (NonEmptyRequest drops None)
        req_kwargs: Dict[str, Any] = {}
        if qty is not None:
            try:
                q = float(qty)
            except (TypeError, ValueError):
                raise ValueError(f"qty {qty!r} must be numeric >0")
            if q <= 0:
                raise ValueError("qty must be >0")
            # Replace qty is int in SDK; for crypto fractional, SDK may still require int — we pass int if not crypto context
            # Since we don't have symbol here, pass rounded int if q is whole, else float (API will validate)
            req_kwargs["qty"] = int(q) if float(q).is_integer() else q
        if limit_price is not None:
            lp = float(limit_price)
            if lp <= 0:
                raise ValueError("limit_price must be >0")
            req_kwargs["limit_price"] = lp
        if stop_price is not None:
            sp = float(stop_price)
            if sp <= 0:
                raise ValueError("stop_price must be >0")
            req_kwargs["stop_price"] = sp
        if trail is not None:
            tr = float(trail)
            if tr <= 0:
                raise ValueError("trail must be >0")
            req_kwargs["trail"] = tr

        req = ReplaceOrderRequest(**req_kwargs)
        order = client.replace_order_by_id(oid, req)
        return _dump(order)

    return _call_with_retry(_do)  # type: ignore


def _is_crypto_symbol(sym: str) -> bool:
    """Detect crypto for qty/time_in_force handling (BTC/USD, BTC, ETH/USD etc.)."""
    if not sym:
        return False
    s = sym.strip().upper().replace(" ", "")
    if "/" in s:
        base = s.split("/")[0]
        return base in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LTC", "BCH", "XRP", "ADA", "DOT", "LINK", "UNI", "ATOM")
    if s in ("BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "LTC", "BCH", "XRP", "ADA", "DOT"):
        return True
    if s in ("BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD", "BTCUSDT", "ETHUSDT"):
        return True
    for base in ("BTC", "ETH", "SOL", "DOGE"):
        if s.startswith(base) and (s == base or s.endswith("USD") or s.endswith("USDT")):
            return True
    return False


def _is_option_symbol(sym: str) -> bool:
    """Heuristic for option symbol vs equity: option symbols are long and contain date+strike, or SPXW family."""
    if _is_crypto_symbol(sym):
        return False
    s = sym.upper()
    if s.startswith(("SPXW", "XSP", "SPX", "NDX", "RUT")):
        return True
    # Typical OCC option symbol: AAPL + 6-digit date + C/P + 8-digit strike -> len >= 15
    if len(s) >= 15 and s[-9:-1].isdigit():
        return True
    return False
