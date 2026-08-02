"""
linkedIn_easy_applier.py — Orchestrator Facade.

Coordinates the full per-job application pipeline by delegating to focused
pipeline-step sub-packages.
"""

# ============================================================
# ORCHESTRATOR: LinkedIn Easy Applier
# Coordinates the full per-job application pipeline:
#   1. Suitability  → suitability_checker.SuitabilityChecker
#   2. Form Filling → form_filler.FormFiller
#   3. Doc Upload   → document_uploader.DocumentUploader
# ============================================================

import os
import random
import time
import traceback
from typing import Any, List, Optional, Tuple

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement

from jobContext import JobContext
from job_application import JobApplication
from src.browser_adapters import BrowserAdapter, SeleniumBrowserAdapter
from src.coapplyer_ai.document_uploader import DocumentUploader
from src.coapplyer_ai.form_filler import FormFiller, SubmitConfirmationRequired
from src.coapplyer_ai.llm.llm_manager import GPTAnswerer
from src.coapplyer_ai.suitability_checker import SuitabilityChecker
from src.job import Job
from src.logging import logger
import src.utils.time_utils


class CoApplyerAIEasyApplier:
    STATUS_SUBMITTED = "submitted"
    STATUS_SKIPPED_NOT_SUITABLE = "skipped_not_suitable"
    STATUS_AWAITING_HUMAN_CONFIRMATION = "awaiting_human_confirmation"

    def __init__(
        self,
        driver: Any,
        resume_dir: Optional[str],
        set_old_answers: List[Tuple[str, str, str]],
        gpt_answerer: GPTAnswerer,
        resume_generator_manager,
        disable_suitability_filter: bool = False,
        browser_adapter: Optional[BrowserAdapter] = None,
        require_submit_confirmation: Optional[bool] = None,
    ):
        logger.debug("Initializing CoApplyerAIEasyApplier")
        if resume_dir is None or not os.path.exists(resume_dir):
            resume_dir = None
        self.driver = driver
        self.browser = browser_adapter or SeleniumBrowserAdapter(driver)
        self.resume_path = resume_dir
        self.set_old_answers = set_old_answers
        self.gpt_answerer = gpt_answerer
        self.resume_generator_manager = resume_generator_manager
        self.disable_suitability_filter = disable_suitability_filter
        self.current_job = None

        # ------------------------------------------------------------------
        # Pipeline step modules
        # ------------------------------------------------------------------
        self._suitability = SuitabilityChecker(
            self.browser, gpt_answerer, disable_filter=disable_suitability_filter
        )
        self._uploader = DocumentUploader(
            self.browser, driver, gpt_answerer, resume_generator_manager, self.resume_path
        )
        self._form_filler = FormFiller(
            self.browser, driver, gpt_answerer, resume_generator_manager, self._uploader,
            require_submit_confirmation=require_submit_confirmation
        )

        logger.debug("CoApplyerAIEasyApplier initialized successfully")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_to_job(self, job: Job) -> str:
        """Start the process of applying to a job; return status string."""
        logger.debug(f"Applying to job: {job}")
        try:
            application_status = self.job_apply(job)
            if application_status == self.STATUS_SUBMITTED:
                logger.info(f"Successfully applied to job: {job.title}")
            elif application_status == self.STATUS_SKIPPED_NOT_SUITABLE:
                logger.info(f"Skipped job after suitability check: {job.title}")
            else:
                logger.warning(f"Unknown application status '{application_status}' for job: {job.title}")
            return application_status
        except Exception as e:
            logger.error(f"Failed to apply to job: {job.title}, error: {str(e)}")
            raise

    def job_apply(self, job: Job) -> str:
        logger.debug(f"Starting job application for job: {job}")
        job_context = JobContext()
        job_context.job = job
        job_context.job_application = JobApplication(job)

        try:
            self.browser.get(job.link)
            logger.debug(f"Navigated to job link: {job.link}")
        except Exception as e:
            logger.error(f"Failed to navigate to job link: {job.link}, error: {str(e)}")
            raise

        src.utils.time_utils.medium_sleep()
        self.check_for_premium_redirect(job_context)

        try:
            self.browser.execute_script("document.activeElement.blur();")
            self.check_for_premium_redirect(job_context)

            easy_apply_button = self._find_easy_apply_button(job_context)
            self.check_for_premium_redirect(job_context)

            # DP-1: fetch metadata + summarise via LLM
            self._suitability.fetch_job_metadata(job)

            self.current_job = job
            self._form_filler.set_current_job(job)

            # DP-2: suitability gate
            if not self._suitability.is_suitable(job):
                return self.STATUS_SKIPPED_NOT_SUITABLE

            logger.debug("Attempting to click 'Easy Apply' button")
            self.browser.click(easy_apply_button, hover_first=True)
            logger.debug("'Easy Apply' button clicked successfully")

            logger.debug("Filling out application form")
            self._form_filler.fill_application_form(job_context)
            logger.debug(f"Job application process completed successfully for job: {job}")
            return self.STATUS_SUBMITTED

        except SubmitConfirmationRequired:
            logger.info(f"Final submit reached for {job.title}; awaiting human confirmation")
            return self.STATUS_AWAITING_HUMAN_CONFIRMATION

        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Failed to apply to job: {job}, error: {tb_str}")
            logger.debug("Saving application process due to failure")
            self._form_filler.save_job_application_process()
            raise Exception(f"Failed to apply to job! Original exception:\nTraceback:\n{tb_str}")

    def check_for_premium_redirect(self, job_context: JobContext, max_attempts: int = 3) -> None:
        job = job_context.job
        current_url = self.browser.current_url()
        attempts = 0

        while "linkedin.com/premium" in current_url and attempts < max_attempts:
            logger.warning("Redirected to linkedIn Premium page. Attempting to return to job page.")
            attempts += 1
            self.browser.get(job.link)
            time.sleep(2)
            current_url = self.browser.current_url()

        if "linkedin.com/premium" in current_url:
            raise Exception(
                f"Redirected to linkedIn Premium page and failed to return after {max_attempts} attempts. Job application aborted."
            )

    # ------------------------------------------------------------------
    # Private: modal-open step (stays on facade — pre-modal transition)
    # ------------------------------------------------------------------

    def _find_easy_apply_button(self, job_context: JobContext) -> WebElement:
        logger.debug("Searching for 'Easy Apply' button")
        attempt = 0

        search_methods = [
            {
                'description': "classic Easy Apply button by jobs-apply-button class",
                'css': 'button.jobs-apply-button'
            },
            {
                'description': "SDUI Easy Apply anchor by openSDUIApplyFlow href",
                'css': 'a[href*=\'openSDUIApplyFlow=true\']'
            },
            {
                'description': "anchor or button with aria-label containing 'Easy Apply' (both UIs)",
                'find_elements': True,
                'xpath': '//*[self::a or self::button][contains(@aria-label, "Easy Apply")]'
            },
            {
                'description': "anchor with href containing '/apply/' and 'openSDUIApplyFlow' (new UI)",
                'xpath': '//a[contains(@href, "/apply/") and contains(@href, "openSDUIApplyFlow")]'
            },
            {
                'description': "find 'Easy Apply' buttons using jobs-apply-button class (old UI)",
                'find_elements': True,
                'xpath': '//button[contains(@class, "jobs-apply-button") and contains(., "Easy Apply")]'
            },
            {
                'description': "anchor or button with 'Easy Apply' span child text",
                'find_elements': True,
                'xpath': '//*[self::a or self::button][.//span[normalize-space(text())="Easy Apply"]]'
            },
            {
                'description': "anchor or button normalised text search",
                'xpath': '//*[self::button or self::a][normalize-space(.)="Easy Apply" or normalize-space(.)="Apply now"]'
            }
        ]

        while attempt < 2:
            self.check_for_premium_redirect(job_context)
            self._scroll_page()

            for method in search_methods:
                try:
                    logger.debug(f"Attempting search using {method['description']}")

                    if method.get('css'):
                        button = self.browser.wait_for_presence(By.CSS_SELECTOR, method['css'], 10)
                        self.browser.wait_for_visible(button, 10)
                        self.browser.wait_for_clickable(button, 10)
                        return button

                    if method.get('find_elements'):
                        buttons = self.browser.find_elements(By.XPATH, method['xpath'])
                        if buttons:
                            for index, button in enumerate(buttons):
                                try:
                                    self.browser.wait_for_visible(button, 10)
                                    self.browser.wait_for_clickable(button, 10)
                                    return button
                                except Exception as e:
                                    logger.warning(f"Button {index + 1} found but not clickable: {e}")
                        else:
                            raise TimeoutException("No 'Easy Apply' buttons found")
                    else:
                        button = self.browser.wait_for_presence(By.XPATH, method['xpath'], 10)
                        self.browser.wait_for_visible(button, 10)
                        self.browser.wait_for_clickable(button, 10)
                        return button

                except TimeoutException:
                    logger.warning(f"Timeout during search using {method['description']}")
                except Exception as e:
                    logger.warning(
                        f"Failed to find 'Easy Apply' button using {method['description']} on attempt {attempt + 1}: {e}"
                    )

            self.check_for_premium_redirect(job_context)

            if attempt == 0:
                logger.debug("Refreshing page to retry finding 'Easy Apply' button")
                self.browser.refresh()
                time.sleep(random.randint(3, 5))
            attempt += 1

        page_url = self.browser.current_url()
        logger.error(f"No clickable 'Easy Apply' button found after 2 attempts. page url: {page_url}")
        raise Exception("No clickable 'Easy Apply' button found")

    def _scroll_page(self) -> None:
        logger.debug("Scrolling the page")
        for _ in range(3):
            self.browser.execute_script("window.scrollBy(0, window.innerHeight);")
            time.sleep(0.5)
        self.browser.execute_script("window.scrollTo(0, 0);")
