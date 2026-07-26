"""Playwright MCP browser adapter — re-exports from the flat module."""
from src.browser_adapters.playwright_mcp_adapter import (  # noqa: F401
    PlaywrightMcpBrowserAdapter,
)

__all__ = ["PlaywrightMcpBrowserAdapter"]