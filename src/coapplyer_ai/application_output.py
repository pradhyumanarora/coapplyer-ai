"""
Module for persisting job application results to JSON output files.
"""

# ============================================================
# STEP: Application Output
# Responsible for persisting job application results to JSON.
# ============================================================

import json
from pathlib import Path

from src.job import Job
from src.logging import logger


class ApplicationOutputWriter:
    """Writes job application results to JSON files in the output directory."""

    def __init__(self, output_file_directory: Path):
        self.output_file_directory = output_file_directory

    def write(self, job: Job, file_name: str, reason: str | None = None) -> None:
        logger.debug(f"Writing job application result to file: {file_name}")
        pdf_path = ""
        if job.resume_path:
            pdf_path = Path(job.resume_path).resolve().as_uri()
        data = {
            "company": job.company,
            "job_title": job.title,
            "link": job.link,
            "job_recruiter": job.recruiter_link,
            "job_location": job.location,
            "pdf_path": pdf_path
        }

        if reason:
            data["reason"] = reason

        file_path = self.output_file_directory / f"{file_name}.json"
        if not file_path.exists():
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump([data], f, indent=4)
                logger.debug(f"Job data written to new file: {file_name}")
        else:
            with open(file_path, 'r+', encoding='utf-8') as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    logger.error(f"JSON decode error in file: {file_path}")
                    existing_data = []
                existing_data.append(data)
                f.seek(0)
                json.dump(existing_data, f, indent=4)
                f.truncate()
                logger.debug(f"Job data appended to existing file: {file_name}")