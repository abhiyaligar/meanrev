"""
Rate limiting for Alpaca broker — leaky bucket + backoff + jitter.

Single shared limiter for all broker calls (25 req/min) per DOC.md §7.
Thread-safe for uvicorn workers; uses monotonic clock.
Never logs secrets.
"""

import random
import time
import threading
from typing import Callable, TypeVar

T = TypeVar("T")

# Config — DOC.md §7 safeguard: 25 req/min with headroom below Alpaca cap
RATE_PER_MINUTE = 25
BUCKET_CAPACITY = 25
REFILL_PER_SECOND = RATE_PER_MINUTE / 60.0  # 0.416...
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 8.0


class RateLimitExceeded(Exception):
    """Raised when bucket is empty and caller should retry after retry_after."""

    def __init__(self, retry_after: float):
        super().__init__(f"Rate limit exceeded. Retry after {retry_after:.2f}s")
        self.retry_after = retry_after


class TokenBucket:
    """
    Leaky-bucket / token-bucket targeting 25 req/min.
    - Capacity = 25 tokens.
    - Refill at 25/60 per second.
    - Single shared instance guarded by a lock.
    """

    def __init__(self, capacity: int = BUCKET_CAPACITY, refill_per_sec: float = REFILL_PER_SECOND):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_sec)
            self._last_refill = now

    def consume(self, tokens: int = 1) -> None:
        """
        Consume tokens or raise RateLimitExceeded with retry_after.
        Thread-safe.
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            # not enough tokens — compute time to next token
            needed = tokens - self._tokens
            retry_after = needed / self.refill_per_sec
            raise RateLimitExceeded(retry_after=retry_after)

    def remaining(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def reset_for_tests(self) -> None:
        """Test helper — reset to full."""
        with self._lock:
            self._tokens = float(self.capacity)
            self._last_refill = time.monotonic()


# Shared singleton — import and use directly
bucket = TokenBucket()


def with_rate_limit(fn: Callable[..., T], *args, **kwargs) -> T:
    """
    Call fn with rate-limit enforcement. Raises RateLimitExceeded if bucket empty.
    Caller (broker/client) decides whether to surface as 429 or to wait.
    """
    bucket.consume(1)
    return fn(*args, **kwargs)


def backoff_delay(attempt: int) -> float:
    """
    Exponential backoff with jitter: base * 2**attempt ± 20% jitter.
    attempt 0 → ~0.5s, 1 → ~1.0s, 2 → ~2.0s, capped at MAX_BACKOFF_SECONDS.
    """
    delay = BASE_BACKOFF_SECONDS * (2**attempt)
    delay = min(delay, MAX_BACKOFF_SECONDS)
    jitter = random.uniform(-0.2 * delay, 0.2 * delay)
    return max(0.1, delay + jitter)


def is_retryable_exception(exc: Exception) -> bool:
    """
    Retryable: 429, 5xx, timeouts. Determined by string/inspection
    so we don't hard-couple to alpaca-py exception hierarchy.
    """
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    retry_names = {"retryerror", "timeout", "timedout", "serviceunavailable"}
    if any(k in name for k in retry_names):
        return True
    if any(k in msg for k in ["429", "rate limit", "too many requests", "timeout", "timed out", "503", "504", "502"]):
        return True
    # alpaca-py wraps HTTP errors with status_code attr
    status = getattr(exc, "status_code", None)
    if status in (429, 502, 503, 504):
        return True
    return False
