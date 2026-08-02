"""
Module for extracting Job objects from the LinkedIn DOM.
"""

# ============================================================
# STEP: Job Scraping
# Responsible for extracting Job objects from LinkedIn DOM.
# ============================================================

import re
import time
import traceback

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from src.job import Job
from src.logging import logger


class JobScraper:
    """Scrapes job listings from the LinkedIn jobs search page."""

    def __init__(self, browser):
        self.browser = browser

    def get_jobs_from_page(self, scroll: bool = False) -> list:
        # LookupError is raised by PlaywrightMcpBrowserAdapter when an element is not found
        _not_found = (NoSuchElementException, LookupError)

        try:
            no_jobs_element = self.browser.find_element(By.CLASS_NAME, 'jobs-search-two-pane__no-results-banner--expand')
            if 'No matching jobs found' in no_jobs_element.text or 'unfortunately, things aren' in self.browser.page_source().lower():
                logger.debug("No matching jobs found on this page, skipping.")
                return []
        except _not_found:
            pass

        try:
            container_xpaths = [
                "//ul[contains(@class, 'scaffold-layout__list-container')]",
                "//ul[contains(@class, 'jobs-search__results-list')]",
                "//ul[.//a[contains(@href, '/jobs/view/')]]",
                "//div[contains(@class, 'jobs-search-results-list')]",
                "//div[contains(@class, 'scaffold-layout__list')]",
            ]
            jobs_container = None
            for xpath in container_xpaths:
                try:
                    candidate = self.browser.find_element(By.XPATH, xpath)
                    jobs_container = candidate
                    logger.debug(f"Found jobs container with selector: {xpath}")
                    break
                except _not_found:
                    continue

            if jobs_container is None:
                raise NoSuchElementException("No jobs container found with any known selector")

            if scroll:
                logger.debug("Scrolling job results to load more cards")
                for _ in range(3):
                    self.browser.execute_script("window.scrollBy(0, document.body.scrollHeight);")
                    time.sleep(0.5)
                self.browser.execute_script("window.scrollTo(0, 0);")

            item_xpaths = [
                ".//li[.//a[contains(@class, 'job-card-container__link')]]",
                ".//li[.//a[contains(@class, 'job-card-list__title--link')]]",
                ".//li[contains(@class, 'jobs-search-results__list-item') and contains(@class, 'ember-view')]",
                ".//li[contains(@class, 'jobs-search-results__list-item')]",
                ".//li[contains(@class, 'job-card-container')]",
                ".//li[contains(@class, 'jobs-search-result')]",
                ".//li[.//a[contains(@href, '/jobs/view/')]]",
            ]
            job_element_list = []
            for xpath in item_xpaths:
                job_element_list = jobs_container.find_elements(By.XPATH, xpath)
                if job_element_list:
                    logger.debug(f"Found {len(job_element_list)} job items with selector: {xpath}")
                    break

            if not job_element_list:
                logger.debug("No job items in container, trying page-wide job link search...")
                job_element_list = self.browser.find_elements(
                    By.XPATH, "//main//li[.//a[contains(@href, '/jobs/view/')]]"
                )
                if job_element_list:
                    logger.debug(f"Found {len(job_element_list)} job items via page-wide search")

            if not job_element_list:
                logger.debug("No job class elements found on page, skipping.")
                return []

            return job_element_list

        except (NoSuchElementException, LookupError):
            logger.warning(f'No job results found on the page. \n expection: {traceback.format_exc()}')
            return []

        except Exception as e:
            logger.error(f"Error while fetching job elements: {e} {traceback.format_exc()}")
            return []

    def job_tile_to_job(self, job_tile) -> Job:
        logger.debug("Extracting job information from tile")
        job = Job()

        # Title
        try:
            job.title = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title--link').find_element(By.TAG_NAME, 'strong').text
            logger.debug(f"Job title extracted via job-card-list__title--link: {job.title}")
        except NoSuchElementException:
            try:
                job.title = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title').find_element(By.TAG_NAME, 'strong').text
                logger.debug(f"Job title extracted via job-card-list__title: {job.title}")
            except NoSuchElementException:
                try:
                    link_el = job_tile.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                    strong_els = link_el.find_elements(By.TAG_NAME, 'strong')
                    job.title = strong_els[0].text if strong_els else (link_el.get_attribute('aria-label') or link_el.text).strip()
                    logger.debug(f"Job title extracted via href fallback: {job.title}")
                except NoSuchElementException:
                    logger.warning("Job title is missing.")

        # Link
        try:
            href = job_tile.find_element(By.CLASS_NAME, 'job-card-container__link').get_attribute('href') or ""
            job.link = href.split('?')[0]
            logger.debug(f"Job link extracted via job-card-container__link: {job.link}")
        except NoSuchElementException:
            try:
                href = job_tile.find_element(By.CLASS_NAME, 'job-card-list__title').get_attribute('href') or ""
                job.link = href.split('?')[0]
                logger.debug(f"Job link extracted via job-card-list__title: {job.link}")
            except NoSuchElementException:
                try:
                    link_el = job_tile.find_element(By.XPATH, ".//a[contains(@href, '/jobs/view/')]")
                    job.link = (link_el.get_attribute('href') or "").split('?')[0]
                    logger.debug(f"Job link extracted via href fallback: {job.link}")
                except NoSuchElementException:
                    logger.warning("Job link is missing.")

        # Company
        try:
            job.company = job_tile.find_element(By.XPATH, ".//div[contains(@class, 'artdeco-entity-lockup__subtitle')]//span").text
            logger.debug(f"Job company extracted: {job.company}")
        except NoSuchElementException:
            try:
                job.company = job_tile.find_element(By.CLASS_NAME, 'job-card-container__company-name').text
                logger.debug(f"Job company extracted via job-card-container__company-name: {job.company}")
            except NoSuchElementException:
                try:
                    job.company = job_tile.find_element(
                        By.XPATH,
                        ".//a[contains(@href, '/jobs/view/')]/following-sibling::*[1]"
                    ).text.strip()
                    logger.debug(f"Job company extracted via sibling fallback: {job.company}")
                except NoSuchElementException as e:
                    logger.warning(f'Job company is missing. {e}')

        # Job ID from URL
        try:
            match = re.search(r'/jobs/view/(\d+)/', job.link)
            if match:
                job.id = match.group(1)
                logger.debug(f"Job ID extracted: {job.id} from url:{job.link}")
            else:
                logger.warning(f"Job ID not found in link: {job.link}")
        except Exception as e:
            logger.warning(f"Failed to extract job ID: {e}", exc_info=True)

        # Location
        try:
            job.location = job_tile.find_element(By.CLASS_NAME, 'job-card-container__metadata-item').text
        except NoSuchElementException:
            try:
                job.location = job_tile.find_element(
                    By.XPATH,
                    ".//li[contains(@class, 'job-card-container__metadata-item')]"
                ).text
            except NoSuchElementException:
                try:
                    job.location = job_tile.find_element(
                        By.XPATH,
                        ".//a[contains(@href, '/jobs/view/')]/following-sibling::*//li[1]"
                    ).text
                except NoSuchElementException:
                    logger.warning("Job location is missing.")

        # Apply method / job state
        try:
            job_state = job_tile.find_element(By.XPATH, ".//ul[contains(@class, 'job-card-list__footer-wrapper')]//li[contains(@class, 'job-card-container__apply-method')]").text
        except NoSuchElementException:
            try:
                job_state = job_tile.find_element(By.XPATH, ".//ul[contains(@class, 'job-card-list__footer-wrapper')]//li[contains(@class, 'job-card-container__footer-job-state')]").text
                job.apply_method = "Applied"
            except NoSuchElementException:
                try:
                    footer_items = job_tile.find_elements(By.XPATH, ".//li[contains(@class, 'job-card-container__footer-item') or contains(@class, 'job-card-list__footer')]")
                    for item in footer_items:
                        text = item.text.strip().lower()
                        if 'easy apply' in text:
                            job.apply_method = "Easy Apply"
                            break
                        elif 'applied' in text:
                            job.apply_method = "Applied"
                            break
                except Exception:
                    pass

        return job