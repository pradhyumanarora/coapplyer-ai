from __future__ import annotations

from typing import Any

from src.browser_adapters.base import BrowserAdapter
from src.browser_adapters.playwright_mcp_adapter import PlaywrightMcpBrowserAdapter
from src.browser_adapters.playwright_mcp_transport import PlaywrightMcpStdioSession
from src.browser_adapters.selenium_adapter import SeleniumBrowserAdapter


def create_browser_adapter(engine: str, selenium_driver: Any | None = None, mcp_session: Any | None = None) -> BrowserAdapter:
    normalized_engine = (engine or "selenium").strip().lower()

    if normalized_engine == "selenium":
        if selenium_driver is None:
            raise ValueError("selenium_driver is required when engine='selenium'")
        return SeleniumBrowserAdapter(selenium_driver)

    if normalized_engine in {"playwright", "playwright_mcp", "playwright-mcp"}:
        if mcp_session is None:
            mcp_session = PlaywrightMcpStdioSession()
        return PlaywrightMcpBrowserAdapter(mcp_session)

    raise ValueError(f"Unsupported browser engine: {engine}")
