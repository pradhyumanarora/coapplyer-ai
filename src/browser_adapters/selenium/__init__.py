"""Selenium browser engine — only imported when --selenium flag is used."""
from .adapter import SeleniumBrowserAdapter

__all__ = ["SeleniumBrowserAdapter"]