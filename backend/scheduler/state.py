"""
Scheduler state persistence — logs/scheduler.json (gitignored) for 12b resilience.

Stores {last_run, next_run, run_count, last_status, last_error, thread_id, interval_min}
Atomic write via temp file + rename. Loaded on boot so scheduler does not duplicate ticks after crash.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.logging import log_event

STATE_DIR = Path(__file__).resolve().parents[1] / "logs"
STATE_FILE = STATE_DIR / "scheduler.json"

# Ensure logs dir exists
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_scheduler_state() -> Dict[str, Any]:
    """Load state from logs/scheduler.json, or defaults if missing/corrupt."""
    defaults: Dict[str, Any] = {
        "last_run": None,
        "next_run": None,
        "run_count": 0,
        "last_status": None,
        "last_error": None,
        "thread_id": None,
        "interval_min": 5,
        "updated_at": None,
    }
    if not STATE_FILE.exists():
        return defaults
    try:
        raw = STATE_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            # Empty file (e.g. after manual `> scheduler.json` or crash) — treat as missing, no warning
            return defaults
        data = json.loads(raw)
        # Merge defaults
        for k, v in defaults.items():
            data.setdefault(k, v)
        return data
    except json.JSONDecodeError as e:
        # Corrupt JSON — backup and reset, warning once
        try:
            backup = STATE_FILE.with_suffix(".json.corrupt")
            STATE_FILE.rename(backup)
            log_event("scheduler_state_corrupt_reset", level="warning", error=str(e)[:200], backup=str(backup))
        except Exception:
            log_event("scheduler_state_load_failed", level="warning", error=str(e)[:200])
        return defaults
    except Exception as e:
        log_event("scheduler_state_load_failed", level="warning", error=str(e)[:200])
        return defaults


def save_scheduler_state(
    last_run: Optional[str] = None,
    next_run: Optional[str] = None,
    run_count: Optional[int] = None,
    last_status: Optional[str] = None,
    last_error: Optional[str] = None,
    thread_id: Optional[str] = None,
    interval_min: Optional[int] = None,
) -> Dict[str, Any]:
    """Merge provided fields into state and atomically write to logs/scheduler.json."""
    state = load_scheduler_state()
    now = _now_iso()
    if last_run is not None:
        state["last_run"] = last_run
    if next_run is not None:
        state["next_run"] = next_run
    if run_count is not None:
        state["run_count"] = run_count
    if last_status is not None:
        state["last_status"] = last_status
    if last_error is not None:
        state["last_error"] = last_error
    if thread_id is not None:
        state["thread_id"] = thread_id
    if interval_min is not None:
        state["interval_min"] = interval_min
    state["updated_at"] = now

    # Atomic write
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(STATE_DIR), prefix="scheduler.", suffix=".tmp")
        os.close(tmp_fd)
        Path(tmp_path).write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        Path(tmp_path).replace(STATE_FILE)
    except Exception as e:
        log_event("scheduler_state_save_failed", level="warning", error=str(e)[:200])
        try:
            STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
    return state


def should_skip_duplicate(interval_min: int = 5) -> bool:
    """
    If last_run was < interval_min minutes ago, skip duplicate tick (idempotent on quick restarts).
    Returns True if should skip.
    """
    state = load_scheduler_state()
    last = state.get("last_run")
    if not last:
        return False
    try:
        last_s = str(last).replace("Z", "+00:00")
        last_dt = datetime.fromisoformat(last_s)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_dt).total_seconds()
        if elapsed < interval_min * 60 * 0.8:  # 80% of interval grace
            return True
        return False
    except Exception:
        return False
