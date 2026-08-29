"""
Broker router — /api/v1 read surface per approved plan.

4 GETs: account, positions, orders, clock.
All funnel through broker/client.py (25 req/min bucket + backoff+jitter).
Legacy /get_account stays as alias (deprecated).
"""

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.broker import client as broker_client
from backend.broker.client import AlpacaConnectionError, BrokerRateLimitError
from backend.core.models import AccountResponse, ClockResponse, OrdersResponse, PositionsResponse
from backend.core.logging import log_broker_call

router = APIRouter(prefix="/api/v1", tags=["broker"])


@router.get("/account", response_model=AccountResponse, summary="Get paper account")
def get_account():
    start = time.monotonic()
    try:
        account = broker_client.get_account()
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/account", latency, "200")
        return AccountResponse(connected=True, account=account)
    except AlpacaConnectionError as e:
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/account", latency, "401", error=str(e))
        raise HTTPException(status_code=401, detail=str(e))
    except BrokerRateLimitError as e:
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/account", latency, "429", error=str(e))
        raise HTTPException(status_code=429, detail={"error": str(e), "type": "RateLimit", "retry_after": e.retry_after})
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/account", latency, "502", error=str(e))
        raise HTTPException(status_code=502, detail={"error": str(e), "type": type(e).__name__})


@router.get("/positions", response_model=PositionsResponse, summary="List open positions")
def get_positions(
    symbol: Optional[str] = Query(None, description="Filter by symbol, e.g. AAPL"),
):
    start = time.monotonic()
    try:
        positions = broker_client.get_positions(symbol=symbol)
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/positions", latency, "200", symbol=symbol, count=len(positions))
        return PositionsResponse(count=len(positions), positions=positions, symbol_filter=symbol)
    except AlpacaConnectionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except BrokerRateLimitError as e:
        raise HTTPException(status_code=429, detail={"error": str(e), "type": "RateLimit", "retry_after": e.retry_after})
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e), "type": type(e).__name__})


@router.get("/orders", response_model=OrdersResponse, summary="List orders")
def get_orders(
    status: str = Query("open", description="open | closed | all", pattern="^(open|closed|all)$"),
    limit: int = Query(50, ge=1, le=500, description="1..500, default 50"),
    symbols: Optional[str] = Query(None, description="Comma-separated symbols filter, e.g. AAPL,SPY"),
):
    start = time.monotonic()
    try:
        orders = broker_client.get_orders(status=status, limit=limit, symbols=symbols)
        latency = (time.monotonic() - start) * 1000
        log_broker_call("GET /api/v1/orders", latency, "200", order_status=status, limit=limit, symbols=symbols, count=len(orders))
        return OrdersResponse(
            count=len(orders),
            orders=orders,
            status_filter=status,
            limit=limit,
            symbols_filter=symbols,
        )
    except AlpacaConnectionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except BrokerRateLimitError as e:
        raise HTTPException(status_code=429, detail={"error": str(e), "type": "RateLimit", "retry_after": e.retry_after})
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e), "type": type(e).__name__})


@router.get("/clock", response_model=ClockResponse, summary="Market clock")
def get_clock():
    start = time.monotonic()
    try:
        clock = broker_client.get_clock()
        latency = (time.monotonic() - start) * 1000
        # clock dump contains is_open
        is_open = bool(clock.get("is_open", False)) if isinstance(clock, dict) else False
        log_broker_call("GET /api/v1/clock", latency, "200", is_open=is_open)
        return ClockResponse(is_open=is_open, clock=clock)
    except AlpacaConnectionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except BrokerRateLimitError as e:
        raise HTTPException(status_code=429, detail={"error": str(e), "type": "RateLimit", "retry_after": e.retry_after})
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e), "type": type(e).__name__})
