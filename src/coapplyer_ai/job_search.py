"""
Module for LinkedIn job search URL construction and page navigation.
"""

# ============================================================
# STEP: Job Search
# Responsible for LinkedIn job search URL construction
# and page navigation.
# ============================================================

import os
import urllib.parse

from app_config import DISABLE_DESCRIPTION_FILTER
from src.logging import logger


class EnvironmentKeys:
    def __init__(self):
        logger.debug("Initializing EnvironmentKeys")
        self.skip_apply = self._read_env_key_bool("SKIP_APPLY")
        self.disable_description_filter = DISABLE_DESCRIPTION_FILTER
        logger.debug(f"EnvironmentKeys initialized: skip_apply={self.skip_apply}, disable_description_filter={self.disable_description_filter}")

    @staticmethod
    def _read_env_key(key: str) -> str:
        value = os.getenv(key, "")
        logger.debug(f"Read environment key {key}: {value}")
        return value

    @staticmethod
    def _read_env_key_bool(key: str) -> bool:
        value = os.getenv(key) == "True"
        logger.debug(f"Read environment key {key} as bool: {value}")
        return value


class JobSearchNavigator:
    """Constructs LinkedIn search URLs and navigates to job search pages."""

    def __init__(self, browser, base_search_url: str = ""):
        self.browser = browser
        self.base_search_url = base_search_url

    @staticmethod
    def get_base_search_url(parameters: dict) -> str:
        logger.debug("Constructing base search URL")
        url_parts = []
        working_type_filter = []
        if parameters.get("onsite") == True:
            working_type_filter.append("1")
        if parameters.get("remote") == True:
            working_type_filter.append("2")
        if parameters.get("hybrid") == True:
            working_type_filter.append("3")

        if working_type_filter:
            url_parts.append(f"f_WT={'%2C'.join(working_type_filter)}")

        experience_levels = [str(i + 1) for i, (level, v) in enumerate(parameters.get('experience_level', {}).items()) if v]
        if experience_levels:
            url_parts.append(f"f_E={','.join(experience_levels)}")
        url_parts.append(f"distance={parameters['distance']}")
        job_types = [key[0].upper() for key, value in parameters.get('jobTypes', {}).items() if value]
        if job_types:
            url_parts.append(f"f_JT={','.join(job_types)}")
        date_mapping = {
            "all_time": "",
            "month": "&f_TPR=r2592000",
            "week": "&f_TPR=r604800",
            "24_hours": "&f_TPR=r86400"
        }
        date_param = next((v for k, v in date_mapping.items() if parameters.get('date', {}).get(k)), "")
        url_parts.append("f_LF=f_AL")  # Easy Apply
        base_url = "&".join(url_parts)
        full_url = f"?{base_url}{date_param}"
        logger.debug(f"Base search URL constructed: {full_url}")
        return full_url

    def navigate_to_page(self, position: str, location_url: str, job_page: int) -> None:
        logger.debug(f"Navigating to next job page: {position} in {location_url}, page {job_page}")
        encoded_position = urllib.parse.quote(position)
        self.browser.get(
            f"https://www.linkedin.com/jobs/search/{self.base_search_url}&keywords={encoded_position}{location_url}&start={job_page * 25}"
        )