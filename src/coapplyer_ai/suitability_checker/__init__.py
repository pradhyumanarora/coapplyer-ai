"""Suitability Checking pipeline step — LLM-based job suitability scoring and summarisation."""

# ============================================================
# STEP: Suitability Checking
# Responsible for LLM-based job suitability scoring and
# job description summarisation.
# ============================================================

import time
import traceback

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from src.browser_adapters import BrowserAdapter
from src.coapplyer_ai.llm.llm_manager import GPTAnswerer
from src.job import Job
from src.logging import logger


class SuitabilityChecker:
    """
    Wraps GPT-answerer calls for job suitability scoring and metadata fetching.

    Args:
        browser: BrowserAdapter instance used to scrape page content.
        gpt_answerer: GPTAnswerer instance used for LLM calls.
        disable_filter: When True, ``is_suitable`` always returns True.
    """

    def __init__(self, browser: BrowserAdapter, gpt_answerer: GPTAnswerer, disable_filter: bool = False):
        self.browser = browser
        self.gpt_answerer = gpt_answerer
        self.disable_filter = disable_filter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_job_metadata(self, job: Job) -> None:
        """
        Fetch job description and recruiter link from the current page,
        attach them to ``job``, then call ``gpt_answerer.set_job(job)``
        to trigger LLM summarisation (DP-1 from AI_DECISION_POINTS.md).
        """
        logger.debug("Retrieving job description")
        job_description = self._get_job_description()
        job.set_job_description(job_description)
        logger.debug(f"Job description set: {job_description[:100]}")

        logger.debug("Retrieving recruiter link")
        recruiter_link = self._get_job_recruiter()
        job.set_recruiter_link(recruiter_link)
        logger.debug(f"Recruiter link set: {recruiter_link}")

        logger.debug("Passing job information to GPT Answerer")
        self.gpt_answerer.set_job(job)

    def is_suitable(self, job: Job) -> bool:
        """
        Return True if the job passes the suitability check (DP-2).

        Returns True unconditionally when ``disable_filter=True`` or when
        the job description is empty (to avoid false negatives).
        """
        if self.disable_filter:
            logger.debug("DISABLE_DESCRIPTION_FILTER is enabled; skipping job suitability check")
            return True

        if not (job.description or "").strip():
            logger.warning("Job description is empty; skipping suitability check to avoid false negatives")
            return True

        suitable = self.gpt_answerer.is_job_suitable()
        if not suitable:
            logger.info("Job deemed not suitable by GPT answerer")
        return suitable

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_job_description(self) -> str:
        logger.debug("Getting job description")
        try:
            for see_more_xpath in [
                '//button[@aria-label="Click to see more description"]',
                '//button[contains(@aria-label, "see more") and contains(@aria-label, "description")]',
                '//button[contains(@class, "jobs-description__footer-button")]',
            ]:
                try:
                    see_more_button = self.browser.find_element(By.XPATH, see_more_xpath)
                    self.browser.click(see_more_button, hover_first=True)
                    time.sleep(2)
                    break
                except NoSuchElementException:
                    continue

            description_selectors = [
                (By.CLASS_NAME, 'jobs-description-content__text'),
                (By.CLASS_NAME, 'job-details-about-the-job-module__description'),
                (By.XPATH, '//article[.//h2[text()="About the job"]]'),
                (By.XPATH, '//div[@id="job-details"]'),
                (By.XPATH, '//div[contains(@class, "jobs-description-content")]'),
                (By.XPATH, '//div[contains(@class, "jobs-description")]'),
                (By.XPATH, '//section[contains(@class, "jobs-description")]'),
                (By.XPATH, '//div[contains(@class, "job-view-layout")]//article'),
                (By.ID, 'job-details'),
            ]
            for by, selector in description_selectors:
                try:
                    element = self.browser.find_element(by, selector)
                    description = element.text
                    if description.strip():
                        logger.debug("Job description retrieved successfully")
                        return description
                except NoSuchElementException:
                    continue

            logger.warning("Job description element not found with any known selector, continuing with empty description")
            return ""
        except Exception:
            logger.warning(f"Error getting job description, continuing with empty: {traceback.format_exc()}")
            return ""

    def _get_job_recruiter(self) -> str:
        logger.debug("Getting job recruiter information")
        try:
            hiring_team_section = self.browser.wait_for_presence(
                By.XPATH, '//h2[text()="Meet the hiring team"]', 10
            )
            logger.debug("Hiring team section found")

            recruiter_elements = hiring_team_section.find_elements(
                By.XPATH, './/following::a[contains(@href, "linkedin.com/in/")]'
            )

            if recruiter_elements:
                recruiter_link = recruiter_elements[0].get_attribute('href')
                logger.debug(f"Job recruiter link retrieved successfully: {recruiter_link}")
                return recruiter_link
            else:
                logger.debug("No recruiter link found in the hiring team section")
                return ""
        except Exception as e:
            logger.warning(f"Failed to retrieve recruiter information: {e}")
            return ""


__all__ = ["SuitabilityChecker"]