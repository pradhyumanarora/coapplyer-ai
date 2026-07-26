"""Playwright MCP locator — re-exports from the flat module."""
from src.browser_adapters.playwright_mcp_locator import (  # noqa: F401
    PlaywrightMcpLocator,
    JS_HELPERS,
)

__all__ = ["PlaywrightMcpLocator", "JS_HELPERS"]