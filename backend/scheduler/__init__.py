"""
Meanrev Autonomous Scheduler — PHASES.md Phase 12b.

Runs the trading graph unattended every SCHEDULER_INTERVAL_MIN minutes,
only when market is open (09:30-16:00 ET via broker/client.get_clock()),
with persistent state in logs/scheduler.json for crash resilience.

Usage:
  meanrev --scheduler                  # autonomous, --thread-id scoring-0831
  meanrev --scheduler --dry-run        # 1 tick dry-run, no live orders
  python -m backend.scheduler.runner   # direct

See backend/scheduler/runner.py for tick logic, market_hours.py for clock guard,
state.py for persistence.
"""

from .runner import run_scheduler, tick

__all__ = ["run_scheduler", "tick"]
