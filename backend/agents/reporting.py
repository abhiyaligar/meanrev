"""
Reporting agent — built-in LangChain create_agent per docs.

DOC.md §3: Reads structured JSON-line logs → human-readable summary.
Model ID from LLM_MODEL_REPORTING selector, not hardcoded.
Uses create_agent with no tools (reads files via injected context) or with a read_logs tool.

Docs: https://docs.langchain.com/oss/python/langchain/tools#basic-tool-definition
"""

from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.tools import tool

from backend.core.system_prompt import REPORTING_SYSTEM_PROMPT
from backend.core.utils import get_model_id, handle_tool_errors


@tool
def read_logs(lines: int = 50) -> str:
    """Read last N lines from structured JSON-line logs (logs/broker.jsonl). Args: lines 1..200 (default 50). Returns JSON lines as text."""
    try:
        lim = int(lines)
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(lim, 200))
    log_path = Path(__file__).resolve().parents[1] / "logs" / "broker.jsonl"
    if not log_path.exists():
        return "No logs yet — run the pipeline first."
    try:
        all_lines = log_path.read_text(encoding="utf-8").splitlines()
        tail = all_lines[-lim:]
        return "\n".join(tail)
    except Exception as e:
        return f"read_logs failed: {e}"


def _model_id() -> str:
    return get_model_id("reporting")


def get_reporting_agent():
    """
    Factory — returns built-in LangChain agent per docs.
    """
    return create_agent(
        model=_model_id(),
        tools=[read_logs],
        system_prompt=REPORTING_SYSTEM_PROMPT,
        middleware=[ToolCallLimitMiddleware(thread_limit=10, run_limit=5), handle_tool_errors],
    )


def reporting_agent(state: dict | None = None) -> dict:
    """
    Adapter for CLI /report and graph. Invokes built-in agent when LLM configured, else stub.
    Accepts optional state dict for compat; returns report dict.
    """
    try:
        from backend.core.config import get_settings

        if get_settings().is_llm_configured():
            agent = get_reporting_agent()
            result = agent.invoke({"messages": [{"role": "user", "content": "Generate the trading report from logs/broker.jsonl."}]})
            last = result.get("messages", [{}])[-1]
            content = getattr(last, "content", str(last)) if hasattr(last, "content") else str(last)
            return {"report": str(content), "agent": "reporting", "model": _model_id(), "built_in": True}
    except Exception:
        pass

    return {
        "report": "stub — LLM not configured or reporting agent unavailable. Read logs/broker.jsonl manually or run with LLM keys set.",
        "positions": [],
        "trades": [],
        "pnl": None,
        "stub": True,
        "model": _model_id(),
    }
