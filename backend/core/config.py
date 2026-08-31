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

    # Modal — proxy GLM endpoint (yaligarabhishek6--ep-glm-5-3-server)
    modal_token_id: Optional[str] = Field(default=None, alias="MODAL_TOKEN_ID")
    modal_token_secret: Optional[str] = Field(default=None, alias="MODAL_TOKEN_SECRET")
    modal_environment: str = Field(default="main", alias="MODAL_ENVIRONMENT")
    modal_endpoint_id: Optional[str] = Field(default=None, alias="MODAL_ENDPOINT_ID", description="Modal endpoint ID, e.g. ep-Ue2TlinRcqHUly46fnVi3Z")
    modal_endpoint_url: Optional[str] = Field(default=None, alias="MODAL_ENDPOINT_URL", description="Modal endpoint URL, e.g. https://yaligarabhishek6--ep-glm-5-3-server.us-west.modal.direct")
    modal_proxy_token_id: Optional[str] = Field(default=None, alias="MODAL_PROXY_TOKEN_ID", description="Modal proxy token ID for Authorization header")
    modal_proxy_token: Optional[str] = Field(default=None, alias="MODAL_PROXY_TOKEN", description="Modal proxy Bearer token — Authorization: Bearer <token>, never logged")

    # Direct fallback keys
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")

    # --- Model selectors — single source of truth, COMPULSORY from .env (no hardcoded model names) ---
    llm_model_market_research: Optional[str] = Field(
        default=None,
        alias="LLM_MODEL_MARKET_RESEARCH",
        description="Research agent model ID — compulsory from .env via .env.example. No hardcoded default in code.",
    )
    llm_model_strategy: Optional[str] = Field(
        default=None,
        alias="LLM_MODEL_STRATEGY",
        description="Strategy agent model ID — compulsory from .env via .env.example. No hardcoded default.",
    )
    llm_model_reporting: Optional[str] = Field(
        default=None,
        alias="LLM_MODEL_REPORTING",
        description="Reporting agent model ID — compulsory from .env via .env.example. No hardcoded default.",
    )

    # --- Risk thresholds — deterministic, no hardcoded logic in agents ---
    risk_max_position_pct: float = Field(default=0.15, alias="RISK_MAX_POSITION_PCT", description="Max single position as fraction of equity (0.15 = 15%)")
    risk_max_exposure_pct: float = Field(default=0.60, alias="RISK_MAX_EXPOSURE_PCT", description="Max gross exposure as fraction of equity (0.60 = 60%)")
    risk_daily_drawdown_pct: float = Field(default=0.03, alias="RISK_DAILY_DRAWDOWN_PCT", description="Daily drawdown trigger fraction (0.03 = 3% → auto-pause)")
    risk_peak_equity: Optional[float] = Field(default=None, alias="RISK_PEAK_EQUITY", description="Peak equity for drawdown calc; if None, uses portfolio_value as peak")

    # --- Execution modes — auto vs HITL ---
    execution_mode: str = Field(default="auto", alias="EXECUTION_MODE", description="Execution mode: auto (autonomous, no HITL) or hitl (human approval via interrupt)")
    hitl_enabled: bool = Field(default=False, alias="HITL_ENABLED", description="Enable Human-in-the-Loop for submit_order (requires checkpointer + thread_id)")

    # --- Scheduler (Phase 12b) ---
    scheduler_enabled: bool = Field(default=False, alias="SCHEDULER_ENABLED", description="Enable autonomous scheduler loop (tick every interval when market open)")
    scheduler_interval_min: int = Field(default=5, alias="SCHEDULER_INTERVAL_MIN", description="Scheduler tick interval minutes (1..60, default 5)")
    scheduler_thread_id: str = Field(default="scheduler", alias="SCHEDULER_THREAD_ID", description="LangGraph thread_id for scheduler ticks (prior_regime continuity)")
    scheduler_prompt: str = Field(default="Do Research On BTC/USD And Propose a Order", alias="SCHEDULER_PROMPT", description="Prompt for scheduler ticks")

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
        Resolve model ID for an agent using selector — compulsory from .env, no hardcoded fallback.
        Raises if selector missing, forcing .env update via .env.example.
        agent: research | strategy | reporting (aliases: market_research, market)
        """
        key = agent.lower().strip()
        val: Optional[str] = None
        if key in ("research", "market_research", "market"):
            val = self.llm_model_market_research
        elif key in ("strategy",):
            val = self.llm_model_strategy
        elif key in ("reporting", "report"):
            val = self.llm_model_reporting
        else:
            raise ValueError(f"Unknown agent model selector: {agent}")

        if not val or not val.strip():
            raise ValueError(
                f"LLM model for '{agent}' not set — define "
                f"{'LLM_MODEL_MARKET_RESEARCH' if key in ('research','market_research','market') else 'LLM_MODEL_STRATEGY' if key=='strategy' else 'LLM_MODEL_REPORTING'} "
                f"in .env (see .env.example). No hardcoded default."
            )
        return val.strip()

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
            endpoint_url = self.modal_endpoint_url or os.getenv("MODAL_ENDPOINT_URL") or "https://yaligarabhishek6--ep-glm-5-3-server.us-west.modal.direct"
            proxy_token = self.modal_proxy_token or os.getenv("MODAL_PROXY_TOKEN")
            token_id = self.modal_token_id or os.getenv("MODAL_TOKEN_ID")
            return {
                "provider": "modal",
                "api_key": proxy_token or token_id,  # for OpenAI-compatible init_chat_model: Authorization: Bearer <proxy_token>
                "base_url": endpoint_url,
                "token_id": token_id or os.getenv("MODAL_TOKEN_ID"),
                "token_secret": self.modal_token_secret or os.getenv("MODAL_TOKEN_SECRET"),
                "environment": self.modal_environment,
                "endpoint_id": self.modal_endpoint_id or os.getenv("MODAL_ENDPOINT_ID") or "ep-Ue2TlinRcqHUly46fnVi3Z",
                "endpoint_url": endpoint_url,
                "proxy_token_id": self.modal_proxy_token_id or os.getenv("MODAL_PROXY_TOKEN_ID"),
                "proxy_token": proxy_token,
            }
        return {"provider": provider}

    def is_llm_configured(self) -> bool:
        """True only if selected provider has its explicit credential available.
        Modal is considered configured if either token pair OR proxy token + endpoint is present.
        """
        cfg = self.llm_provider_config()
        if cfg["provider"] == "openrouter":
            return bool(cfg.get("api_key"))
        if cfg["provider"] == "groq":
            return bool(cfg.get("api_key"))
        if cfg["provider"] == "modal":
            has_tokens = bool(cfg.get("token_id") and cfg.get("token_secret"))
            has_proxy = bool(cfg.get("proxy_token") and cfg.get("endpoint_url"))
            return has_tokens or has_proxy
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
