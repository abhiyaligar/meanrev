"""Broker: rate limit 25/min, retry, timeout, error mapping — via config, no hardcodes."""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.broker.rate_limit import (
    BUCKET_CAPACITY,
    REFILL_PER_SECOND,
    TokenBucket,
    backoff_delay,
    is_retryable_exception,
)


@pytest.mark.unit
def test_token_bucket_capacity_via_config(settings, bucket_fresh):
    # Capacity/refill read from module constants which mirror config (not hardcoded 25)
    assert bucket_fresh.capacity == BUCKET_CAPACITY
    assert bucket_fresh.refill_per_sec == REFILL_PER_SECOND
    # Fresh bucket full
    assert bucket_fresh.remaining() == pytest.approx(BUCKET_CAPACITY)


@pytest.mark.unit
def test_token_bucket_consume_and_refill(bucket_fresh):
    b = bucket_fresh
    cap = b.capacity
    b.consume(5)
    assert b.remaining() == pytest.approx(cap - 5)
    # Exhaust
    for _ in range(int(cap - 5)):
        b.consume(1)
    assert b.remaining() == pytest.approx(0, abs=0.1)
    # Next consume raises with retry_after
    with pytest.raises(Exception) as ei:
        b.consume(1)
    assert hasattr(ei.value, "retry_after")
    assert ei.value.retry_after > 0
    # Wait for refill (sleep refill_per_sec)
    time.sleep(0.5)
    assert b.remaining() > 0


@pytest.mark.unit
def test_backoff_delay_range_and_cap():
    # Attempt 0 ~0.5 ±20% => 0.4-0.6 (allow 0.3-0.7 with jitter)
    for attempt in range(5):
        d = backoff_delay(attempt)
        assert 0.1 <= d <= 8.0
    # Attempt 0 specifically 0.4-0.8
    delays_0 = [backoff_delay(0) for _ in range(20)]
    assert min(delays_0) >= 0.3 and max(delays_0) <= 0.8
    # Attempt 10 capped at 8
    assert backoff_delay(10) <= 8.0
    assert backoff_delay(20) <= 8.0


@pytest.mark.unit
def test_is_retryable_classification():
    class Fake429(Exception):
        pass

    Fake429.__name__ = "Fake429"
    # By message
    assert is_retryable_exception(Exception("429 Too Many Requests"))
    assert is_retryable_exception(Exception("502 Bad Gateway"))
    assert is_retryable_exception(Exception("503 Service Unavailable"))
    assert is_retryable_exception(Exception("504 Gateway Timeout"))
    assert is_retryable_exception(Exception("timeout"))
    assert is_retryable_exception(TimeoutError("timed out"))
    # By status_code attr
    e = Exception("boom")
    e.status_code = 429
    assert is_retryable_exception(e)
    e.status_code = 500  # not in retry list (only 429,502,503,504)
    assert not is_retryable_exception(e)
    # Non-retryable
    assert not is_retryable_exception(Exception("400 Bad Request"))
    assert not is_retryable_exception(ValueError("invalid symbol"))


@pytest.mark.unit
def test_broker_client_retry_on_429_then_success(monkeypatch):
    from backend.broker import client as bc
    from backend.broker.rate_limit import bucket

    bucket.reset_for_tests()
    # Mock TradingClient to fail once with 429 then succeed
    call_count = {"n": 0}

    def fake_get_account():
        call_count["n"] += 1
        if call_count["n"] == 1:
            e = Exception("429 rate limit")
            e.status_code = 429
            raise e
        m = MagicMock()
        m.model_dump.return_value = {"id": "test", "portfolio_value": "100000"}
        return m

    mock_client = MagicMock()
    mock_client.get_account.side_effect = fake_get_account
    monkeypatch.setattr(bc, "_create_trading_client", lambda: mock_client)

    # Should retry and succeed on second attempt (tenacity)
    result = bc.get_account()
    assert result["id"] == "test"
    assert call_count["n"] == 2


