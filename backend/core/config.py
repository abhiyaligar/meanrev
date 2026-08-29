"""
Core configuration — single source of truth per DOC.md §5 core/config.py and Backend_Architecture.md §8.

Reads .env via pydantic-settings (supports both .env and backend/.env), never logs secrets.
Provides:
- Alpaca paper trading (ALPACA_API_*)
- LLM provider gateway (OpenRouter / Groq / Modal) via LLM_PROVIDER
- Model selectors per agent (LLM_MODEL_*) — system uses these values, no hardcoded model strings in agents
- Fallback direct keys (ANTHROPIC_API_KEY, OPENAI_API_KEY)
- Redis (REDIS_URL) for rate limiter pub/sub (optional, graceful fallback)

Usage:
    from backend.core.config import get_settings
    s = get_settings()
    s.alpaca_api_key / s.get_key() / s.get_secret()
    s.llm_provider / s.get_model("research") / s.resolve_llm()
"""

import os
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


LLMProvider = Literal["openrouter", "groq", "modal"]


class Settings(BaseSettings):
    # --- Alpaca Paper Trading ---
    alpaca_api_key: Optional[str] = Field(default=None, alias="ALPACA_API_KEY")
    alpaca_api_secret: Optional[str] = Field(default=None, alias="ALPACA_API_SECRET")
    alpaca_api_url: str = Field(default="https://paper-api.alpaca.markets", alias="ALPACA_API_URL")

    # --- LLM Provider selection ---
    llm_provider: LLMProvider = Field(default="openrouter", alias="LLM_PROVIDER")

    # OpenRouter — unified gateway
    openrouter_api_key: Optional[str] = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    # Groq
    groq_api_key: Optional[str] = Field(default=None, alias="GROQ_API_KEY")
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL")

    # Modal
    modal_token_id: Optional[str] = Field(default=None, alias="MODAL_TOKEN_ID")
    modal_token_secret: Optional[str] = Field(default=None, alias="MODAL_TOKEN_SECRET")
    modal_environment: str = Field(default="main", alias="MODAL_ENVIRONMENT")

    # Direct fallback keys
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")

    # --- Model selectors — single source of truth for all agents ---
    llm_model_market_research: str = Field(
        default="anthropic/claude-3.5-sonnet",
        alias="LLM_MODEL_MARKET_RESEARCH",
        description="Research agent model ID, e.g. anthropic/claude-3.5-sonnet",
    )
    llm_model_strategy: str = Field(
        default="openai/gpt-4o",
        alias="LLM_MODEL_STRATEGY",
        description="Strategy agent model ID, e.g. openai/gpt-4o",
    )
    llm_model_reporting: str = Field(
        default="openai/gpt-4o-mini",
        alias="LLM_MODEL_REPORTING",
        description="Reporting agent model ID, e.g. openai/gpt-4o-mini",
    )

    # --- Optional infra ---
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Alpaca helpers (backward compat with backend/app/config.py) ---

    def get_secret(self) -> Optional[str]:
        return self.alpaca_api_secret or os.getenv("ALPACA_API_SECRET")

    def get_key(self) -> Optional[str]:
        return self.alpaca_api_key or os.getenv("ALPACA_API_KEY")

    # --- LLM helpers ---

    def get_model(self, agent: str) -> str:
        """
        Resolve model ID for an agent using selector, no hardcoded fallback in agents.
        agent: research | strategy | reporting (aliases: market_research, market)
        """
        key = agent.lower().strip()
        if key in ("research", "market_research", "market"):
            return self.llm_model_market_research
        if key in ("strategy",):
            return self.llm_model_strategy
        if key in ("reporting", "report"):
            return self.llm_model_reporting
        raise ValueError(f"Unknown agent model selector: {agent}")

    def llm_provider_config(self) -> dict:
        """
        Returns provider-specific config dict for the selected LLM_PROVIDER.
        Agents should call this to resolve base_url / api_key without branching on env.
        """
        provider = self.llm_provider.lower()
        if provider == "openrouter":
            return {
                "provider": "openrouter",
                "api_key": self.openrouter_api_key or os.getenv("OPENROUTER_API_KEY"),
                "base_url": self.openrouter_base_url,
                "fallback_key": self.anthropic_api_key or self.openai_api_key,
            }
        if provider == "groq":
            return {
                "provider": "groq",
                "api_key": self.groq_api_key or os.getenv("GROQ_API_KEY"),
                "base_url": self.groq_base_url,
            }
        if provider == "modal":
            return {
                "provider": "modal",
                "token_id": self.modal_token_id or os.getenv("MODAL_TOKEN_ID"),
                "token_secret": self.modal_token_secret or os.getenv("MODAL_TOKEN_SECRET"),
                "environment": self.modal_environment,
            }
        return {"provider": provider}

    def is_llm_configured(self) -> bool:
        """True if selected provider has a credential available."""
        cfg = self.llm_provider_config()
        if cfg["provider"] == "openrouter":
            return bool(cfg.get("api_key") or cfg.get("fallback_key"))
        if cfg["provider"] == "groq":
            return bool(cfg.get("api_key"))
        if cfg["provider"] == "modal":
            return bool(cfg.get("token_id") and cfg.get("token_secret"))
        return False


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings singleton. Loads .env from both locations via pydantic-settings
    and also via python-dotenv for compatibility with app/config.py behavior.
    Never logs secrets.
    """
    try:
        from dotenv import load_dotenv

        for p in ("backend/.env", ".env"):
            if os.path.exists(p):
                load_dotenv(p, override=False)
    except Exception:
        pass
    return Settings()
