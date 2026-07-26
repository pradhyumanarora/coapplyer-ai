"""Selenium browser adapter — isolated from Playwright code.

This module is only imported when the --selenium CLI flag is passed.
No playwright imports exist anywhere in this file or its dependencies.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterable

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from src.browser_adapters.base import BrowserAdapter


class SeleniumBrowserAdapter(BrowserAdapter):
    """BrowserAdapter implementation backed by a Selenium WebDriver instance."""

    def __init__(self, driver: Any):
        self.driver = driver

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def get(self, url: str) -> None:
        self.driver.get(url)

    def refresh(self) -> None:
        self.driver.refresh()

    def current_url(self) -> str:
        return str(self.driver.current_url or "")

    def page_source(self) -> str:
        return str(self.driver.page_source or "")

    def execute_script(self, script: str, *args: Any) -> Any:
        return self.driver.execute_script(script, *args)

    def switch_to_default_content(self) -> None:
        self.driver.switch_to.default_content()

    def find_element(self, by: str, value: str) -> Any:
        return self.driver.find_element(by, value)

    def find_elements(self, by: str, value: str) -> Iterable[Any]:
        return self.driver.find_elements(by, value)

    def click(self, element: Any, hover_first: bool = False) -> None:
        if hover_first:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.driver).move_to_element(element).click(element).perform()
        else:
            element.click()

    def fill_text(self, element: Any, text: str) -> None:
        element.clear()
        element.send_keys(text)

    def get_element_value(self, element: Any) -> str:
        return str(element.get_attribute("value") or "")

    def select_by_visible_text(self, element: Any, text: str) -> None:
        from selenium.webdriver.support.ui import Select
        Select(element).select_by_visible_text(text)

    def get_select_options(self, element: Any) -> list[str]:
        from selenium.webdriver.support.ui import Select
        return [opt.text for opt in Select(element).options]

    def get_selected_option_text(self, element: Any) -> str:
        from selenium.webdriver.support.ui import Select
        return Select(element).first_selected_option.text

    def wait_for_visible(self, element: Any, timeout_seconds: int) -> None:
        WebDriverWait(self.driver, timeout_seconds).until(
            EC.visibility_of(element)
        )

    def wait_for_clickable(self, element: Any, timeout_seconds: int) -> None:
        WebDriverWait(self.driver, timeout_seconds).until(
            EC.element_to_be_clickable(element)
        )

    def wait_for_presence(self, by: str, value: str, timeout_seconds: int) -> Any:
        return WebDriverWait(self.driver, timeout_seconds).until(
            EC.presence_of_element_located((by, value))
        )

    def wait_until(self, predicate: Callable[[], bool], timeout_seconds: int) -> None:
        deadline = time.time() + timeout_seconds
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                if predicate():
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(0.25)
        if last_error is not None:
            raise TimeoutError(str(last_error))
        raise TimeoutError("Timed out waiting for predicate")