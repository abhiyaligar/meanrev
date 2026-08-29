"""
Core utils — single source for duplicate helpers per DRY.

Consolidates functions that were duplicated across agents/graph/tools:
- get_model_id() — was 4x duplicated in research/strategy/reporting/graph
- handle_tool_errors — was 3x duplicated via @wrap_tool_call
- normalize_symbol — was duplicated in broker/client, tools, data
- clamp_limit — was duplicated in broker/client, tools, data
"""

from typing import Optional

from langchain.agents.middleware import wrap_tool_call
from langchain.messages import ToolMessage

from backend.core.config import get_settings


def get_model_id(agent: str) -> str:
    """
    Resolve model ID for agent via LLM_MODEL_* selectors — compulsory from .env.
    Single implementation replaces 4 duplicated _model_id() in agents/graph.
    Handles provider prefix (openrouter:..., groq:..., modal:...) and missing case.
    """
    try:
        s = get_settings()
        provider = s.llm_provider
        model = s.get_model(agent)
        if ":" in model and model.split(":")[0] in ("openrouter", "groq", "modal", "openai", "anthropic", "google_genai"):
            return model
        return model if provider == "modal" else f"{provider}:{model}"
    except Exception as e:
        return f"missing:{str(e)[:60]}"


@wrap_tool_call
def handle_tool_errors(request, handler):
    """
    Single tool error handler for all agents — replaces 3 duplicated definitions.
    Per docs: wrap_tool_call with ToolMessage on invalid inputs, re-raise others.
    """
    try:
        return handler(request)
    except Exception as e:
        return ToolMessage(content=f"Tool error: Please check input and try again. ({str(e)})", tool_call_id=request.tool_call["id"])


def normalize_symbol(symbol: Optional[str]) -> Optional[str]:
    """
    Normalize symbol to upper-case stripped, or None if empty.
    Replaces duplicated `symbol.strip().upper()` logic in broker/tools/data.
    """
    if not symbol or not symbol.strip():
        return None
    return symbol.strip().upper()


def normalize_symbols(symbols: Optional[str]) -> Optional[str]:
    """
    Normalize comma-separated symbols string to upper-case comma list, or None.
    Used for get_orders symbols filter.
    """
    if not symbols or not symbols.strip():
        return None
    parts = [p.strip().upper() for p in symbols.split(",") if p.strip()]
    return ",".join(parts) if parts else None


def clamp_limit(limit: object, default: int = 50, min_val: int = 1, max_val: int = 500) -> int:
    """
    Clamp limit to [min_val, max_val] with default fallback.
    Replaces duplicated limit clamp in broker/client, tools, data.
    """
    try:
        lim = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        lim = default
    if lim < min_val:
        lim = min_val
    return min(lim, max_val)


def clamp_limit_strict(limit: object, default: int = 20, min_val: int = 1, max_val: int = 100) -> int:
    """Stricter clamp for news/options (default 20, max 100)."""
    return clamp_limit(limit, default=default, min_val=min_val, max_val=max_val)


def count_tokens(text: str, model_id: Optional[str] = None) -> int:
    """Count tokens via tiktoken (library, not custom len//4). Replaces 2x duplicated in research/strategy."""
    if not text:
        return 0
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model_id or "gpt-4o")
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def enforce_token_limit(prompt: str, max_tokens: int = 1000, model_id: Optional[str] = None) -> str:
    """Truncate prompt to fit max_tokens, preserving head/tail. Replaces 2x duplicated in research/strategy."""
    tokens = count_tokens(prompt, model_id)
    if tokens <= max_tokens:
        return prompt
    max_chars = max_tokens * 4 - 200
    if len(prompt) <= max_chars:
        return prompt
    head = prompt[: max_chars // 2]
    tail = prompt[-max_chars // 2 :]
    truncated = head + "\n\n[...truncated for token limit...]\n\n" + tail
    # Log via core/logging to avoid circular
    try:
        from backend.core.logging import log_event

        log_event("token_truncated", original_tokens=tokens, truncated_tokens=count_tokens(truncated, model_id), max_tokens=max_tokens)
    except Exception:
        pass
    return truncated


class TTLCache:
    """Library-backed TTL cache via cachetools if available, else custom dict fallback. Replaces 2x duplicated _CACHE in market/news."""

    def __init__(self, maxsize: int = 200, ttl: int = 60):
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: dict = {}
        # Try cachetools if installed
        try:
            import cachetools  # type: ignore

            self._cache = cachetools.TTLCache(maxsize=maxsize, ttl=ttl)
            self._use_cachetools = True
        except ImportError:
            self._cache = {}
            self._use_cachetools = False
            self._store = {}

    def get(self, key: str):
        if self._use_cachetools:
            return self._cache.get(key)
        else:
            import time

            entry = self._store.get(key)
            if not entry:
                return None
            ts, val = entry
            if time.time() - ts > self.ttl:
                self._store.pop(key, None)
                return None
            return val

    def set(self, key: str, value):
        if self._use_cachetools:
            self._cache[key] = value
        else:
            import time

            self._store[key] = (time.time(), value)
            if len(self._store) > self.maxsize:
                oldest = min(self._store, key=lambda k: self._store[k][0])
                self._store.pop(oldest, None)

    def clear(self):
        if self._use_cachetools:
            self._cache.clear()
        else:
            self._store.clear()
