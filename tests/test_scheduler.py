"""Scheduler — market guard, duplicate skip, persistence."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
def test_scheduler_skip_when_market_closed(tmp_path, monkeypatch):
    from backend.scheduler import market_hours, runner
    from backend.scheduler.state import STATE_FILE

    # Force fresh state
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    # Mock closed clock
    monkeypatch.setattr(market_hours, "get_market_clock", lambda: {"is_open": False, "timestamp": "2026-08-31T07:00:00-04:00", "next_open": "2026-08-31T09:30:00-04:00", "next_close": "2026-08-31T16:00:00-04:00"})
    # Ensure cache cleared
    market_hours._cache.clear()
    res = runner.tick(dry_run=True, thread_id="test-sched-closed", prompt="hi")
    assert res["skipped"] and res["reason"] == "market_closed"
    assert "next_open" in res
    # State next_run should be next_open
    from backend.scheduler.state import load_scheduler_state

    st = load_scheduler_state()
    assert st["next_run"] == "2026-08-31T09:30:00-04:00"


@pytest.mark.unit
def test_scheduler_tick_when_open(tmp_path, monkeypatch):
    from backend.scheduler import market_hours, runner
    from backend.scheduler.state import STATE_FILE

    if STATE_FILE.exists():
        STATE_FILE.unlink()
    # Mock open
    monkeypatch.setattr(market_hours, "get_market_clock", lambda: {"is_open": True, "timestamp": "2026-08-31T10:00:00-04:00", "next_open": "2026-08-31T09:30:00-04:00", "next_close": "2026-08-31T16:00:00-04:00"})
    market_hours._cache.clear()

    dummy = {"risk": {"decision": "no_trade"}, "execution": {"status": "skipped"}}
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = dummy

    with patch("backend.graph.build.build_graph", return_value=fake_graph):
        res = runner.tick(dry_run=True, thread_id="test-sched-open", prompt="test")
        assert not res["skipped"]
        assert res["result"] == dummy
        from backend.scheduler.state import load_scheduler_state

        st = load_scheduler_state()
        assert st["run_count"] == 1
        assert st["thread_id"] == "test-sched-open"


@pytest.mark.unit
def test_scheduler_duplicate_skip(monkeypatch):
    from backend.scheduler import market_hours, runner
    from backend.scheduler.state import STATE_FILE

    # Ensure state has recent last_run
    monkeypatch.setattr(market_hours, "get_market_clock", lambda: {"is_open": True, "timestamp": "2026-08-31T10:00:00-04:00", "next_open": "2026-08-31T09:30:00-04:00", "next_close": "2026-08-31T16:00:00-04:00"})
    market_hours._cache.clear()
    # First tick already done, second immediate should skip
    dummy = {"risk": {"decision": "no_trade"}, "execution": {"status": "skipped"}}
    fake_graph = MagicMock()
    fake_graph.invoke.return_value = dummy
    with patch("backend.graph.build.build_graph", return_value=fake_graph):
        # First tick
        runner.tick(dry_run=True, thread_id="dup-test", prompt="x")
        # Second tick immediately
        res2 = runner.tick(dry_run=True, thread_id="dup-test", prompt="x")
        assert res2["skipped"] and res2["reason"] == "duplicate"


@pytest.mark.unit
def test_scheduler_persistence_roundtrip(tmp_path, monkeypatch):
    from backend.scheduler.state import load_scheduler_state, save_scheduler_state, STATE_FILE

    if STATE_FILE.exists():
        STATE_FILE.unlink()
    s = save_scheduler_state(last_run="2026-08-31T10:00:00+00:00", next_run="2026-08-31T10:05:00+00:00", run_count=5, last_status="ok", thread_id="persist-test", interval_min=5)
    assert s["run_count"] == 5
    loaded = load_scheduler_state()
    assert loaded["run_count"] == 5
    assert loaded["thread_id"] == "persist-test"
