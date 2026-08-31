"""
Scheduler runner — Phase 12b autonomous loop.

Tick every SCHEDULER_INTERVAL_MIN minutes (default 5), but only when market is open.
Uses APScheduler if available (jitter, coalesce, misfire_grace_time), else asyncio sleep loop fallback.

Each tick:
  1. Check market_hours.is_market_open() (cached 60s) — if closed, log skip and persist next_run
  2. Check should_skip_duplicate (crash resilience via logs/scheduler.json)
  3. Invoke build_graph().invoke({"messages":[{"role":"user","content": SCHEDULER_PROMPT}]}, thread_id)
  4. Persist last_run/next_run/run_count/last_status, log scheduler_tick

Run via:
  meanrev --scheduler
  meanrev --scheduler --dry-run
  python -m backend.scheduler.runner
  python -m backend.scheduler.runner --dry-run --once
"""

import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend.core.logging import log_event

try:
    from backend.core.config import get_settings

    _settings = get_settings()
    SCHEDULER_INTERVAL_MIN = int(getattr(_settings, "scheduler_interval_min", 5) or 5)
    SCHEDULER_THREAD_ID = str(getattr(_settings, "scheduler_thread_id", "scheduler") or "scheduler")
    SCHEDULER_PROMPT = str(getattr(_settings, "scheduler_prompt", "Do Research On BTC/USD And Propose a Order") or "Do Research On BTC/USD And Propose a Order")
except Exception:
    SCHEDULER_INTERVAL_MIN = 5
    SCHEDULER_THREAD_ID = "scheduler"
    SCHEDULER_PROMPT = "Do Research On BTC/USD And Propose a Order"


def _resolve_prompt_and_thread(dry_run: bool = False) -> tuple[str, str, int]:
    """Resolve prompt, thread_id, interval from config (live) with env overrides."""
    try:
        from backend.core.config import get_settings

        s = get_settings()
        prompt = str(getattr(s, "scheduler_prompt", SCHEDULER_PROMPT) or SCHEDULER_PROMPT)
        thread_id = str(getattr(s, "scheduler_thread_id", SCHEDULER_THREAD_ID) or SCHEDULER_THREAD_ID)
        interval = int(getattr(s, "scheduler_interval_min", SCHEDULER_INTERVAL_MIN) or SCHEDULER_INTERVAL_MIN)
        # Clamp interval 1..60
        interval = max(1, min(60, interval))
        return prompt, thread_id, interval
    except Exception:
        return SCHEDULER_PROMPT, SCHEDULER_THREAD_ID, SCHEDULER_INTERVAL_MIN


def tick(dry_run: bool = False, thread_id: Optional[str] = None, prompt: Optional[str] = None) -> Dict[str, Any]:
    """
    Single scheduler tick — market guard + graph invoke + persistence.

    Returns {"skipped": bool, "reason": str, "result": dict} or {"error": str}.
    """
    from backend.scheduler.market_hours import is_market_open
    from backend.scheduler.state import load_scheduler_state, save_scheduler_state, should_skip_duplicate

    # Resolve config
    cfg_prompt, cfg_thread, cfg_interval = _resolve_prompt_and_thread(dry_run=dry_run)
    use_prompt = prompt or cfg_prompt
    use_thread = thread_id or cfg_thread
    interval = cfg_interval

    # 1. Market hours guard
    mh = is_market_open()
    if not mh.get("is_open"):
        nxt = mh.get("next_open") or "unknown"
        log_event("scheduler_skip_closed", level="info", next_open=str(nxt), now_iso=mh.get("now_iso"))
        # Persist next_run as next_open
        try:
            save_scheduler_state(next_run=str(nxt), thread_id=use_thread, interval_min=interval)
        except Exception:
            pass
        return {"skipped": True, "reason": "market_closed", "next_open": str(nxt), "market_hours": mh}

    # 2. Duplicate guard (crash resilience)
    if should_skip_duplicate(interval_min=interval):
        state = load_scheduler_state()
        log_event("scheduler_skip_duplicate", level="info", last_run=str(state.get("last_run")), interval_min=interval)
        return {"skipped": True, "reason": "duplicate", "last_run": str(state.get("last_run"))}

    # 3. Enforce dry-run: if dry_run, we still invoke graph but execution will be dry_run_no_hitl
    #    (execution_agent respects dry_run via state flag or via EXECUTION_MODE). For scheduler dry-run,
    #    we set a flag in state.
    start = time.monotonic()
    try:
        from backend.graph.build import build_graph

        g = build_graph()
        state_in: Dict[str, Any] = {"messages": [{"role": "user", "content": use_prompt}]}
        if dry_run:
            state_in["dry_run"] = True
            # Also hint execution to stay dry
            state_in["scheduler_dry_run"] = True
        result = g.invoke(state_in, config={"configurable": {"thread_id": use_thread}})

        latency_ms = (time.monotonic() - start) * 1000
        risk = result.get("risk", {}) if isinstance(result, dict) else {}
        execution = result.get("execution", {}) if isinstance(result, dict) else {}
        risk_decision = str(risk.get("decision", "unknown"))
        exec_status = str(execution.get("status", "unknown"))
        last_status = f"risk:{risk_decision} exec:{exec_status}"

        # 4. Persist
        now_iso = datetime.now(timezone.utc).isoformat()
        next_run_iso = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat()
        prior = load_scheduler_state()
        new_count = int(prior.get("run_count", 0) or 0) + 1
        save_scheduler_state(
            last_run=now_iso,
            next_run=next_run_iso,
            run_count=new_count,
            last_status=last_status,
            last_error=None,
            thread_id=use_thread,
            interval_min=interval,
        )
        log_event(
            "scheduler_tick",
            level="info",
            run_count=new_count,
            latency_ms=round(latency_ms, 2),
            last_status=last_status,
            thread_id=use_thread,
            next_run=next_run_iso,
            dry_run=dry_run,
        )
        return {"skipped": False, "result": result, "latency_ms": round(latency_ms, 2), "market_hours": mh}

    except Exception as e:
        err = str(e)[:500]
        log_event("scheduler_tick_error", level="warning", error=err, thread_id=use_thread, dry_run=dry_run)
        # Persist error
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            prior = load_scheduler_state()
            save_scheduler_state(last_run=now_iso, last_error=err, last_status="error", thread_id=use_thread)
        except Exception:
            pass
        return {"skipped": False, "error": err}