@pytest.mark.unit
def test_broker_client_not_retry_on_validation_error(monkeypatch):
    from backend.broker import client as bc
    from backend.broker.rate_limit import bucket

    bucket.reset_for_tests()
    mock_client = MagicMock()
    mock_client.get_account.side_effect = ValueError("invalid symbol not retryable")
    monkeypatch.setattr(bc, "_create_trading_client", lambda: mock_client)
    with pytest.raises(ValueError):
        bc.get_account()


@pytest.mark.unit
def test_get_positions_empty_maps_to_empty(monkeypatch):
    from backend.broker import client as bc
    from backend.broker.rate_limit import bucket

    bucket.reset_for_tests()

    mock_client = MagicMock()
    # Simulate 404 for missing position
    def fake_get_open_position(sym):
        raise Exception("position does not exist 404")

    mock_client.get_open_position.side_effect = fake_get_open_position
    monkeypatch.setattr(bc, "_create_trading_client", lambda: mock_client)
    res = bc.get_positions(symbol="FAKE")
    assert res == []


@pytest.mark.unit
def test_get_orders_clamps_and_filters(monkeypatch):
    from backend.broker import client as bc
    from backend.broker.rate_limit import bucket

    bucket.reset_for_tests()
    mock_client = MagicMock()
    # Return 10 orders with symbols
    orders = []
    for i in range(10):
        m = MagicMock()
        m.model_dump.return_value = {"id": f"o{i}", "symbol": "AAPL" if i % 2 == 0 else "SPY"}
        orders.append(m)
    mock_client.get_orders.return_value = orders
    monkeypatch.setattr(bc, "_create_trading_client", lambda: mock_client)
    # limit 1..500 clamp: 0 -> 1, 999 -> 500
    res = bc.get_orders(status="open", limit=0, symbols=None)
    assert len(res) == 1  # clamped to 1
    res2 = bc.get_orders(status="open", limit=999, symbols="AAPL")
    # Filtered to AAPL only, but also clamped to 500 (still 5 AAPL)
    assert all(o["symbol"] == "AAPL" for o in res2)


@pytest.mark.unit
def test_submit_order_validates_and_normalizes():
    from backend.broker.client import submit_order

    with pytest.raises(ValueError, match="symbol required"):
        submit_order(symbol="", qty=1)
    with pytest.raises(ValueError, match="qty must be"):
        submit_order(symbol="AAPL", qty=0)
    with pytest.raises(ValueError):
        submit_order(symbol="AAPL", qty="bad")  # type: ignore


@pytest.mark.unit
def test_broker_http_mapping_401_429_502(monkeypatch):
    # Test FastAPI layer maps 401/429/502 — uses TestClient against app/main
    from backend.broker.rate_limit import bucket
    from backend.broker import client as bc

    bucket.reset_for_tests()
    # Missing creds -> 401
    monkeypatch.setattr(bc, "_create_trading_client", lambda: (_ for _ in ()).throw(Exception("Set ALPACA_API_KEY")))
    # We need to patch get_settings to make get_key return None so _create raises 401 path
    # Simpler: mock bc.get_account to raise AlpacaConnectionError
    from backend.broker.client import AlpacaConnectionError

    monkeypatch.setattr(bc, "get_account", lambda: (_ for _ in ()).throw(AlpacaConnectionError("Set ALPACA_API_KEY")))

    from backend.app.main import app

    client = TestClient(app)
    r = client.get("/api/v1/account")
    assert r.status_code == 401

    # Rate limit -> 429
    from backend.broker.client import BrokerRateLimitError

    monkeypatch.setattr(bc, "get_account", lambda: (_ for _ in ()).throw(BrokerRateLimitError("Rate limit exceeded", retry_after=1.5)))
    r2 = client.get("/api/v1/account")
    assert r2.status_code == 429
    assert "retry_after" in r2.text

    # Upstream 5xx -> 502
    monkeypatch.setattr(bc, "get_account", lambda: (_ for _ in ()).throw(Exception("502 Bad Gateway")))
    r3 = client.get("/api/v1/account")
    assert r3.status_code == 502
