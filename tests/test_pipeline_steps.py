from unittest import mock

import pytest

from src.coapplyer_ai.form_filler import FormFiller
from src.coapplyer_ai.suitability_checker import SuitabilityChecker
from src.job import Job


@pytest.fixture
def form_filler():
    return FormFiller(
        browser=mock.Mock(),
        driver=mock.Mock(),
        gpt_answerer=mock.Mock(),
        resume_generator_manager=mock.Mock(),
        document_uploader=mock.Mock(),
    )


def test_enter_text_uses_browser_adapter_verification(form_filler):
    element = mock.Mock()
    form_filler.browser.get_element_value.return_value = "Answer"

    form_filler._enter_text(element, "Answer")

    form_filler.browser.fill_text.assert_called_once_with(element, "Answer")
    form_filler.browser.wait_until.assert_called_once()


def test_set_current_job_updates_answer_store(form_filler):
    job = Job(title="Title", company="Company", location="Remote", apply_method="", link="Link")

    form_filler.set_current_job(job)

    assert form_filler.current_job is job
    assert form_filler._store.current_job is job


def test_is_suitable_skips_llm_when_description_empty():
    gpt_answerer = mock.Mock()
    checker = SuitabilityChecker(browser=mock.Mock(), gpt_answerer=gpt_answerer)
    job = Job(title="Title", company="Company", location="Remote", apply_method="", link="Link")
    job.description = ""

    assert checker.is_suitable(job) is True
    gpt_answerer.is_job_suitable.assert_not_called()


def test_is_suitable_bypassed_when_filter_disabled():
    gpt_answerer = mock.Mock()
    checker = SuitabilityChecker(browser=mock.Mock(), gpt_answerer=gpt_answerer, disable_filter=True)
    job = Job(title="Title", company="Company", location="Remote", apply_method="", link="Link")
    job.description = "Some description"

    assert checker.is_suitable(job) is True
    gpt_answerer.is_job_suitable.assert_not_called()
