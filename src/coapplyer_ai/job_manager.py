"""
Orchestrator for the job search and application pipeline.
"""

# ============================================================
# ORCHESTRATOR: Job Manager
# Coordinates the full job search pipeline:
#   1. Search  → job_search.JobSearchNavigator
#   2. Scrape  → job_scraper.JobScraper
#   3. Filter  → job_filter.JobFilter
#   4. Output  → application_output.ApplicationOutputWriter
# ============================================================

import random
import time
import traceback
from itertools import product
from pathlib import Path

from inputimeout import inputimeout, TimeoutOccurred
from selenium.webdriver.common.by import By

from app_config import (
    JOB_MAX_APPLICATIONS,
    JOB_MIN_APPLICATIONS,
    MINIMUM_WAIT_TIME_IN_SECONDS,
    REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT,
)
from src.browser_adapters import BrowserAdapter, SeleniumBrowserAdapter
from src.logging import logger
import src.utils.time_utils

from src.coapplyer_ai.application_output import ApplicationOutputWriter
from src.coapplyer_ai.job_filter import JobFilter
from src.coapplyer_ai.job_scraper import JobScraper
from src.coapplyer_ai.job_search import EnvironmentKeys, JobSearchNavigator
from src.coapplyer_ai.linkedIn_easy_applier import CoApplyerAIEasyApplier


