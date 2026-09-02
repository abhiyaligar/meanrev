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
from .exa_tools import EXA_TOOLS, exa_get_contents, exa_search, exa_search_news
from .market_tools import align_timeframes_tool, detect_arbitrage, get_market_snapshot, get_ohlcv, get_option_chain
from .mcp_tools import MCP_TOOLS, mcp_get_account, mcp_get_clock, mcp_get_orders, mcp_get_positions
from .news_tools import extract_keywords, fetch_news, get_macro_calendar
from .option_tools import get_option_chain_docs, get_option_contracts, place_option_order

# Flat list for create_agent(tools=TOOLS) — 30 total (27 + 3 Exa web search)
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
    # Docs-correct options (per https://docs.alpaca.markets/us/docs/options-trading) — NOT OPRA historical
    get_option_contracts,
    place_option_order,
    # Exa Web Search — public perception + web context (highlights:true for agents, https://exa.ai/docs/reference/search-api-guide-for-coding-agents)
    exa_search,
    exa_search_news,
    exa_get_contents,
]

# Sub-groupings for per-agent wiring
BROKER_TOOLS = [get_account, get_positions, get_orders, get_clock]
# Sensitive write tools — require HumanInTheLoopMiddleware (all order-mutating, incl. options)
BROKER_WRITE_TOOLS = [submit_order, set_stop_loss, modify_order, cancel_order, cancel_all_orders, place_option_order]
MARKET_TOOLS = [get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, detect_arbitrage]
NEWS_TOOLS = [fetch_news, get_macro_calendar, extract_keywords]
ALPACA_CLI_TOOLS = ALPACA_CLI_TOOLS
MCP_TOOLS = MCP_TOOLS
OPTION_TOOLS = [get_option_contracts, place_option_order, get_option_chain_docs]
EXA_TOOLS = EXA_TOOLS

__all__ = [
    "TOOLS",
    "BROKER_TOOLS",
    "BROKER_WRITE_TOOLS",
    "MARKET_TOOLS",
    "NEWS_TOOLS",
    "ALPACA_CLI_TOOLS",
    "MCP_TOOLS",
    "OPTION_TOOLS",
    "EXA_TOOLS",
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
    "get_option_contracts",
    "place_option_order",
    "get_option_chain_docs",
    "exa_search",
    "exa_search_news",
    "exa_get_contents",
]
