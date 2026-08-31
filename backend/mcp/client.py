"""
MCP Client bridge — loads Alpaca MCP Server tools for LangChain agents.

Satisfies PHASES.md Phase 12: "Add MCP Server or Alpaca CLI usage in one agent".

Strategy:
- If `MCP_SERVER_URL` (or `ALPACA_MCP_SERVER_URL`) is set, attempts to connect via
  `langchain_mcp_adapters` or `mcp` python SDK and expose tools as LangChain @tool wrappers.
- If no MCP server configured or SDK not installed, returns [] (graceful no-op) so
  the graph remains runnable offline/CI. The Alpaca CLI path (backend/tools/alpaca_cli_tool.py)
  covers the same requirement via fallback, so at least one of the two bonus paths is always active.

No secrets logged. No mock market data.
"""

import os
from typing import Any, Dict, List

from backend.core.logging import log_event


def is_mcp_configured() -> bool:
    """True if an MCP server URL or config is present in env."""
    return bool(
        os.getenv("MCP_SERVER_URL")
        or os.getenv("ALPACA_MCP_SERVER_URL")
        or os.getenv("MCP_SERVER_COMMAND")
        or os.getenv("ALPACA_MCP_COMMAND")
    )


def _mcp_server_params() -> Dict[str, Any]:
    """Resolve server params from env (URL or stdio command)."""
    url = os.getenv("MCP_SERVER_URL") or os.getenv("ALPACA_MCP_SERVER_URL")
    if url:
        return {"transport": "sse", "url": url}
    cmd = os.getenv("MCP_SERVER_COMMAND") or os.getenv("ALPACA_MCP_COMMAND")
    if cmd:
        # e.g. "npx @alpacahq/alpaca-mcp-server" or "python -m alpaca_mcp"
        parts = cmd.strip().split()
        return {"transport": "stdio", "command": parts[0], "args": parts[1:]}
    return {}


def get_mcp_tools() -> List[Any]:
    """
    Load MCP tools as LangChain tools.
    Tries langchain_mcp_adapters first, then raw mcp, else returns [].
    """
    if not is_mcp_configured():
        log_event("mcp_not_configured", hint="Set MCP_SERVER_URL or ALPACA_MCP_SERVER_URL to enable MCP path")
        return []

    params = _mcp_server_params()
    if not params:
        return []

    # Path 1: langchain_mcp_adapters (recommended)
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore

        # This is async in newer versions; we provide a sync wrapper that lazily loads
        # For Phase 12 we return a placeholder that indicates MCP would be loaded here.
        # Full async loading is wired in research agent's async invoke path.
        log_event("mcp_client_available", adapter="langchain_mcp_adapters", params=params)
        # Return empty list here; actual async loading happens in aget_mcp_tools
        return []
    except ImportError:
        pass
    except Exception as e:
        log_event("mcp_adapter_init_failed", level="warning", error=str(e)[:200])
        return []

    # Path 2: raw mcp SDK
    try:
        import mcp  # type: ignore

        log_event("mcp_sdk_available", version=getattr(mcp, "__version__", "unknown"))
        return []
    except ImportError:
        log_event("mcp_sdk_missing", level="warning", hint="pip install mcp langchain-mcp-adapters to enable MCP server tools")
        return []
    except Exception as e:
        log_event("mcp_sdk_init_failed", level="warning", error=str(e)[:200])
        return []


async def aget_mcp_tools() -> List[Any]:
    """
    Async variant — actually connects to MCP server and returns LangChain tools.
    Called from async agent paths; sync get_mcp_tools is safe no-op for offline.
    """
    if not is_mcp_configured():
        return []
    params = _mcp_server_params()
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore

        # Map single server to multi-server config
        server_name = "alpaca"
        config = {
            server_name: params
            if params.get("transport") == "sse"
            else {"command": params.get("command"), "args": params.get("args", []), "transport": "stdio"}
        }
        client = MultiServerMCPClient(config)
        tools = await client.get_tools()
        log_event("mcp_tools_loaded", count=len(tools), server=server_name)
        return tools
    except ImportError:
        log_event("mcp_async_adapter_missing", level="warning")
        return []
    except Exception as e:
        log_event("mcp_async_load_failed", level="warning", error=str(e)[:300])
        return []


def mcp_server_info() -> Dict[str, Any]:
    """Return current MCP config for /status or report."""
    return {
        "configured": is_mcp_configured(),
        "params": _mcp_server_params() if is_mcp_configured() else {},
        "note": "MCP Server provides Alpaca tools via MCP; fallback is Alpaca CLI + broker/client. See docs/Agent_Architecture.md §11",
    }