class CoApplyerAIJobManager:
    def __init__(self, driver, browser_adapter: BrowserAdapter | None = None):
        logger.debug("Initializing CoApplyerAIJobManager")
        self.driver = driver
        self.browser_adapter = browser_adapter or (SeleniumBrowserAdapter(driver) if driver is not None else None)
        self.set_old_answers = set()
        self.easy_applier_component = None
        self.trial_job_limit = None
        self.require_submit_confirmation = REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT
        self.output_file_directory: Path | None = None

        browser = self.browser_adapter or driver
        self._scraper = JobScraper(browser)
        self._navigator = JobSearchNavigator(browser)
        # Parameter-dependent; built in set_parameters().
        self._filter: JobFilter | None = None
        self._output: ApplicationOutputWriter | None = None
        logger.debug("CoApplyerAIJobManager initialized successfully")

    # ── Parameter Setup ───────────────────────────────────────
    def set_parameters(self, parameters):
        logger.debug("Setting parameters for CoApplyerAIJobManager")
        self.positions = parameters.get('positions', [])
        self.locations = parameters.get('locations', [])

        self.min_applicants = JOB_MIN_APPLICATIONS
        self.max_applicants = JOB_MAX_APPLICATIONS

        resume_path = parameters.get('uploads', {}).get('resume', None)
        self.resume_path = Path(resume_path) if resume_path and Path(resume_path).exists() else None
        self.output_file_directory = Path(parameters['outputFileDirectory'])
        self.trial_job_limit = parameters.get('trialJobLimit')
        self.require_submit_confirmation = parameters.get(
            'requireSubmitConfirmation', REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT
        )
        self.env_config = EnvironmentKeys()

        self._navigator.base_search_url = JobSearchNavigator.get_base_search_url(parameters)
        self._output = ApplicationOutputWriter(self.output_file_directory)
        self._filter = JobFilter(
            company_blacklist=parameters.get('company_blacklist', []) or [],
            title_blacklist=parameters.get('title_blacklist', []) or [],
            location_blacklist=parameters.get('location_blacklist', []) or [],
            output_file_directory=self.output_file_directory,
            seen_jobs=[],
            apply_once_at_company=parameters.get('apply_once_at_company', False),
        )

        logger.debug("Parameters set successfully")

    def set_gpt_answerer(self, gpt_answerer):
        logger.debug("Setting GPT answerer")
        self.gpt_answerer = gpt_answerer

    def set_resume_generator_manager(self, resume_generator_manager):
        logger.debug("Setting resume generator manager")
        self.resume_generator_manager = resume_generator_manager

    # ── Search loop ───────────────────────────────────────────
    def start_collecting_data(self):
        searches = list(product(self.positions, self.locations))
        random.shuffle(searches)
        page_sleep = 0
        minimum_time = 60 * 5
        minimum_page_time = time.time() + minimum_time

        for position, location in searches:
            location_url = "&location=" + location
            job_page_number = -1
            logger.info(f"Collecting data for {position} in {location}.", color="yellow")
            try:
                while True:
                    page_sleep += 1
                    job_page_number += 1
                    logger.info(f"Going to job page {job_page_number}", color="yellow")
                    self._navigator.navigate_to_page(position, location_url, job_page_number)
                    src.utils.time_utils.medium_sleep()
                    logger.info("Starting the collecting process for this page", color="yellow")
                    self.read_jobs()
                    logger.info("Collecting data on this page has been completed!", color="yellow")

                    time_left = minimum_page_time - time.time()
                    if time_left > 0:
                        logger.info(f"Sleeping for {time_left} seconds.", color="yellow")
                        time.sleep(time_left)
                        minimum_page_time = time.time() + minimum_time
                    if page_sleep % 5 == 0:
                        sleep_time = random.randint(1, 5)
                        logger.info(f"Sleeping for {sleep_time / 60} minutes.", color="yellow")
                        time.sleep(sleep_time)
                        page_sleep += 1
            except Exception:
                pass
            time_left = minimum_page_time - time.time()
            if time_left > 0:
                logger.info(f"Sleeping for {time_left} seconds.", color="yellow")
                time.sleep(time_left)
                minimum_page_time = time.time() + minimum_time
            if page_sleep % 5 == 0:
                sleep_time = random.randint(50, 90)
                logger.info(f"Sleeping for {sleep_time / 60} minutes.", color="yellow")
                time.sleep(sleep_time)
                page_sleep += 1

    def start_applying(self):
        logger.debug("Starting job application process")
        self.easy_applier_component = CoApplyerAIEasyApplier(
            self.driver, self.resume_path, self.set_old_answers,
            self.gpt_answerer, self.resume_generator_manager,
            disable_suitability_filter=self.env_config.disable_description_filter,
            browser_adapter=self.browser_adapter,
            require_submit_confirmation=self.require_submit_confirmation
        )
        searches = list(product(self.positions, self.locations))
        random.shuffle(searches)
        page_sleep = 0
        minimum_time = MINIMUM_WAIT_TIME_IN_SECONDS
        minimum_page_time = time.time() + minimum_time

        for position, location in searches:
            location_url = "&location=" + location
            job_page_number = -1
            logger.debug(f"Starting the search for {position} in {location}.")

            try:
                while True:
                    page_sleep += 1
                    job_page_number += 1
                    logger.debug(f"Going to job page {job_page_number}")
                    self._navigator.navigate_to_page(position, location_url, job_page_number)
                    src.utils.time_utils.medium_sleep()
                    logger.debug("Starting the application process for this page...")

                    try:
                        jobs = self._scraper.get_jobs_from_page(scroll=True)
                        if not jobs:
                            logger.debug("No more jobs found on this page. Exiting loop.")
                            break
                    except Exception as e:
                        logger.error(f"Failed to retrieve jobs: {e}")
                        break

                    try:
                        self.apply_jobs()
                    except Exception as e:
                        logger.error(f"Error during job application: {e} {traceback.format_exc()}")
                        continue

                    logger.debug("Applying to jobs on this page has been completed!")

                    time_left = minimum_page_time - time.time()
                    if time_left > 0:
                        try:
                            user_input = inputimeout(
                                prompt=f"Sleeping for {time_left} seconds. Press 'y' to skip waiting. Timeout 60 seconds : ",
                                timeout=60).strip().lower()
                        except TimeoutOccurred:
                            user_input = ''
                        if user_input == 'y':
                            logger.debug("User chose to skip waiting.")
                        else:
                            logger.debug(f"Sleeping for {time_left} seconds as user chose not to skip.")
                            time.sleep(time_left)

                    minimum_page_time = time.time() + minimum_time

                    if page_sleep % 5 == 0:
                        sleep_time = random.randint(5, 34)
                        try:
                            user_input = inputimeout(
                                prompt=f"Sleeping for {sleep_time / 60} minutes. Press 'y' to skip waiting. Timeout 60 seconds : ",
                                timeout=60).strip().lower()
                        except TimeoutOccurred:
                            user_input = ''
                        if user_input == 'y':
                            logger.debug("User chose to skip waiting.")
                        else:
                            logger.debug(f"Sleeping for {sleep_time} seconds.")
                            time.sleep(sleep_time)
                        page_sleep += 1
            except Exception as e:
                logger.error(f"Unexpected error during job search: {e}")
                continue

            time_left = minimum_page_time - time.time()
            if time_left > 0:
                try:
                    user_input = inputimeout(
                        prompt=f"Sleeping for {time_left} seconds. Press 'y' to skip waiting. Timeout 60 seconds : ",
                        timeout=60).strip().lower()
                except TimeoutOccurred:
                    user_input = ''
                if user_input == 'y':
                    logger.debug("User chose to skip waiting.")
                else:
                    logger.debug(f"Sleeping for {time_left} seconds as user chose not to skip.")
                    time.sleep(time_left)

            minimum_page_time = time.time() + minimum_time

            if page_sleep % 5 == 0:
                sleep_time = random.randint(50, 90)
                try:
                    user_input = inputimeout(
                        prompt=f"Sleeping for {sleep_time / 60} minutes. Press 'y' to skip waiting: ",
                        timeout=60).strip().lower()
                except TimeoutOccurred:
                    user_input = ''
                if user_input == 'y':
                    logger.debug("User chose to skip waiting.")
                else:
                    logger.debug(f"Sleeping for {sleep_time} seconds.")
                    time.sleep(sleep_time)
                page_sleep += 1

    # ── Read / Apply helpers ──────────────────────────────────
    def read_jobs(self):
        job_element_list = self._scraper.get_jobs_from_page()
        job_list = [self._scraper.job_tile_to_job(job_element) for job_element in job_element_list]
        for job in job_list:
            if self._filter.is_blacklisted(job.title, job.company, job.link, job.location):
                logger.info(f"Blacklisted {job.title} at {job.company} in {job.location}, skipping...")
                self._output.write(job, "skipped")
                continue
            try:
                self._output.write(job, 'data')
            except Exception:
                self._output.write(job, "failed")
                continue

    def apply_jobs(self):
        job_element_list = self._scraper.get_jobs_from_page()
        job_list = [self._scraper.job_tile_to_job(job_element) for job_element in job_element_list]
        applied_attempts = 0

        for job in job_list:
            logger.debug(f"Starting applicant for job: {job.title} at {job.company}")

            if self._filter.is_previously_failed_to_apply(job.link):
                logger.debug(f"Previously failed to apply for {job.title} at {job.company}, skipping...")
                continue
            if self._filter.is_blacklisted(job.title, job.company, job.link, job.location):
                logger.debug(f"Job blacklisted: {job.title} at {job.company} in {job.location}")
                self._output.write(job, "skipped", "Job blacklisted")
                continue
            if self._filter.is_already_applied_to_job(job.title, job.company, job.link):
                self._output.write(job, "skipped", "Already applied to this job")
                continue
            if self._filter.is_already_applied_to_company(job.company):
                self._output.write(job, "skipped", "Already applied to this company")
                continue
            try:
                if job.apply_method not in {"Continue", "Applied", "Apply"}:
                    application_status = self.easy_applier_component.job_apply(job)
                    applied_attempts += 1
                    if application_status in {None, CoApplyerAIEasyApplier.STATUS_SUBMITTED}:
                        self._output.write(job, "success")
                        logger.debug(f"Applied to job: {job.title} at {job.company}")
                    elif application_status == CoApplyerAIEasyApplier.STATUS_SKIPPED_NOT_SUITABLE:
                        self._output.write(job, "skipped", "Job did not pass suitability filter")
                        logger.debug(f"Skipped job due to suitability filter: {job.title} at {job.company}")
                    elif application_status == CoApplyerAIEasyApplier.STATUS_AWAITING_HUMAN_CONFIRMATION:
                        self._output.write(job, "skipped", "Awaiting human confirmation before submit")
                        logger.info(f"Paused before submit for human confirmation: {job.title} at {job.company}")
                    else:
                        self._output.write(job, "skipped", f"Unexpected apply status: {application_status}")
                        logger.warning(f"Unexpected apply status '{application_status}' for job: {job.title} at {job.company}")

                    if getattr(self, "trial_job_limit", None) and applied_attempts >= int(self.trial_job_limit):
                        logger.info(f"Trial job limit reached ({self.trial_job_limit}); stopping after the first application attempt")
                        break
            except Exception as e:
                logger.error("Failed to apply for {} at {}: {}", job.title, job.company, type(e).__name__)
                self._output.write(job, "failed", f"Application error: {type(e).__name__}")
                if getattr(self, "trial_job_limit", None) and applied_attempts >= int(self.trial_job_limit):
                    logger.info(f"Trial job limit reached ({self.trial_job_limit}) after failure; stopping run")
                    break
                continue

