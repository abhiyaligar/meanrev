"""
Research agent — built-in LangChain create_agent per docs + Phase 10 enhancements.

DOC.md §3: Claude 3.5 Sonnet via LLM_PROVIDER, model ID from LLM_MODEL_MARKET_RESEARCH selector (compulsory from .env).
Uses create_agent (LangChain 1.0) with ToolNode + middleware, not custom dict logic.
Phase 10: token <1000 via tiktoken, output schema (sentiment/regime/catalyst), prior regime continuity.
"""

from typing import Any, Dict, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from pydantic import BaseModel, Field

from backend.core.logging import log_event
from backend.core.system_prompt import RESEARCH_SYSTEM_PROMPT
from backend.core.utils import get_model_id, handle_tool_errors
from backend.tools.news_tools import extract_keywords, fetch_news, get_macro_calendar


# --- 10.2: Output schema via Pydantic ---

class ResearchOutput(BaseModel):
    """Validated research output for strategy consumption."""

    sentiment: str = Field(default="neutral", description="bullish|bearish|neutral")
    conviction: float = Field(default=0.5, ge=0.0, le=1.0, description="0..1 confidence")
    regime: str = Field(default="neutral", description="risk_on|risk_off|neutral")
    catalyst_summary: str = Field(default="", description="Concise catalyst summary")
    sentiment_vector: Optional[list] = Field(default=None, description="Optional vector")


# Built-in middleware per docs: limits + error handling — single source via core/utils
_RESEARCH_MIDDLEWARE = [ToolCallLimitMiddleware(thread_limit=20, run_limit=10), handle_tool_errors]


def _model_id() -> str:
    return get_model_id("research")


# --- 10.1: Token counter <1000 via tiktoken ---

