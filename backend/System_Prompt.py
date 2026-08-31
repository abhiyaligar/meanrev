"""
Re-export of central prompts — single source of truth is backend/core/system_prompt.py per DOC.md §5.

This file exists for backward compatibility (import path `backend.System_Prompt` used in older modules).
All prompts are defined in `backend.core.system_prompt`; this module re-exports them so edits happen in one place.

Do NOT add new prompt strings here — add them to backend/core/system_prompt.py and import.
"""

from backend.core.system_prompt import (
    CLI_SYSTEM_PROMPT,
    EXECUTION_SYSTEM_PROMPT,
    GRAPH_REPORTING_PROMPT,
    GRAPH_RESEARCH_PROMPT,
    GRAPH_STRATEGY_PROMPT_TEMPLATE,
    REPORTING_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    RISK_SYSTEM_PROMPT,
    STRATEGY_SYSTEM_PROMPT,
    SYSTEM_PROMPTS,
    get_system_prompt,
)

__all__ = [
    "RESEARCH_SYSTEM_PROMPT",
    "STRATEGY_SYSTEM_PROMPT",
    "REPORTING_SYSTEM_PROMPT",
    "RISK_SYSTEM_PROMPT",
    "EXECUTION_SYSTEM_PROMPT",
    "CLI_SYSTEM_PROMPT",
    "GRAPH_RESEARCH_PROMPT",
    "GRAPH_STRATEGY_PROMPT_TEMPLATE",
    "GRAPH_REPORTING_PROMPT",
    "SYSTEM_PROMPTS",
    "get_system_prompt",
]
