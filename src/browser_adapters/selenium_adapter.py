from __future__ import annotations

from typing import Callable, Iterable, Any

from selenium.webdriver import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select

from src.browser_adapters.base import BrowserAdapter


class SeleniumBrowserAdapter(BrowserAdapter):
    def __init__(self, driver: Any):
        self.driver = driver

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def switch_to_default_content(self) -> None:
        self.driver.switch_to.default_content()

    def get(self, url: str) -> None:
        self.driver.get(url)

    def refresh(self) -> None:
        self.driver.refresh()

    def current_url(self) -> str:
        return self.driver.current_url

    def execute_script(self, script: str, *args: Any) -> Any:
        return self.driver.execute_script(script, *args)

    def page_source(self) -> str:
        return self.driver.page_source

    def click(self, element: Any, hover_first: bool = False) -> None:
        if hover_first:
            try:
                ActionChains(self.driver).move_to_element(element).click().perform()
                return
            except Exception:
                pass

        try:
            element.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", element)

    def wait_for_visible(self, element: Any, timeout_seconds: int) -> None:
        WebDriverWait(self.driver, timeout_seconds).until(EC.visibility_of(element))

    def wait_for_clickable(self, element: Any, timeout_seconds: int) -> None:
        WebDriverWait(self.driver, timeout_seconds).until(EC.element_to_be_clickable(element))

    def wait_for_presence(self, by: str, value: str, timeout_seconds: int) -> Any:
        return WebDriverWait(self.driver, timeout_seconds).until(EC.presence_of_element_located((by, value)))

    def select_by_visible_text(self, element: Any, text: str) -> None:
        Select(element).select_by_visible_text(text)

    def get_select_options(self, element: Any) -> list[str]:
        return [option.text for option in Select(element).options]

    def get_selected_option_text(self, element: Any) -> str:
        return Select(element).first_selected_option.text

    def fill_text(self, element: Any, text: str) -> None:
        element.clear()
        element.send_keys(text)

    def get_element_value(self, element: Any) -> str:
        value = element.get_attribute("value")
        return value if value is not None else ""

    def find_elements(self, by: str, value: str) -> Iterable[Any]:
        return self.driver.find_elements(by, value)

    def find_element(self, by: str, value: str) -> Any:
        return self.driver.find_element(by, value)

    def wait_until(self, predicate: Callable[[], bool], timeout_seconds: int) -> None:
        def _wrapped(_driver: Any) -> bool:
            return predicate()

        WebDriverWait(self.driver, timeout_seconds).until(_wrapped)
