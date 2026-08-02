import pytest
from selenium.common.exceptions import NoSuchElementException

from src.coapplyer_ai.job_scraper import JobScraper


@pytest.fixture
def browser(mocker):
    return mocker.Mock()


def test_get_jobs_from_page_no_jobs(browser):
    browser.find_element.side_effect = NoSuchElementException
    browser.find_elements.return_value = []

    assert JobScraper(browser).get_jobs_from_page() == []


def test_get_jobs_from_page_with_jobs(mocker, browser):
    no_jobs_element = mocker.Mock()
    no_jobs_element.text = ""
    browser.page_source.return_value = ""

    container = mocker.Mock()
    job_element = mocker.Mock()
    container.find_elements.return_value = [job_element, job_element]
    browser.find_element.side_effect = [no_jobs_element, container]

    jobs = JobScraper(browser).get_jobs_from_page()

    assert len(jobs) == 2
    assert browser.find_element.call_count == 2
    assert container.find_elements.call_count == 1