def run_scheduler(dry_run: bool = False, once: bool = False, thread_id: Optional[str] = None, prompt: Optional[str] = None) -> None:
    """
    Run scheduler loop — APScheduler if available, else asyncio fallback.

    Args:
        dry_run: if True, each tick invokes graph with dry_run flag (no live orders)
        once: if True, run single tick then return (for tests / --dry-run --once)
        thread_id/prompt: override config
    """
    cfg_prompt, cfg_thread, cfg_interval = _resolve_prompt_and_thread(dry_run=dry_run)
    use_prompt = prompt or cfg_prompt
    use_thread = thread_id or cfg_thread
    interval = cfg_interval

    log_event("scheduler_start", level="info", interval_min=interval, thread_id=use_thread, prompt=use_prompt[:100], dry_run=dry_run, once=once)

    if once:
        res = tick(dry_run=dry_run, thread_id=use_thread, prompt=use_prompt)
        log_event("scheduler_once_complete", result=str(res)[:500])
        return

    # Try APScheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler = BackgroundScheduler(timezone="UTC", daemon=False)

        # Wrap tick for scheduler
        def _job():
            try:
                tick(dry_run=dry_run, thread_id=use_thread, prompt=use_prompt)
            except Exception as e:
                log_event("scheduler_job_error", level="warning", error=str(e)[:200])

        trigger = IntervalTrigger(minutes=interval, jitter=30)
        scheduler.add_job(
            _job,
            trigger=trigger,
            id="meanrev_scheduler_tick",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )
        scheduler.start()
        log_event("scheduler_apscheduler_started", interval_min=interval, jitter=30, misfire_grace_time=300)

        # Also run immediate tick on start if market open (so Aug 31 09:30 does not wait 5min)
        try:
            tick(dry_run=dry_run, thread_id=use_thread, prompt=use_prompt)
        except Exception as e:
            log_event("scheduler_initial_tick_error", level="warning", error=str(e)[:200])

        # Block forever
        try:
            import time as _time

            while True:
                _time.sleep(60)
        except KeyboardInterrupt:
            log_event("scheduler_stop", level="info", reason="KeyboardInterrupt")
            scheduler.shutdown(wait=False)

        return

    except ImportError as e:
        log_event("scheduler_apscheduler_missing", level="warning", error=str(e)[:200], fallback="asyncio")

    # Fallback: asyncio sleep loop (no extra dep)
    async def _async_loop():
        # Immediate tick
        try:
            tick(dry_run=dry_run, thread_id=use_thread, prompt=use_prompt)
        except Exception as e:
            log_event("scheduler_initial_tick_error", level="warning", error=str(e)[:200])
        while True:
            try:
                await asyncio.sleep(interval * 60)
                tick(dry_run=dry_run, thread_id=use_thread, prompt=use_prompt)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_event("scheduler_async_error", level="warning", error=str(e)[:200])

    try:
        asyncio.run(_async_loop())
    except KeyboardInterrupt:
        log_event("scheduler_stop", level="info", reason="KeyboardInterrupt")


def main():
    parser = argparse.ArgumentParser(description="Meanrev Autonomous Scheduler — Phase 12b")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run: no live orders, only logs")
    parser.add_argument("--once", action="store_true", help="Run single tick then exit (for tests)")
    parser.add_argument("--thread-id", type=str, default=None, help="Override SCHEDULER_THREAD_ID")
    parser.add_argument("--prompt", type=str, default=None, help="Override SCHEDULER_PROMPT")
    args = parser.parse_args()
    run_scheduler(dry_run=args.dry_run, once=args.once, thread_id=args.thread_id, prompt=args.prompt)


if __name__ == "__main__":
    main()
