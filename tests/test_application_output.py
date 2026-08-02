import json

import pytest

from src.coapplyer_ai.application_output import ApplicationOutputWriter
from src.job import Job


@pytest.fixture
def job():
    return Job(
        title="Title",
        company="Company",
        location="Location",
        apply_method="",
        link="https://www.linkedin.com/jobs/view/1/",
        recruiter_link="",
        resume_path=""
    )


def test_write_sets_empty_pdf_path_when_resume_missing(tmp_path, job):
    ApplicationOutputWriter(tmp_path).write(job, "skipped", "No resume path")

    rows = json.loads((tmp_path / "skipped.json").read_text(encoding="utf-8"))

    assert rows[0]["pdf_path"] == ""
    assert rows[0]["reason"] == "No resume path"


def test_write_appends_to_existing_file(tmp_path, job):
    writer = ApplicationOutputWriter(tmp_path)
    writer.write(job, "success")
    writer.write(job, "success")

    rows = json.loads((tmp_path / "success.json").read_text(encoding="utf-8"))

    assert len(rows) == 2
