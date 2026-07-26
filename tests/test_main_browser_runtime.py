from unittest import mock

import main


def test_resolve_browser_engine_prefers_selenium_override():
    assert main.resolve_browser_engine(True, configured_engine="playwright") == "selenium"


def test_resolve_browser_engine_keeps_configured_default():
    assert main.resolve_browser_engine(False, configured_engine="playwright") == "playwright"


def test_create_browser_runtime_selenium_path(mocker):
    """--selenium flag: Selenium browser + SeleniumBrowserAdapter."""
    driver = mock.Mock()
    adapter = mock.Mock()
    init_browser_spy = mocker.patch("main._init_selenium_browser", return_value=driver)
    create_adapter_spy = mocker.patch("main.create_browser_adapter", return_value=adapter)

    browser, browser_adapter = main.create_browser_runtime("selenium")

    init_browser_spy.assert_called_once_with()
    create_adapter_spy.assert_called_once_with("selenium", selenium_driver=driver)
    assert browser is driver
    assert browser_adapter is adapter


def test_create_browser_runtime_playwright_path(mocker):
    """Default (no --selenium): pure Playwright MCP, no Selenium imports."""
    adapter = mock.Mock()
    playwright_spy = mocker.patch("main._init_playwright_browser", return_value=(None, adapter))

    browser, browser_adapter = main.create_browser_runtime("playwright")

    playwright_spy.assert_called_once_with()
    assert browser is None
    assert browser_adapter is adapter


def test_create_browser_runtime_playwright_falls_back_to_selenium(mocker):
    """If _init_playwright_browser raises, falls back to Selenium."""
    adapter = mock.Mock()
    driver = mock.Mock()
    mocker.patch("main._init_playwright_browser", side_effect=FileNotFoundError("npx missing"))
    init_selenium_spy = mocker.patch("main._init_selenium_browser", return_value=driver)
    create_adapter_spy = mocker.patch("main.create_browser_adapter", return_value=adapter)

    browser, browser_adapter = main.create_browser_runtime("playwright")

    init_selenium_spy.assert_called_once_with()
    create_adapter_spy.assert_called_once_with("selenium", selenium_driver=driver)
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