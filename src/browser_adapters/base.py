from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterable, Any


class BrowserAdapter(ABC):
    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def switch_to_default_content(self) -> None:
        pass

    @abstractmethod
    def get(self, url: str) -> None:
        pass

    @abstractmethod
    def refresh(self) -> None:
        pass

    @abstractmethod
    def current_url(self) -> str:
        pass

    @abstractmethod
    def execute_script(self, script: str, *args: Any) -> Any:
        pass

    @abstractmethod
    def page_source(self) -> str:
        pass

    @abstractmethod
    def click(self, element: Any, hover_first: bool = False) -> None:
        pass

    @abstractmethod
    def wait_for_visible(self, element: Any, timeout_seconds: int) -> None:
        pass

    @abstractmethod
    def wait_for_clickable(self, element: Any, timeout_seconds: int) -> None:
        pass

    @abstractmethod
    def wait_for_presence(self, by: str, value: str, timeout_seconds: int) -> Any:
        pass

    @abstractmethod
    def select_by_visible_text(self, element: Any, text: str) -> None:
        pass

    @abstractmethod
    def get_select_options(self, element: Any) -> list[str]:
        pass

    @abstractmethod
    def get_selected_option_text(self, element: Any) -> str:
        pass

    @abstractmethod
    def fill_text(self, element: Any, text: str) -> None:
        pass

    @abstractmethod
    def get_element_value(self, element: Any) -> str:
        pass

    @abstractmethod
    def find_elements(self, by: str, value: str) -> Iterable[Any]:
        pass

    @abstractmethod
    def find_element(self, by: str, value: str) -> Any:
        pass

    @abstractmethod
    def wait_until(self, predicate: Callable[[], bool], timeout_seconds: int) -> None:
        pass
