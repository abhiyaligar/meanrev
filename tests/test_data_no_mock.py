"""Data layer — no mock fallbacks, returns empty with log."""

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.mark.unit
def test_fetch_ohlcv_unknown_returns_empty_with_log(caplog=None):
    from backend.data.market import fetch_ohlcv

    # Unknown symbol should return empty DataFrame, no mock bars, with No data available log
    # Mock data client to return empty
    with patch("backend.data.market._get_data_client", return_value=None):
        # Also patch bucket to avoid rate limit
        with patch("backend.broker.rate_limit.bucket.consume", return_value=None):
            df = fetch_ohlcv("UNKNOWN_FAKE_XYZ", limit=5)
            assert isinstance(df, pd.DataFrame)
            assert df.empty


@pytest.mark.unit
def test_fetch_ohlcv_empty_logs_no_mock(monkeypatch):
    from backend.data.market import fetch_ohlcv

    # Mock StockHistoricalDataClient to return empty bars
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = MagicMock(data={})
    with patch("backend.data.market._get_data_client", return_value=mock_client):
        with patch("backend.broker.rate_limit.bucket.consume", return_value=None):
            df = fetch_ohlcv("FAKE", timeframe="1Day", limit=5)
            assert df.empty


@pytest.mark.unit
def test_fetch_option_chain_no_data(monkeypatch):
    from backend.data.market import fetch_option_chain

    with patch("backend.data.market._get_data_client", return_value=None):
        res = fetch_option_chain("AAPL", limit=2)
        assert res == []


@pytest.mark.unit
def test_fetch_news_no_data(monkeypatch):
    from backend.data import news as news_mod

    # Ensure no creds -> returns empty
    with patch("backend.core.config.get_settings") as mock_cfg:
        mock_s = MagicMock()
        mock_s.get_key.return_value = None
        mock_s.get_secret.return_value = None
        mock_cfg.return_value = mock_s
        # Clear cache
        from backend.data.news import _NEWS_CACHE

        _NEWS_CACHE.clear()
        res = news_mod.fetch_news(symbols=["AAPL"], limit=5)
        assert res == []


@pytest.mark.unit
def test_get_macro_calendar_no_data(monkeypatch):
    from backend.data.news import get_macro_calendar, _MACRO_CACHE

    _MACRO_CACHE.clear()
    res = get_macro_calendar()
    assert res == []


@pytest.mark.unit
def test_strategy_ensure_options_no_data():
    from backend.agents.strategy import ensure_options_in_decision

    # No option data available -> should inject error leg, not mock leg
    with patch("backend.agents.strategy.get_option_chain") as mock_chain:
        mock_chain.invoke.return_value = json.dumps({"chain": []})
        decision = {"symbol": "AAPL", "action": "buy", "qty": 1}
        out = ensure_options_in_decision(decision, underlying="AAPL")
        assert "option_leg" in out
        assert "No data available" in str(out["option_leg"])


@pytest.mark.unit
def test_tools_return_no_mock_json(monkeypatch):
    from backend.tools.broker_tools import set_stop_loss

    # Crypto stop should return CryptoStopNotSupported error, not mock order
    res_str = set_stop_loss.invoke({"symbol": "BTC/USD", "stop_price": 50000, "qty": 0.01})
    res = json.loads(res_str)
    assert "error" in res
    assert res["type"] == "CryptoStopNotSupported"
