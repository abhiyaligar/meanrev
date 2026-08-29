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
    """Normalize alpaca-py model to dict; fallback to raw."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
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
