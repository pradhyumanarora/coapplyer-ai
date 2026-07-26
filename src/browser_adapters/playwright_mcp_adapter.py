from __future__ import annotations

import json
import time
from typing import Callable, Iterable, Any

from src.browser_adapters.base import BrowserAdapter
from src.browser_adapters.playwright_mcp_locator import PlaywrightMcpLocator, JS_HELPERS
from src.browser_adapters.playwright_mcp_transport import PlaywrightMcpStdioSession


class PlaywrightMcpBrowserAdapter(BrowserAdapter):
    def __init__(self, session: Any | None = None):
        self.session = session or PlaywrightMcpStdioSession()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _selector_from_locator(self, by: str, value: str) -> str:
        by = (by or "").upper()
        if by == "XPATH":
            return f"xpath={value}"
        if by == "CSS_SELECTOR":
            return value
        if by == "ID":
            return f"#{value}"
        if by == "CLASS_NAME":
            return "." + ".".join(part for part in value.split() if part)
        if by == "TAG_NAME":
            return value
        if by == "LINK_TEXT":
            return f'text="{value}"'
        if by == "PARTIAL_LINK_TEXT":
            return f'text={value}'
        return value

    def _evaluate(self, code: str) -> Any:
        return self.session.evaluate(f"{JS_HELPERS}\n{code}")

    def switch_to_default_content(self) -> None:
        return None

    def get(self, url: str) -> None:
        self.session.call_tool("browser_navigate", "navigate", url=url)

    def refresh(self) -> None:
        if getattr(self.session, "has_tool", lambda *args: False)("browser_refresh", "refresh"):
            self.session.call_tool("browser_refresh", "refresh")
            return
        self.session.call_tool("browser_navigate", "navigate", url=self.current_url())

    def current_url(self) -> str:
        result = self._evaluate("return window.location.href;")
        url = str(result or "")
        # If MCP returns an error string (e.g. "### Error\n..."), return empty
        if url.startswith("### Error") or url.startswith("Error:"):
            return ""
        return url

    def execute_script(self, script: str, *args: Any) -> Any:
        if args:
            serialized_args = ", ".join(json.dumps(arg) for arg in args)
            code = f"(function() {{ const arguments = [{serialized_args}]; {script} }})();"
        else:
            code = script
        return self._evaluate(code)

    def page_source(self) -> str:
        result = self._evaluate("return document.documentElement.outerHTML;")
        return str(result or "")

    def find_elements(self, by: str, value: str) -> Iterable[Any]:
        selector = self._selector_from_locator(by, value)
        raw = self._evaluate(f"return __pw_query({json.dumps(selector)}, document).length;")
        # Guard against error strings returned by MCP on browser failure
        try:
            count = int(raw or 0)
        except (ValueError, TypeError):
            count = 0
        return [PlaywrightMcpLocator(self.session, selector, index) for index in range(count)]

    def find_element(self, by: str, value: str) -> Any:
        selector = self._selector_from_locator(by, value)
        matches = list(self.find_elements(by, value))
        if not matches:
            raise LookupError(f"No element found for {by}={value}")
        return matches[0]

    def click(self, element: Any, hover_first: bool = False) -> None:
        if isinstance(element, PlaywrightMcpLocator):
            element.click()
            return
        if hasattr(element, "click"):
            element.click()
            return
        self.session.call_tool("browser_click", "click", selector=str(element), hover=hover_first)

    def wait_for_visible(self, element: Any, timeout_seconds: int) -> None:
        self.wait_until(lambda: self._is_visible(element), timeout_seconds)

    def wait_for_clickable(self, element: Any, timeout_seconds: int) -> None:
        self.wait_for_visible(element, timeout_seconds)

    def wait_for_presence(self, by: str, value: str, timeout_seconds: int) -> Any:
        selector = self._selector_from_locator(by, value)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            matches = list(self.find_elements(by, value))
            if matches:
                return matches[0]
            time.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for selector: {selector}")

    def select_by_visible_text(self, element: Any, text: str) -> None:
        if isinstance(element, PlaywrightMcpLocator):
            element.select_option(label=text)
            return
        if hasattr(element, "select_option"):
            element.select_option(label=text)
            return
        self.session.call_tool("browser_select_option", "select_option", selector=str(element), label=text)

    def get_select_options(self, element: Any) -> list[str]:
        if isinstance(element, PlaywrightMcpLocator):
            result = element.evaluate_all("return nodes.map(option => option.textContent?.trim() || '');")
            return list(result) if isinstance(result, list) else []
        result = self._evaluate(
            f"return Array.from((__pw_one({json.dumps(str(element))}, document, 0) || {{ options: [] }}).options || []).map(option => option.textContent?.trim() || '');"
        )
        return list(result) if isinstance(result, list) else []

    def get_selected_option_text(self, element: Any) -> str:
        if isinstance(element, PlaywrightMcpLocator):
            result = element.evaluate("return node.selectedOptions[0]?.textContent?.trim() || '';")
            return str(result or "")
        result = self._evaluate(
            f"return (__pw_one({json.dumps(str(element))}, document, 0) || {{ selectedOptions: [] }}).selectedOptions[0]?.textContent?.trim() || '';"
        )
        return str(result or "")

    def fill_text(self, element: Any, text: str) -> None:
        if isinstance(element, PlaywrightMcpLocator):
            element.fill(text)
            return
        if hasattr(element, "fill"):
            element.fill(text)
            return
        self.session.call_tool("browser_fill", "fill", selector=str(element), text=text)

    def get_element_value(self, element: Any) -> str:
        if isinstance(element, PlaywrightMcpLocator):
            return element.input_value()
        if hasattr(element, "input_value"):
            return element.input_value()
        result = self._evaluate(f"return __pw_value({json.dumps(str(element))}, document, 0);")
        return str(result or "")

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
        raise TimeoutError("Timed out waiting for Playwright predicate")

    def _is_visible(self, element: Any) -> bool:
        if isinstance(element, PlaywrightMcpLocator):
            result = element.evaluate(
                "return !!(node && node.getBoundingClientRect && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0);"
            )
            return bool(result)
        return bool(element)
