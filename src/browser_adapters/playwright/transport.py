"""Playwright MCP transport — re-exports from the flat module for backwards compat.

The canonical implementation lives in src/browser_adapters/playwright_mcp_transport.py.
This file provides the playwright-subpackage import path:

    from src.browser_adapters.playwright.transport import PlaywrightMcpStdioSession
"""
from src.browser_adapters.playwright_mcp_transport import (  # noqa: F401
    PlaywrightMcpStdioSession,
    PlaywrightMcpSseClient,
    JsonRpcStdioClient,
    ToolSchema,
)

__all__ = [
    "PlaywrightMcpStdioSession",
    "PlaywrightMcpSseClient",
    "JsonRpcStdioClient",
    "ToolSchema",
]