def count_tokens(text: str, model_id: Optional[str] = None) -> int:
    """Count tokens via tiktoken; fallback to cl100k_base and len//4."""
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
    """Truncate prompt to fit max_tokens, preserving head/tail."""
    tokens = count_tokens(prompt, model_id)
    if tokens <= max_tokens:
        return prompt
    max_chars = max_tokens * 4 - 200
    if len(prompt) <= max_chars:
        return prompt
    head = prompt[: max_chars // 2]
    tail = prompt[-max_chars // 2 :]
    truncated = head + "\n\n[...truncated for token limit...]\n\n" + tail
    log_event("research_token_truncated", original_tokens=tokens, truncated_tokens=count_tokens(truncated, model_id), max_tokens=max_tokens)
    return truncated


# --- 10.3: Prior regime continuity ---

def get_prior_regime(state: Dict[str, Any]) -> Optional[str]:
    """Extract prior regime from GraphState for continuity (via InMemorySaver thread_id persistence)."""
    # Direct from last research
    research = state.get("research") or {}
    if isinstance(research, dict) and research.get("regime"):
        reg = str(research["regime"]).lower()
        if reg in ("risk_on", "risk_off", "neutral"):
            return reg
    # Fallback from state key
    prior = state.get("prior_regime") or state.get("regime")
    if prior and str(prior).lower() in ("risk_on", "risk_off", "neutral"):
        return str(prior).lower()
    return None


def validate_research_output(output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize LLM output to ResearchOutput schema.
    Ensures sentiment and regime are in allowed sets, catalyst non-empty.
    """
    if not isinstance(output, dict):
        try:
            import json, re

            m = re.search(r"\{[^}]+\}", str(output))
            if m:
                output = json.loads(m.group(0))
            else:
                output = {"catalyst_summary": str(output)[:500]}
        except Exception:
            output = {"catalyst_summary": str(output)[:500]}

    # Normalize sentiment
    sentiment = str(output.get("sentiment", "neutral")).lower().strip()
    if sentiment not in ("bullish", "bearish", "neutral"):
        # Map synonyms
        if sentiment in ("positive", "up", "risk_on"):
            sentiment = "bullish"
        elif sentiment in ("negative", "down", "risk_off"):
            sentiment = "bearish"
        else:
            sentiment = "neutral"
            log_event("research_output_invalid", field="sentiment", value=output.get("sentiment"))

    # Normalize regime
    regime = str(output.get("regime", "neutral")).lower().strip()
    if regime not in ("risk_on", "risk_off", "neutral"):
        if regime in ("bullish", "positive"):
            regime = "risk_on"
        elif regime in ("bearish", "negative"):
            regime = "risk_off"
        else:
            regime = "neutral"
            log_event("research_output_invalid", field="regime", value=output.get("regime"))

    # Ensure catalyst_summary
    catalyst = str(output.get("catalyst_summary") or output.get("summary") or output.get("output") or "")[:800]
    if not catalyst:
        catalyst = "No catalyst summary — research tool data unavailable"
        log_event("research_output_invalid", field="catalyst_summary", value="empty")

    # Conviction
    try:
        conviction = float(output.get("conviction", 0.5))
        conviction = max(0.0, min(1.0, conviction))
    except (TypeError, ValueError):
        conviction = 0.5

    validated = ResearchOutput(
        sentiment=sentiment,
        conviction=conviction,
        regime=regime,
        catalyst_summary=catalyst,
        sentiment_vector=output.get("sentiment_vector"),
    )
    result = validated.model_dump()
    # Preserve extra fields like model, built_in
    for k in ("model", "built_in", "stub", "output"):
        if k in output:
            result[k] = output[k]
    return result


def get_research_agent():
    """
    Factory — returns built-in LangChain agent (create_agent) per docs.
    """
    try:
        return create_agent(
            model=_model_id(),
            tools=[fetch_news, get_macro_calendar, extract_keywords],
            system_prompt=RESEARCH_SYSTEM_PROMPT,
            middleware=_RESEARCH_MIDDLEWARE,
        )
    except Exception as e:
        if "langchain-openrouter" in str(e) or "langchain-groq" in str(e).lower():
            try:
                from langchain.chat_models import init_chat_model

                from backend.core.config import get_settings

                s = get_settings()
                cfg = s.llm_provider_config()
                model_name = s.get_model("research")
                fallback = init_chat_model(
                    model_name,
                    model_provider="openai",
                    api_key=cfg.get("api_key"),
                    base_url=cfg.get("base_url"),
                    temperature=0.5,
                )
                return create_agent(
                    model=fallback,
                    tools=[fetch_news, get_macro_calendar, extract_keywords],
                    system_prompt=RESEARCH_SYSTEM_PROMPT,
                    middleware=_RESEARCH_MIDDLEWARE,
                )
            except Exception as e2:
                log_event("research_agent_fallback_failed", level="warning", error=str(e2)[:200])
                return None
        log_event("research_agent_create_failed", level="warning", error=str(e)[:200])
        return None


def research_agent(state: dict) -> dict:
    """
    Adapter for StateGraph nodes that expect research_agent(state: dict) -> dict.
    Integrates 10.1-10.3: token limit, prior regime, output validation, fallback stub.
    """
    # 10.3: Inject prior regime continuity into prompt context
    prior_regime = get_prior_regime(state)
    prior_note = f" Prior regime: {prior_regime} (for continuity)." if prior_regime else ""

    # Build prompt with token limit (10.1)
    base_prompt = f"Perform market research: fetch news and macro calendar, output sentiment and regime as JSON.{prior_note}"
    full_prompt = f"{RESEARCH_SYSTEM_PROMPT}\n\n{base_prompt}"
    model_id = _model_id()
    if count_tokens(full_prompt, model_id) > 1000:
        # Truncate research system prompt tail if needed (should not happen with <1000 base, but for safety)
        base_prompt = enforce_token_limit(base_prompt, 800, model_id)
        full_prompt = enforce_token_limit(full_prompt, 1000, model_id)

    try:
        from backend.core.config import get_settings

        if get_settings().is_llm_configured():
            agent = get_research_agent()
            if agent is None:
                raise RuntimeError("research agent not available")
            msgs = state.get("messages", [{"role": "user", "content": base_prompt}])
            # If state already has messages, append base_prompt
            if state.get("messages") and base_prompt not in str(msgs):
                msgs = msgs + [{"role": "user", "content": base_prompt}]
            result = agent.invoke({"messages": msgs})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            content_str = str(content)

            # Try to parse JSON from LLM output and validate (10.2)
            parsed: Dict[str, Any] = {"output": content_str}
            try:
                import json, re

                m = re.search(r"\{[^}]+\}", content_str)
                if m:
                    parsed.update(json.loads(m.group(0)))
            except Exception:
                pass
            validated = validate_research_output(parsed)
            validated["output"] = content_str
            validated["agent"] = "research"
            validated["model"] = _model_id()
            validated["built_in"] = True
            validated["prior_regime"] = prior_regime
            state["research"] = validated
            # Preserve prior for next cycle
            state["prior_regime"] = validated["regime"]
            state["messages"] = result.get("messages", msgs)
            log_event("research_agent_ok", sentiment=validated["sentiment"], regime=validated["regime"], prior_regime=prior_regime)
            return state
    except Exception as e:
        log_event("research_agent_invoke_failed", level="warning", error=str(e)[:300])

    # Fallback stub — keeps pipeline runnable offline, with prior regime continuity
    stub_regime = prior_regime or "unknown"
    state.setdefault("research", {})
    state["research"] = {
        "sentiment": "neutral",
        "regime": stub_regime,
        "catalyst_summary": "stub — LLM not configured or research agent unavailable",
        "conviction": 0.5,
        "stub": True,
        "model": _model_id(),
        "prior_regime": prior_regime,
    }
    state["prior_regime"] = stub_regime
    return state
