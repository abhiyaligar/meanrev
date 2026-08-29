"""
Backward-compat alias — app/config.py delegates to core/config.py.

DOC.md §5 states core/config.py is the canonical config (pydantic-settings, reads .env).
This module re-exports Settings and get_settings so that
`from backend.app.config import get_settings` and
`from backend.broker.client import _create_trading_client` keep working.
New code should import from `backend.core.config`.
"""

from backend.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
