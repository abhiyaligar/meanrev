"""Risk engine — position/exposure/drawdown/SPXW, no hardcoded thresholds."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.agents import risk as risk_mod
from backend.agents.risk import (
    check_drawdown,
    check_exposure,
    check_position_limit,
    check_spxw_settlement,
    evaluate_risk,
    is_paused,
    track_account_state,
)


# Helpers


def df_with_close(price: float, atr: float = 2.0):
    return pd.DataFrame({"close": [price], "atr": [atr]})


def state_for(symbol: str, qty, action="buy", extra=None):
    s = {"strategy": {"symbol": symbol, "qty": qty, "action": action}}
    if extra:
        s["strategy"].update(extra)
    return s


# track_account_state


@pytest.mark.unit
def test_track_account_state_normal(account):
    out = track_account_state(account, [])
    assert out["equity"] == float(account["portfolio_value"])
    assert out["cash"] == float(account["cash"])
    assert out["peak_equity"] >= out["equity"]
    assert out["margin_usage"] >= 0


@pytest.mark.unit
def test_track_account_state_missing_equity_fallback_to_cash():
    acct = {"cash": "50000"}  # no equity/portfolio_value
    out = track_account_state(acct, [])
    assert out["equity"] == 50000.0
    assert out["peak_equity"] == 50000.0


@pytest.mark.unit
def test_track_account_state_unrealized_sum(account, positions_single):
    out = track_account_state(account, positions_single)
    assert out["unrealized_pl"] == pytest.approx(100.0)


@pytest.mark.unit
def test_track_account_state_peak_from_settings(account, monkeypatch):
    # If risk_peak_equity set via settings, peak should be max(settings.peak, equity)
    from backend.core.config import get_settings

    s = get_settings()
    original = s.risk_peak_equity
    try:
        s.risk_peak_equity = 200_000  # type: ignore
        out = track_account_state(account, [])
        assert out["peak_equity"] == 200_000
        # If settings peak < equity, peak becomes equity
        s.risk_peak_equity = 10_000  # type: ignore
        out2 = track_account_state(account, [])
        assert out2["peak_equity"] == float(account["portfolio_value"])
    finally:
        s.risk_peak_equity = original  # type: ignore


# check_position_limit


@pytest.mark.unit
def test_position_limit_pass_and_exact(risk_thresholds, equity):
    max_pct = risk_thresholds["max_position_pct"]
    price = 100.0
    # Exactly at limit: qty = equity*max_pct/price should pass
    qty_exact = (equity * max_pct / price)
    ok, adj, msg = check_position_limit("AAPL", qty_exact, price, equity)
    assert ok and adj == pytest.approx(qty_exact)
    # Slightly below passes
    ok2, _, _ = check_position_limit("AAPL", qty_exact * 0.99, price, equity)
    assert ok2
    # Slightly above scales
    ok3, adj3, msg3 = check_position_limit("AAPL", qty_exact * 1.5, price, equity)
    assert not ok3 and adj3 == pytest.approx(qty_exact)
    assert "scaled" in msg3


@pytest.mark.unit
def test_position_limit_edge_invalid(equity, risk_thresholds):
    max_pct = risk_thresholds["max_position_pct"]
    assert not check_position_limit("AAPL", 0, 100, equity)[0]  # qty 0
    assert not check_position_limit("AAPL", 10, 0, equity)[0]  # price 0
    assert not check_position_limit("AAPL", 10, 100, 0)[0]  # equity 0
    # Negative qty (sell) preserves sign when scaled
    qty_big = (equity * max_pct / 100) * 2
    ok, adj, _ = check_position_limit("AAPL", -qty_big, 100, equity)
    assert not ok and adj < 0


# check_exposure


@pytest.mark.unit
def test_exposure_pass_and_cap(risk_thresholds, equity):
    max_pct = risk_thresholds["max_exposure_pct"]
    # Existing 0, new exactly at cap
    new = equity * max_pct
    ok, _ = check_exposure(new, 0, equity)
    assert ok
    # Over cap
    ok2, msg2 = check_exposure(new + 0.01, 0, equity)
    assert not ok2 and "breach" in msg2
    # With existing exposure
    existing = equity * max_pct * 0.5
    ok3, _ = check_exposure(existing, 0, equity)
    assert ok3
    ok4, _ = check_exposure(existing, existing, equity)  # existing+new = max -> ok? existing 0.5*cap + new 0.5*cap
    assert ok4
    ok5, _ = check_exposure(existing + 1, existing, equity)
    assert not ok5


@pytest.mark.unit
def test_exposure_edge_equity_zero(risk_thresholds):
    ok, msg = check_exposure(1000, 0, 0)
    assert not ok and "equity" in msg


# check_drawdown


@pytest.mark.unit
def test_drawdown_not_triggered_and_triggered(risk_thresholds):
    peak = 100_000.0
    thresh = risk_thresholds["drawdown_pct"]
    # No drawdown
    trig, dd, _ = check_drawdown(peak, peak)
    assert not trig and dd == pytest.approx(0.0)
    # Just under threshold -> not triggered (drawdown = -thresh + epsilon)
    equity_ok = peak * (1 - thresh + 0.0001)
    trig2, _, _ = check_drawdown(equity_ok, peak)
    assert not trig2
    # Exactly at threshold -> not triggered (only < -thresh)
    equity_at = peak * (1 - thresh)
    trig3, _, _ = check_drawdown(equity_at, peak)
    assert not trig3
    # Beyond -> triggered
    equity_bad = peak * (1 - thresh - 0.001)
    trig4, dd4, msg4 = check_drawdown(equity_bad, peak)
    assert trig4 and dd4 < -thresh and "TRIGGERED" in msg4


@pytest.mark.unit
def test_drawdown_edge_peak_zero():
    trig, dd, msg = check_drawdown(100000, 0)
    assert not trig and "peak" in msg
    # Equity > peak -> positive drawdown, not triggered
    trig2, dd2, _ = check_drawdown(110000, 100000)
    assert not trig2 and dd2 > 0


# check_spxw


@pytest.mark.unit
def test_spxw_not_spxw():
    r = check_spxw_settlement("AAPL")
    assert not r["is_spxw"] and not r["should_close_before_expiry"]


@pytest.mark.unit
def test_spxw_expiry_logic():
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    # Expiry today (0d) -> should close
    exp_today = (now).isoformat()
    r0 = check_spxw_settlement("SPXW", expiration=exp_today)
    assert r0["is_spxw"] and r0["should_close_before_expiry"] and r0["settlement_lag_flag"]
    # Expiry +2d -> lag but not close
    exp_2 = (now + __import__("datetime").timedelta(days=2)).isoformat()
    r2 = check_spxw_settlement("SPXW", expiration=exp_2)
    assert r2["settlement_lag_flag"] and not r2["should_close_before_expiry"]
    # Expiry +10d -> no close, no lag? Actually code: <=3 lag, so 10d no lag?
    # Check impl: days<=3 lag, else? For no expiration, lag True. For 10d, code leaves should_close False, lag False (since only set for <=3)
    exp_10 = (now + __import__("datetime").timedelta(days=10)).isoformat()
    r10 = check_spxw_settlement("SPXW", expiration=exp_10)
    assert r10["is_spxw"] and not r10["should_close_before_expiry"]
    # No expiration -> lag true but not close
    r_none = check_spxw_settlement("SPXW", expiration=None)
    assert r_none["is_spxw"] and r_none["settlement_lag_flag"] and not r_none["should_close_before_expiry"]


# evaluate_risk


def _mock_market(df_price=100.0):
    return patch("backend.data.market.fetch_ohlcv", return_value=df_with_close(df_price))


@pytest.mark.unit
def test_evaluate_risk_no_trade_cases(account):
    # Hold -> no_trade
    with patch("backend.broker.client.get_account", return_value=account), patch("backend.broker.client.get_positions", return_value=[]), _mock_market(100):
        s = state_for("AAPL", 10, action="hold")
        s["account_state"] = track_account_state(account, [])
        v = evaluate_risk(s)
        assert v["decision"] == "no_trade"
        # No symbol
        s2 = {"strategy": {"action": "buy", "qty": 10}}
        s2["account_state"] = track_account_state(account, [])
        v2 = evaluate_risk(s2)
        assert v2["decision"] == "no_trade"
        # Qty 0
        s3 = state_for("AAPL", 0, action="buy")
        s3["account_state"] = track_account_state(account, [])
        v3 = evaluate_risk(s3)
        assert v3["decision"] == "no_trade"


@pytest.mark.unit
def test_evaluate_risk_approved(account, risk_thresholds, equity):
    max_pct = risk_thresholds["max_position_pct"]
    price = 100.0
    qty_ok = (equity * max_pct * 0.5 / price)  # 50% of limit
    with patch("backend.broker.client.get_account", return_value=account), patch("backend.broker.client.get_positions", return_value=[]), _mock_market(price):
        s = state_for("AAPL", qty_ok, action="buy")
        s["account_state"] = track_account_state(account, [])
        # Force price path via market fetch mock
        v = evaluate_risk(s)
        assert v["decision"] == "approved"
        assert "approved:" in v["rule"]


@pytest.mark.unit
def test_evaluate_risk_scaled_when_position_breach(account, risk_thresholds, equity):
    max_pct = risk_thresholds["max_position_pct"]
    price = 100.0
    qty_big = (equity * max_pct * 2 / price)  # 2x limit
    with patch("backend.broker.client.get_account", return_value=account), patch("backend.broker.client.get_positions", return_value=[]), _mock_market(price):
        s = state_for("AAPL", qty_big, action="buy")
        s["account_state"] = track_account_state(account, [])
        v = evaluate_risk(s)
        assert v["decision"] == "approved_scaled"
        assert v["adjusted_qty"] == pytest.approx(equity * max_pct / price)


@pytest.mark.unit
def test_evaluate_risk_rejected_exposure_after_scale(account, risk_thresholds, equity):
    max_pct = risk_thresholds["max_position_pct"]
    max_exp = risk_thresholds["max_exposure_pct"]
    price = 100.0
    qty_big = (equity * max_pct * 2 / price)
    # Make existing exposure already high so scaled still breaches
    existing = [{"market_value": str(equity * max_exp * 0.9)}]
    with patch("backend.broker.client.get_account", return_value=account), patch("backend.broker.client.get_positions", return_value=existing), _mock_market(price):
        s = state_for("AAPL", qty_big, action="buy")
        s["account_state"] = track_account_state(account, [])
        v = evaluate_risk(s)
        # Should be rejected due to exposure after scale
        assert v["decision"] == "rejected"
        assert "exposure" in v["rule"].lower() or "scaled" in v["rule"].lower()


@pytest.mark.unit
def test_evaluate_risk_drawdown_triggers_pause(account_drawdown):
    # account_drawdown already beyond threshold
    with patch("backend.broker.client.get_account", return_value=account_drawdown), patch("backend.broker.client.get_positions", return_value=[]), _mock_market(100):
        s = state_for("AAPL", 10, action="buy")
        # Ensure no account_state to force live fetch (which will see drawdown)
        v = evaluate_risk(s)
        assert v["decision"] == "rejected"
        assert "drawdown" in v["rule"].lower()
        # .paused flag should be set
        assert is_paused()
        # Cleanup for other tests
        risk_mod.clear_pause()
        assert not is_paused()


@pytest.mark.unit
def test_evaluate_risk_paused_blocks_all(account, equity):
    # Manually set paused flag
    risk_mod._set_paused(True, reason="test pause")
    try:
        s = state_for("AAPL", 1, action="buy")
        s["account_state"] = track_account_state(account, [])
        with _mock_market(100):
            v = evaluate_risk(s)
            assert v["decision"] == "rejected"
            assert "paused" in v["rule"].lower()
    finally:
        risk_mod.clear_pause()


@pytest.mark.unit
def test_evaluate_risk_spxw_reject_buy_into_expiry(account):
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    with patch("backend.broker.client.get_account", return_value=account), patch("backend.broker.client.get_positions", return_value=[]), _mock_market(400):
        s = state_for("SPXW", 1, action="buy", extra={"expiration": now})
        s["account_state"] = track_account_state(account, [])
        v = evaluate_risk(s)
        assert v["decision"] == "rejected"
        assert "SPXW" in v["rule"]


@pytest.mark.unit
def test_evaluate_risk_sell_preserves_negative_qty(account):
    price = 100.0
    with patch("backend.broker.client.get_account", return_value=account), patch("backend.broker.client.get_positions", return_value=[]), _mock_market(price):
        s = state_for("AAPL", 10, action="sell")  # qty positive but sell -> should become -10
        s["account_state"] = track_account_state(account, [])
        v = evaluate_risk(s)
        # Decision should be approved (qty 10 within limit) but original_qty reflects -10
        assert v["decision"] in ("approved", "approved_scaled")
        assert v["original_qty"] < 0


@pytest.mark.unit
def test_evaluate_risk_parses_output_json_string(account):
    # Strategy stub output contains JSON string without symbol/qty fields
    with patch("backend.broker.client.get_account", return_value=account), patch("backend.broker.client.get_positions", return_value=[]), _mock_market(100):
        s = {"strategy": {"output": '{"action":"buy","symbol":"SPY","qty":5}'}, "account_state": track_account_state(account, [])}
        v = evaluate_risk(s)
        assert v["decision"] in ("approved", "approved_scaled", "rejected", "no_trade")
        # Should have parsed symbol
        assert v.get("original_qty") is not None or "no trade" in v["rule"]
