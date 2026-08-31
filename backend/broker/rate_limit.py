"""
Rate limiting for Alpaca broker — leaky bucket + backoff + jitter.

Single shared limiter for all broker calls (25 req/min) per DOC.md §7.
- Thread-safe via threading.Lock for single-process.
- Multi-process safe via Redis when REDIS_URL is set (prevents ~25/min per worker bypass).
- Never logs secrets.

Falls back to in-memory bucket if Redis is not configured or unreachable.
"""

import logging
import os
import random
import time
import threading
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

# Config — DOC.md §7 safeguard: 25 req/min with headroom below Alpaca cap
RATE_PER_MINUTE = 25
BUCKET_CAPACITY = 25
REFILL_PER_SECOND = RATE_PER_MINUTE / 60.0  # 0.416...
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 8.0
REDIS_BUCKET_KEY = "rate_limit:bucket"  # hash with fields: tokens, ts

_logger = logging.getLogger(__name__)

# Lazy redis client
_redis_client = None
_redis_init_attempted = False
_redis_lock = threading.Lock()


def _get_redis_client():
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    with _redis_lock:
        if _redis_init_attempted:
            return _redis_client
        _redis_init_attempted = True
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            # Also try via settings to catch .env loaded via pydantic
            try:
                from backend.core.config import get_settings

                redis_url = get_settings().redis_url or os.getenv("REDIS_URL")
            except Exception:
                redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return None
        try:
            import redis  # type: ignore

            client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
            # Test connection
            client.ping()
            _redis_client = client
            _logger.info("Redis rate limiter enabled at %s", redis_url.split("@")[-1])
        except Exception as e:
            _logger.warning("Redis unavailable, falling back to in-memory bucket: %s", e)
            _redis_client = None
        return _redis_client


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
    - Thread-safe via lock for single-process.
    - Multi-process safe when Redis is available (uses Redis hash + Lua fallback).
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

    def _consume_redis(self, tokens: int = 1) -> None:
        """
        Redis-backed consume — atomic via Lua script if available, else WATCH/MULTI fallback.
        Stores hash {tokens: float, ts: monotonic-like timestamp} under REDIS_BUCKET_KEY.
        Uses time.time() for Redis-side timestamp to be comparable across processes.
        """
        r = _get_redis_client()
        if r is None:
            raise RuntimeError("Redis not available")

        # Lua script for atomic refill+consume
        lua = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_per_sec = tonumber(ARGV[2])
        local requested = tonumber(ARGV[3])
        local now = tonumber(ARGV[4])
        local data = redis.call('HMGET', key, 'tokens', 'ts')
        local tokens = tonumber(data[1])
        local ts = tonumber(data[2])
        if tokens == nil then tokens = capacity end
        if ts == nil then ts = now end
        local elapsed = now - ts
        if elapsed > 0 then
            tokens = math.min(capacity, tokens + elapsed * refill_per_sec)
        end
        if tokens >= requested then
            tokens = tokens - requested
            redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
            redis.call('EXPIRE', key, 120)
            return {1, tokens}
        else
            local needed = requested - tokens
            local retry_after = needed / refill_per_sec
            redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
            redis.call('EXPIRE', key, 120)
            return {0, retry_after}
        end
        """
        try:
            # Try Lua first
            result = r.eval(lua, 1, REDIS_BUCKET_KEY, self.capacity, self.refill_per_sec, tokens, time.time())
            if result[0] == 1:
                return
            else:
                raise RateLimitExceeded(retry_after=float(result[1]))
        except Exception as lua_err:
            # Fallback if Lua not supported or error — use WATCH
            if "unknown command" in str(lua_err).lower() or "eval" in str(lua_err).lower():
                pass
            else:
                # If it's RateLimitExceeded, re-raise
                if isinstance(lua_err, RateLimitExceeded):
                    raise
            # Fallback path: simple non-atomic best-effort (still better than per-process)
            try:
                with r.pipeline() as pipe:
                    while True:
                        try:
                            pipe.watch(REDIS_BUCKET_KEY)
                            data = pipe.hmget(REDIS_BUCKET_KEY, "tokens", "ts")
                            tokens_val = float(data[0]) if data[0] is not None else float(self.capacity)
                            ts_val = float(data[1]) if data[1] is not None else time.time()
                            elapsed = time.time() - ts_val
                            if elapsed > 0:
                                tokens_val = min(self.capacity, tokens_val + elapsed * self.refill_per_sec)
                            if tokens_val >= tokens:
                                tokens_val -= tokens
                                pipe.multi()
                                pipe.hmset(REDIS_BUCKET_KEY, {"tokens": tokens_val, "ts": time.time()})
                                pipe.expire(REDIS_BUCKET_KEY, 120)
                                pipe.execute()
                                return
                            else:
                                needed = tokens - tokens_val
                                retry_after = needed / self.refill_per_sec
                                # Update ts even on failure to account for refill
                                pipe.multi()
                                pipe.hmset(REDIS_BUCKET_KEY, {"tokens": tokens_val, "ts": time.time()})
                                pipe.expire(REDIS_BUCKET_KEY, 120)
                                pipe.execute()
                                raise RateLimitExceeded(retry_after=retry_after)
                        except RateLimitExceeded:
                            raise
                        except Exception as e:
                            # Watch error — retry loop once
                            if "WatchError" in type(e).__name__:
                                continue
                            raise
            except RateLimitExceeded:
                raise
            except Exception as e:
                _logger.warning("Redis fallback failed, using in-memory: %s", e)
                raise RuntimeError("Redis fallback failed") from e

    def consume(self, tokens: int = 1) -> None:
        """
        Consume tokens or raise RateLimitExceeded with retry_after.
        Tries Redis first if configured, falls back to in-memory.
        Thread-safe for in-memory path.
        """
        # Try Redis path if configured
        r = _get_redis_client()
        if r is not None:
            try:
                return self._consume_redis(tokens)
            except RuntimeError:
                pass  # fall through to in-memory
            except RateLimitExceeded:
                raise
            except Exception as e:
                _logger.warning("Redis consume failed, fallback to memory: %s", e)

        # In-memory fallback — thread-safe
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            needed = tokens - self._tokens
            retry_after = needed / self.refill_per_sec
            raise RateLimitExceeded(retry_after=retry_after)

    def remaining(self) -> float:
        r = _get_redis_client()
        if r is not None:
            try:
                data = r.hmget(REDIS_BUCKET_KEY, "tokens", "ts")
                if data[0] is not None:
                    tokens_val = float(data[0])
                    ts_val = float(data[1]) if data[1] is not None else time.time()
                    elapsed = time.time() - ts_val
                    if elapsed > 0:
                        tokens_val = min(self.capacity, tokens_val + elapsed * self.refill_per_sec)
                    return tokens_val
            except Exception:
                pass
        with self._lock:
            self._refill()
            return self._tokens

    def reset_for_tests(self) -> None:
        """Test helper — reset to full (both Redis and memory)."""
        with self._lock:
            self._tokens = float(self.capacity)
            self._last_refill = time.monotonic()
        r = _get_redis_client()
        if r is not None:
            try:
                r.delete(REDIS_BUCKET_KEY)
            except Exception:
                pass


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

    Note: Production retry in broker/client uses tenacity.wait_exponential_jitter (library, single source).
    This helper is kept for unit tests that assert jitter range 0.4-0.6 for attempt 0 and cap 8s.
    """
    delay = BASE_BACKOFF_SECONDS * (2**attempt)
    delay = min(delay, MAX_BACKOFF_SECONDS)
    jitter = random.uniform(-0.2 * delay, 0.2 * delay)
    return max(0.1, min(MAX_BACKOFF_SECONDS, delay + jitter))


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
    status = getattr(exc, "status_code", None)
    if status in (429, 502, 503, 504):
        return True
    return False
