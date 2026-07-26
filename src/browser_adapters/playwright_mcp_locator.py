from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


JS_HELPERS = r"""
const __pw_query = (selector, root) => {
  const context = root || document;
  if (selector.startsWith('xpath=')) {
    const expression = selector.slice(6);
    const snapshot = document.evaluate(expression, context, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    const nodes = [];
    for (let index = 0; index < snapshot.snapshotLength; index += 1) {
      nodes.push(snapshot.snapshotItem(index));
    }
    return nodes;
  }
  return Array.from((context.querySelectorAll ? context : document).querySelectorAll(selector));
};

const __pw_one = (selector, root, index = 0) => __pw_query(selector, root)[index] || null;
const __pw_text = (selector, root, index = 0) => {
  const node = __pw_one(selector, root, index);
  return node ? (node.innerText || node.textContent || '').trim() : '';
};
const __pw_attr = (selector, root, index, name) => {
  const node = __pw_one(selector, root, index);
  return node ? (node.getAttribute(name) || '') : '';
};
const __pw_value = (selector, root, index = 0) => {
  const node = __pw_one(selector, root, index);
  return node && 'value' in node ? (node.value || '') : '';
};
const __pw_tag = (selector, root, index = 0) => {
  const node = __pw_one(selector, root, index);
  return node ? (node.tagName || '').toLowerCase() : '';
};
const __pw_click = (selector, root, index = 0) => {
  const node = __pw_one(selector, root, index);
  if (node) {
    node.click();
    return true;
  }
  return false;
};
const __pw_fill = (selector, root, index, value) => {
  const node = __pw_one(selector, root, index);
  if (!node) {
    return false;
  }
  node.focus();
  node.value = value;
  node.dispatchEvent(new Event('input', { bubbles: true }));
  node.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
};
const __pw_select = (selector, root, index, value) => {
  const node = __pw_one(selector, root, index);
  if (!node) {
    return false;
  }
  const option = Array.from(node.options || []).find(item => item.textContent.trim() === value || item.value === value);
  if (!option) {
    return false;
  }
  node.value = option.value;
  node.dispatchEvent(new Event('input', { bubbles: true }));
  node.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
};
"""


@dataclass(frozen=True)
class PlaywrightMcpLocator:
    session: Any
    selector: str
    index: int = 0
    root_expr: str = "document"

    def _json(self, value: str) -> str:
        return json.dumps(value)

    def _element_expr(self) -> str:
        return f"__pw_one({self._json(self.selector)}, {self.root_expr}, {self.index})"

    def _query_expr(self) -> str:
        return f"__pw_query({self._json(self.selector)}, {self.root_expr})"

    def _evaluate(self, expression: str) -> Any:
        return self.session.evaluate(f"{JS_HELPERS}\n{expression}")

    @property
    def text(self) -> str:
        return str(self._evaluate(f"return __pw_text({self._json(self.selector)}, {self.root_expr}, {self.index});") or "")

    @property
    def tag_name(self) -> str:
        return str(self._evaluate(f"return __pw_tag({self._json(self.selector)}, {self.root_expr}, {self.index});") or "")

    def get_attribute(self, name: str) -> str:
        return str(self._evaluate(f"return __pw_attr({self._json(self.selector)}, {self.root_expr}, {self.index}, {self._json(name)});") or "")

    def click(self) -> None:
        self._evaluate(f"return __pw_click({self._json(self.selector)}, {self.root_expr}, {self.index});")

    def fill(self, value: str) -> None:
        self._evaluate(f"return __pw_fill({self._json(self.selector)}, {self.root_expr}, {self.index}, {self._json(value)});")

    def clear(self) -> None:
        self.fill("")

    def input_value(self) -> str:
        return str(self._evaluate(f"return __pw_value({self._json(self.selector)}, {self.root_expr}, {self.index});") or "")

    def select_option(self, label: str) -> None:
        self._evaluate(f"return __pw_select({self._json(self.selector)}, {self.root_expr}, {self.index}, {self._json(label)});")

    def send_keys(self, value: str) -> None:
        """Bridge for Selenium-style send_keys. Routes to fill() for text inputs.
        For file inputs, sets the value via JS (works for generated/local paths)."""
        tag = self.tag_name
        field_type = self.get_attribute("type").lower()
        if tag == "input" and field_type == "file":
            # Native file dialog is not available via MCP; set value directly via JS.
            self._evaluate(
                f"(() => {{ const node = {self._element_expr()}; if (node) {{ "
                f"Object.defineProperty(node, 'value', {{writable: true}}); "
                f"node.value = {self._json(value)}; "
                f"node.dispatchEvent(new Event('change', {{bubbles: true}})); }} }})()"
            )
        else:
            self.fill(value)

    def evaluate(self, code: str) -> Any:
        return self._evaluate(f"(() => {{ const node = {self._element_expr()}; {code} }})()")

    def evaluate_all(self, code: str) -> Any:
        return self._evaluate(f"(() => {{ const nodes = {self._query_expr()}; {code} }})()")

    def find_elements(self, by: str, value: str) -> list[PlaywrightMcpLocator]:
        child_selector = self.session.to_selector(by, value)
        count = int(self._evaluate(f"return __pw_query({self._json(child_selector)}, {self._element_expr()}).length;") or 0)
        return [PlaywrightMcpLocator(self.session, child_selector, index, self._element_expr()) for index in range(count)]

    def find_element(self, by: str, value: str) -> PlaywrightMcpLocator:
        matches = self.find_elements(by, value)
        if not matches:
            raise LookupError(f"No element found for {by}={value}")
        return matches[0]