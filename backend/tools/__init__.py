"""
Tools package — re-exports all LangChain @tool wrappers.

Research via langchain-docs MCP: @tool decorator + type hints + docstring defines schema;
create_agent(model="openrouter:...") wires tools via ToolNode.
All tools respect 25/min bucket, 30s timeout, and LLM_MODEL_* selectors from core/config.
"""

from .broker_tools import get_account, get_clock, get_orders, get_positions, submit_order
from .market_tools import align_timeframes_tool, detect_arbitrage, get_market_snapshot, get_ohlcv, get_option_chain
from .news_tools import extract_keywords, fetch_news, get_macro_calendar

# Flat list for create_agent(tools=TOOLS)
TOOLS = [
    get_account,
    get_positions,
    get_orders,
    get_clock,
    submit_order,
    get_ohlcv,
    get_market_snapshot,
    get_option_chain,
    align_timeframes_tool,
    detect_arbitrage,
    fetch_news,
    get_macro_calendar,
    extract_keywords,
]

# Sub-groupings for per-agent wiring
BROKER_TOOLS = [get_account, get_positions, get_orders, get_clock]
# Sensitive write tools — require HumanInTheLoopMiddleware
BROKER_WRITE_TOOLS = [submit_order]
MARKET_TOOLS = [get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, detect_arbitrage]
NEWS_TOOLS = [fetch_news, get_macro_calendar, extract_keywords]

__all__ = [
    "TOOLS",
    "BROKER_TOOLS",
    "BROKER_WRITE_TOOLS",
    "MARKET_TOOLS",
    "NEWS_TOOLS",
    "get_account",
    "get_positions",
    "get_orders",
    "get_clock",
    "submit_order",
    "get_ohlcv",
    "get_market_snapshot",
    "get_option_chain",
    "align_timeframes_tool",
    "detect_arbitrage",
    "fetch_news",
    "get_macro_calendar",
    "extract_keywords",
]
