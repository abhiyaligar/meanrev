"""
Structured JSON-line logger — DOC.md §5 core/logging.py

Every broker/API call appends one JSON line to logs/broker.jsonl (and stdout).
Never logs secrets. Uses stdlib logging with JSON formatter.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_FILE = LOG_DIR / "broker.jsonl"

# Ensure logs dir exists (has .gitkeep)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Configure stdlib logger — thread-safe init per VULN 9
_logger = logging.getLogger("broker")
_logger_lock = __import__("threading").Lock()
with _logger_lock:
    if not _logger.handlers:
        _logger.setLevel(logging.INFO)
        # File handler — JSON lines
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.INFO)
        _logger.addHandler(fh)
        # Stream handler for terminal (when running via uvicorn, uvicorn handles it anyway)
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        _logger.addHandler(sh)
        _logger.propagate = False


def _redact(obj: Any) -> Any:
    """Best-effort redaction of secret-like keys in logged payloads."""
    if isinstance(obj, dict):
        redacted: Dict[str, Any] = {}
        for k, v in obj.items():
            lk = k.lower()
            if any(s in lk for s in ["secret", "api_key", "apikey", "token", "password"]):
                redacted[k] = "***redacted***"
            else:
                redacted[k] = _redact(v)
        return redacted
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def log_event(event: str, level: str = "info", **fields: Any) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "level": level,
        **_redact(fields),
    }
    line = json.dumps(payload, ensure_ascii=False)
    if level == "error":
        _logger.error(line)
    elif level == "warning":
        _logger.warning(line)
    else:
        _logger.info(line)


def log_broker_call(endpoint: str, latency_ms: float, status: str, **extra: Any) -> None:
    log_event(
        "broker_call",
        endpoint=endpoint,
        latency_ms=round(latency_ms, 2),
        status=status,
        **extra,
    )
