import pytest
from unittest import mock

from coapplyer_ai.linkedIn_easy_applier import CoApplyerAIEasyApplier, SubmitConfirmationRequired
from src.job import Job



@pytest.fixture
def mock_driver():
    """Fixture to mock Selenium WebDriver."""
    return mock.Mock()


@pytest.fixture
def mock_gpt_answerer():
    """Fixture to mock GPT Answerer."""
    return mock.Mock()


@pytest.fixture
def mock_resume_generator_manager():
    """Fixture to mock Resume Generator Manager."""
    return mock.Mock()


@pytest.fixture
def easy_applier(mock_driver, mock_gpt_answerer, mock_resume_generator_manager):
    """Fixture to initialize CoApplyerAIEasyApplier with mocks."""
    return CoApplyerAIEasyApplier(
        driver=mock_driver,
        resume_dir="/path/to/resume",
        set_old_answers=[('Question 1', 'Answer 1', 'Type 1')],
        gpt_answerer=mock_gpt_answerer,
        resume_generator_manager=mock_resume_generator_manager
    )


def test_initialization(mocker, easy_applier):
    """Test that CoApplyerAIEasyApplier is initialized correctly."""
    # Mock os.path.exists to return True
    mocker.patch('os.path.exists', return_value=True)

    easy_applier = CoApplyerAIEasyApplier(
        driver=mocker.Mock(),
        resume_dir="/path/to/resume",
        set_old_answers=[('Question 1', 'Answer 1', 'Type 1')],
        gpt_answerer=mocker.Mock(),
        resume_generator_manager=mocker.Mock()
    )

    assert easy_applier.resume_path == "/path/to/resume"
    assert len(easy_applier.set_old_answers) == 1
    assert easy_applier.gpt_answerer is not None
    assert easy_applier.resume_generator_manager is not None


def test_apply_to_job_success(mocker, easy_applier):
    """Test successfully applying to a job."""
    mock_job = mock.Mock()

    # Mock job_apply so we don't actually try to apply
    mocker.patch.object(easy_applier, 'job_apply')

    easy_applier.apply_to_job(mock_job)
    easy_applier.job_apply.assert_called_once_with(mock_job)


def test_apply_to_job_failure(mocker, easy_applier):
    """Test failure while applying to a job."""
    mock_job = mock.Mock()
    mocker.patch.object(easy_applier, 'job_apply',
                        side_effect=Exception("Test error"))

    with pytest.raises(Exception, match="Test error"):
        easy_applier.apply_to_job(mock_job)

    easy_applier.job_apply.assert_called_once_with(mock_job)


def test_check_for_premium_redirect_no_redirect(mocker, easy_applier):
    """Test that check_for_premium_redirect works when there's no redirect."""
    mock_job = mock.Mock()
    easy_applier.driver.current_url = "https://www.linkedin.com/jobs/view/1234"

    easy_applier.check_for_premium_redirect(mock_job)
    easy_applier.driver.get.assert_not_called()


def test_check_for_premium_redirect_with_redirect(mocker, easy_applier):
    """Test that check_for_premium_redirect handles linkedin Premium redirects."""
    mock_job = mock.Mock()
    easy_applier.driver.current_url = "https://www.linkedin.com/premium"
    mock_job.link = "https://www.linkedin.com/jobs/view/1234"

    with pytest.raises(Exception, match="Redirected to linkedIn Premium page and failed to return after 3 attempts. Job application aborted."):
        easy_applier.check_for_premium_redirect(mock_job)

    # Verify that it attempted to return to the job page 3 times
    assert easy_applier.driver.get.call_count == 3


def test_job_apply_returns_skipped_when_not_suitable(mocker, easy_applier):
    mock_job = mock.Mock()
    mock_job.link = "https://www.linkedin.com/jobs/view/1234"
    mock_job.title = "Test title"

    easy_applier.driver.current_url = "https://www.linkedin.com/jobs/view/1234"
    mocker.patch.object(easy_applier, '_find_easy_apply_button', return_value=mocker.Mock())
    mocker.patch.object(easy_applier, '_get_job_description', return_value="Some description")
    mocker.patch.object(easy_applier, '_get_job_recruiter', return_value="")
    fill_form_spy = mocker.patch.object(easy_applier, '_fill_application_form')
    easy_applier.gpt_answerer.is_job_suitable.return_value = False

    status = easy_applier.job_apply(mock_job)

    assert status == CoApplyerAIEasyApplier.STATUS_SKIPPED_NOT_SUITABLE
    fill_form_spy.assert_not_called()


def test_job_apply_skips_suitability_when_description_is_empty(mocker, easy_applier):
    mock_job = Job(
        title="Test title",
        company="Test company",
        location="Remote",
        apply_method="",
        link="https://www.linkedin.com/jobs/view/1234"
    )

    easy_applier.driver.current_url = "https://www.linkedin.com/jobs/view/1234"
    mocker.patch.object(easy_applier, '_find_easy_apply_button', return_value=mocker.Mock())
    mocker.patch.object(easy_applier, '_get_job_description', return_value="")
    mocker.patch.object(easy_applier, '_get_job_recruiter', return_value="")
    mocker.patch.object(easy_applier, '_fill_application_form')
    mocker.patch.object(easy_applier.browser, 'click')

    easy_applier.gpt_answerer.is_job_suitable.return_value = False
    status = easy_applier.job_apply(mock_job)

    easy_applier.gpt_answerer.is_job_suitable.assert_not_called()
    assert status == CoApplyerAIEasyApplier.STATUS_SUBMITTED


def test_job_apply_returns_awaiting_confirmation_when_submit_is_blocked(mocker, easy_applier):
    mock_job = Job(
        title="Test title",
        company="Test company",
        location="Remote",
        apply_method="",
        link="https://www.linkedin.com/jobs/view/1234"
    )

    easy_applier.driver.current_url = "https://www.linkedin.com/jobs/view/1234"
    mocker.patch.object(easy_applier, '_find_easy_apply_button', return_value=mocker.Mock())
    mocker.patch.object(easy_applier, '_get_job_description', return_value="Some description")
    mocker.patch.object(easy_applier, '_get_job_recruiter', return_value="")
    mocker.patch.object(easy_applier, '_fill_application_form', side_effect=SubmitConfirmationRequired())

    status = easy_applier.job_apply(mock_job)

    assert status == CoApplyerAIEasyApplier.STATUS_AWAITING_HUMAN_CONFIRMATION


def test_enter_text_uses_browser_adapter_verification(mocker, easy_applier):
    element = mock.Mock()
    browser = mock.Mock()
    browser.get_element_value.return_value = "Answer"
    easy_applier.browser = browser

    easy_applier._enter_text(element, "Answer")

    browser.fill_text.assert_called_once_with(element, "Answer")
    browser.wait_until.assert_called_once()
