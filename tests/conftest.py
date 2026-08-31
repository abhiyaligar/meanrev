"""
Fixtures — single source, no hardcoded trading values.

All risk thresholds, rate limits, and intervals are read from backend/core/config.get_settings()
so tests never hardcode 0.15 / 0.60 / 0.03 / 25. If .env changes, tests adapt automatically.
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend is importable
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session")
def settings():
    from backend.core.config import get_settings

    return get_settings()


@pytest.fixture
def risk_thresholds(settings):
    """Dynamic thresholds from .env — never hardcoded."""
    return {
        "max_position_pct": float(settings.risk_max_position_pct),
        "max_exposure_pct": float(settings.risk_max_exposure_pct),
        "drawdown_pct": float(settings.risk_daily_drawdown_pct),
    }


@pytest.fixture
def equity():
    """Base equity for sizing — use 100k paper default, but read from fixture not hardcoded in tests logic."""
    return 100_000.0


@pytest.fixture
def account(equity):
    """Paper account shape matching broker/client.get_account() dump."""
    return {
        "id": "test-acct-0001",
        "account_number": "PA00000001",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": str(equity * 0.85),
        "portfolio_value": str(equity),
        "buying_power": str(equity * 4),
        "equity": str(equity),
        "last_equity": str(equity),
        "trading_blocked": False,
        "options_approved_level": 3,
        "options_buying_power": str(equity * 0.85),
    }


@pytest.fixture
def account_drawdown(account, risk_thresholds):
    """Account that has just exceeded drawdown — equity = peak * (1 - drawdown - 0.001)."""
    peak = float(account["portfolio_value"])
    dd = risk_thresholds["drawdown_pct"]
    # Slightly beyond threshold
    equity_now = peak * (1 - dd - 0.001)
    acct = dict(account)
    acct["portfolio_value"] = str(equity_now)
    acct["equity"] = str(equity_now)
    # last_equity is peak
    acct["last_equity"] = str(peak)
    return acct


@pytest.fixture
def positions_empty():
    return []


@pytest.fixture
def positions_single():
    return [
        {
            "symbol": "AAPL",
            "qty": "10",
            "avg_entry_price": "150.0",
            "market_value": "1600.0",
            "cost_basis": "1500.0",
            "unrealized_pl": "100.0",
            "unrealized_plpc": "0.06",
            "side": "long",
        }
    ]


@pytest.fixture
def orders_open():
    now = "2026-08-31T10:00:00Z"
    return [
        {"id": "ord-open-1", "symbol": "AAPL", "side": "buy", "qty": "1", "type": "limit", "status": "new", "limit_price": "1", "created_at": now},
        {"id": "ord-open-2", "symbol": "SPY", "side": "buy", "qty": "2", "type": "limit", "status": "new", "limit_price": "1", "created_at": now},
    ]


@pytest.fixture
def clock_open():
    return {"is_open": True, "timestamp": "2026-08-31T10:00:00-04:00", "next_open": "2026-08-31T09:30:00-04:00", "next_close": "2026-08-31T16:00:00-04:00"}


@pytest.fixture
def clock_closed():
    return {"is_open": False, "timestamp": "2026-08-31T07:00:00-04:00", "next_open": "2026-08-31T09:30:00-04:00", "next_close": "2026-08-31T16:00:00-04:00"}


@pytest.fixture
def bucket_fresh():
    """Fresh TokenBucket for rate-limit tests — reads capacity from rate_limit module, not hardcoded."""
    from backend.broker.rate_limit import TokenBucket, BUCKET_CAPACITY, REFILL_PER_SECOND

    b = TokenBucket(capacity=BUCKET_CAPACITY, refill_per_sec=REFILL_PER_SECOND)
    # Ensure full
    b._tokens = float(BUCKET_CAPACITY)
    b._last_refill = time.monotonic()
    return b


@pytest.fixture
def mock_broker(monkeypatch, account, positions_single, orders_open, clock_open):
    """Patch broker/client to return controlled values without network."""
    from backend.broker import client as bc

    monkeypatch.setattr(bc, "get_account", lambda: dict(account))
    monkeypatch.setattr(bc, "get_positions", lambda symbol=None: [p for p in positions_single if not symbol or p["symbol"] == symbol] if symbol else list(positions_single))
    monkeypatch.setattr(bc, "get_orders", lambda status="open", limit=50, symbols=None: [o for o in orders_open if status in ("open", "all")][:limit])
    monkeypatch.setattr(bc, "get_clock", lambda: dict(clock_open))
    # submit/cancel/replace not mocked by default — tests that need them patch directly
    return bc
