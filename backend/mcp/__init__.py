"""
Meanrev MCP Bridge — Alpaca MCP Server integration for PHASES.md Phase 12.

This package exposes helpers to load Alpaca MCP Server tools into LangChain agents
via the Model Context Protocol. The graph and research agent prefer this path when
an MCP server is configured, otherwise fall back to local tools (no mock).

See: docs/Agent_Architecture.md §11, backend/tools/mcp_tools.py
"""

from .client import get_mcp_tools, is_mcp_configured

__all__ = ["get_mcp_tools", "is_mcp_configured"]
