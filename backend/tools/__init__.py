"""
Tools package — re-exports all LangChain @tool wrappers.

Research via langchain-docs MCP: @tool decorator + type hints + docstring defines schema;
create_agent(model="openrouter:...") wires tools via ToolNode.
All tools respect 25/min bucket, 30s timeout, and LLM_MODEL_* selectors from core/config.
"""

from .alpaca_cli_tool import ALPACA_CLI_TOOLS, alpaca_cli_account, alpaca_cli_clock, alpaca_cli_orders, alpaca_cli_positions
from .broker_tools import (
    cancel_all_orders,
    cancel_order,
    get_account,
    get_clock,
    get_orders,
    get_positions,
    modify_order,
    set_stop_loss,
    submit_order,
)
from .market_tools import align_timeframes_tool, detect_arbitrage, get_market_snapshot, get_ohlcv, get_option_chain
from .mcp_tools import MCP_TOOLS, mcp_get_account, mcp_get_clock, mcp_get_orders, mcp_get_positions
from .news_tools import extract_keywords, fetch_news, get_macro_calendar

# Flat list for create_agent(tools=TOOLS)
TOOLS = [
    get_account,
    get_positions,
    get_orders,
    get_clock,
    submit_order,
    set_stop_loss,
    modify_order,
    cancel_order,
    cancel_all_orders,
    get_ohlcv,
    get_market_snapshot,
    get_option_chain,
    align_timeframes_tool,
    detect_arbitrage,
    fetch_news,
    get_macro_calendar,
    extract_keywords,
    # Phase 12 — Alpaca CLI (subprocess) + MCP Server (via mcp bridge), both with broker fallback
    alpaca_cli_account,
    alpaca_cli_positions,
    alpaca_cli_orders,
    alpaca_cli_clock,
    mcp_get_account,
    mcp_get_positions,
    mcp_get_orders,
    mcp_get_clock,
]

# Sub-groupings for per-agent wiring
BROKER_TOOLS = [get_account, get_positions, get_orders, get_clock]
# Sensitive write tools — require HumanInTheLoopMiddleware (all order-mutating)
BROKER_WRITE_TOOLS = [submit_order, set_stop_loss, modify_order, cancel_order, cancel_all_orders]
MARKET_TOOLS = [get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, detect_arbitrage]
NEWS_TOOLS = [fetch_news, get_macro_calendar, extract_keywords]
ALPACA_CLI_TOOLS = ALPACA_CLI_TOOLS
MCP_TOOLS = MCP_TOOLS

__all__ = [
    "TOOLS",
    "BROKER_TOOLS",
    "BROKER_WRITE_TOOLS",
    "MARKET_TOOLS",
    "NEWS_TOOLS",
    "ALPACA_CLI_TOOLS",
    "MCP_TOOLS",
    "get_account",
    "get_positions",
    "get_orders",
    "get_clock",
    "submit_order",
    "set_stop_loss",
    "modify_order",
    "cancel_order",
    "cancel_all_orders",
    "get_ohlcv",
    "get_market_snapshot",
    "get_option_chain",
    "align_timeframes_tool",
    "detect_arbitrage",
    "fetch_news",
    "get_macro_calendar",
    "extract_keywords",
    "alpaca_cli_account",
    "alpaca_cli_positions",
    "alpaca_cli_orders",
    "alpaca_cli_clock",
    "mcp_get_account",
    "mcp_get_positions",
    "mcp_get_orders",
    "mcp_get_clock",
]
