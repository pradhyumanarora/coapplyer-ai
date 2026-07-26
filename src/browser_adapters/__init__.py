"""Browser adapters package.

Playwright symbols are exported at module level (no Selenium dependency).
SeleniumBrowserAdapter is also importable here for backward compatibility,
but the factory only imports it lazily when engine='selenium' is requested.
"""
from src.browser_adapters.base import BrowserAdapter
from src.browser_adapters.factory import create_browser_adapter

# Playwright symbols — zero Selenium dependency
from src.browser_adapters.playwright_mcp_adapter import PlaywrightMcpBrowserAdapter
from src.browser_adapters.playwright_mcp_locator import PlaywrightMcpLocator
from src.browser_adapters.playwright_mcp_transport import PlaywrightMcpStdioSession

# SeleniumBrowserAdapter — kept for backward compat (tests, etc.)
# The factory imports it lazily; having it here doesn't hurt if selenium is installed.
from src.browser_adapters.selenium_adapter import SeleniumBrowserAdapter  # noqa: F401

__all__ = [
    "BrowserAdapter",
    "create_browser_adapter",
    "PlaywrightMcpBrowserAdapter",
    "PlaywrightMcpStdioSession",
    "PlaywrightMcpLocator",
    "SeleniumBrowserAdapter",
]
