from unittest import mock

import main


def test_resolve_browser_engine_prefers_selenium_override():
    assert main.resolve_browser_engine(True, configured_engine="playwright") == "selenium"


def test_resolve_browser_engine_keeps_configured_default():
    assert main.resolve_browser_engine(False, configured_engine="playwright") == "playwright"


def test_create_browser_runtime_uses_playwright_with_cdp(mocker):
    """Playwright MCP runtime launches Chrome with CDP and attaches MCP to it."""
    driver = mock.Mock()
    adapter = mock.Mock()
    init_browser_spy = mocker.patch("main.init_browser", return_value=driver)
    create_adapter_spy = mocker.patch("main.create_browser_adapter", return_value=adapter)

    browser, browser_adapter = main.create_browser_runtime("playwright")

    # Chrome is launched with the CDP port so playwright-mcp can attach
    init_browser_spy.assert_called_once_with(cdp_port=main._PLAYWRIGHT_CDP_PORT)
    # Adapter is created with the CDP endpoint (not the selenium_driver)
    create_adapter_spy.assert_called_once_with(
        "playwright", cdp_endpoint=f"http://localhost:{main._PLAYWRIGHT_CDP_PORT}"
    )
    assert browser is driver
    assert browser_adapter is adapter


def test_create_browser_runtime_uses_selenium_driver(mocker):
    driver = mock.Mock()
    adapter = mock.Mock()
    init_browser_spy = mocker.patch("main.init_browser", return_value=driver)
    create_adapter_spy = mocker.patch("main.create_browser_adapter", return_value=adapter)

    browser, browser_adapter = main.create_browser_runtime("selenium")

    init_browser_spy.assert_called_once()
    create_adapter_spy.assert_called_once_with("selenium", selenium_driver=driver)
    assert browser is driver
    assert browser_adapter is adapter


def test_create_browser_runtime_falls_back_to_selenium_when_playwright_missing(mocker):
    driver = mock.Mock()
    adapter = mock.Mock()
    init_browser_spy = mocker.patch("main.init_browser", return_value=driver)
    create_adapter_spy = mocker.patch(
        "main.create_browser_adapter",
        side_effect=[FileNotFoundError("npx missing"), adapter],
    )

    browser, browser_adapter = main.create_browser_runtime("playwright")

    assert create_adapter_spy.call_count == 2
    # init_browser called twice: once with CDP port (Playwright attempt), once without (Selenium fallback)
    assert init_browser_spy.call_count == 2
    assert browser is driver
    assert browser_adapter is adapter


def test_apply_run_profile_sets_demo_limit():
    parameters = {"existing": True}

    updated = main.apply_run_profile(parameters, demo=True)

    assert updated["trialJobLimit"] == 1
    assert updated["demoMode"] is True
    assert "trialJobLimit" not in parameters


def test_apply_run_profile_noop_when_not_demo():
    parameters = {"existing": True}

    updated = main.apply_run_profile(parameters, demo=False)

    assert updated is parameters