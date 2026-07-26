"""Browser adapter factory — selects engine at runtime with lazy imports.

Selenium is only imported if engine='selenium', keeping the Playwright-only
path free of any Selenium dependency.
"""
from __future__ import annotations

from typing import Any

from src.browser_adapters.base import BrowserAdapter


def create_browser_adapter(
    engine: str,
    selenium_driver: Any | None = None,
    mcp_session: Any | None = None,
    cdp_endpoint: str | None = None,
    args: list[str] | None = None,
    extra_mcp_args: list[str] | None = None,
) -> BrowserAdapter:
    """Create and return a BrowserAdapter for the requested engine.

    Args:
        engine: "selenium" | "playwright" | "playwright_mcp"
        selenium_driver: a live Selenium WebDriver (required for engine='selenium')
        mcp_session: a pre-built PlaywrightMcpStdioSession (optional override)
        cdp_endpoint: CDP URL to attach playwright-mcp to an existing Chrome
        args: full args override for playwright-mcp (e.g. ["@playwright/mcp", "--headless"])
        extra_mcp_args: extra flags appended to the playwright-mcp command
                        e.g. ["--browser=chrome", "--user-data-dir=/path"]
    """
    normalized_engine = (engine or "playwright").strip().lower()

    # ------------------------------------------------------------------
    # Selenium engine — Selenium imported lazily, not at module level
    # ------------------------------------------------------------------
    if normalized_engine == "selenium":
        if selenium_driver is None:
            raise ValueError("selenium_driver is required when engine='selenium'")
        # Lazy import: keeps Playwright-only path free of Selenium
        from src.browser_adapters.selenium.adapter import SeleniumBrowserAdapter
        return SeleniumBrowserAdapter(selenium_driver)

    # ------------------------------------------------------------------
    # Playwright MCP engine (default)
    # ------------------------------------------------------------------
    if normalized_engine in {"playwright", "playwright_mcp", "playwright-mcp"}:
        if mcp_session is None:
            from src.browser_adapters.playwright_mcp_transport import PlaywrightMcpStdioSession
            mcp_session = PlaywrightMcpStdioSession(
                cdp_endpoint=cdp_endpoint,
                args=args,
                extra_mcp_args=extra_mcp_args,
            )
        from src.browser_adapters.playwright_mcp_adapter import PlaywrightMcpBrowserAdapter
        return PlaywrightMcpBrowserAdapter(mcp_session)

    raise ValueError(f"Unsupported browser engine: '{engine}'. Use 'selenium' or 'playwright'.")
