import os

import pytest

from src.coapplyer_ai.job_manager import CoApplyerAIJobManager
from src.coapplyer_ai.linkedIn_easy_applier import CoApplyerAIEasyApplier
from src.job import Job


@pytest.fixture
def job_manager(mocker):
    manager = CoApplyerAIJobManager(mocker.Mock())
    manager._scraper = mocker.Mock()
    manager._filter = mocker.Mock()
    manager._output = mocker.Mock()
    return manager


@pytest.fixture
def job():
    return Job(title="Title", company="Company", location="Location", apply_method="", link="Link")


def _stage_single_job(job_manager, job, status):
    job_manager._scraper.get_jobs_from_page.return_value = [object()]
    job_manager._scraper.job_tile_to_job.return_value = job
    job_manager._filter.is_previously_failed_to_apply.return_value = False
    job_manager._filter.is_blacklisted.return_value = False
    job_manager._filter.is_already_applied_to_job.return_value = False
    job_manager._filter.is_already_applied_to_company.return_value = False
    job_manager.easy_applier_component.job_apply.return_value = status


def test_initialization(job_manager):
    assert job_manager.driver is not None
    assert job_manager.set_old_answers == set()
    assert job_manager.easy_applier_component is None


def test_set_parameters(mocker, job_manager):
    mocker.patch('pathlib.Path.exists', return_value=True)

    params = {
        'company_blacklist': ['Company A', 'Company B'],
        'title_blacklist': ['Intern', 'Junior'],
        'positions': ['Software Engineer', 'Data Scientist'],
        'locations': ['New York', 'San Francisco'],
        'apply_once_at_company': True,
        'uploads': {'resume': '/path/to/resume'},
        'outputFileDirectory': '/path/to/output',
        'job_applicants_threshold': {'min_applicants': 5, 'max_applicants': 50},
        'remote': False,
        'distance': 50,
        'date': {'all_time': True}
    }

    job_manager.set_parameters(params)

    assert str(job_manager.resume_path) == os.path.normpath('/path/to/resume')
    assert str(job_manager.output_file_directory) == os.path.normpath('/path/to/output')
    assert job_manager._filter is not None
    assert job_manager._output is not None


def test_apply_jobs_with_no_jobs(job_manager):
    job_manager._scraper.get_jobs_from_page.return_value = []

    job_manager.apply_jobs()

    job_manager._scraper.job_tile_to_job.assert_not_called()
    job_manager._output.write.assert_not_called()


def test_apply_jobs_with_jobs(mocker, job_manager, job):
    job_manager.easy_applier_component = mocker.Mock()
    _stage_single_job(job_manager, job, CoApplyerAIEasyApplier.STATUS_SUBMITTED)
    job_manager._scraper.get_jobs_from_page.return_value = [object(), object()]

    job_manager.apply_jobs()

    assert job_manager._scraper.job_tile_to_job.call_count == 2
    assert job_manager.easy_applier_component.job_apply.call_count == 2


def test_apply_jobs_writes_success_only_on_submitted(mocker, job_manager, job):
    job_manager.easy_applier_component = mocker.Mock()
    _stage_single_job(job_manager, job, CoApplyerAIEasyApplier.STATUS_SUBMITTED)

    job_manager.apply_jobs()

    job_manager._output.write.assert_called_once_with(job, "success")


def test_apply_jobs_writes_skipped_when_not_suitable(mocker, job_manager, job):
    job_manager.easy_applier_component = mocker.Mock()
    _stage_single_job(job_manager, job, CoApplyerAIEasyApplier.STATUS_SKIPPED_NOT_SUITABLE)

    job_manager.apply_jobs()

    job_manager._output.write.assert_called_once_with(job, "skipped", "Job did not pass suitability filter")


def test_apply_jobs_writes_skipped_when_awaiting_human_confirmation(mocker, job_manager, job):
    job_manager.easy_applier_component = mocker.Mock()
    _stage_single_job(job_manager, job, CoApplyerAIEasyApplier.STATUS_AWAITING_HUMAN_CONFIRMATION)

    job_manager.apply_jobs()

    job_manager._output.write.assert_called_once_with(job, "skipped", "Awaiting human confirmation before submit")


def test_read_jobs_skips_blacklisted(mocker, job_manager, job):
    job_manager._scraper.get_jobs_from_page.return_value = [object()]
    job_manager._scraper.job_tile_to_job.return_value = job
    job_manager._filter.is_blacklisted.return_value = True

    job_manager.read_jobs()

    job_manager._output.write.assert_called_once_with(job, "skipped")
