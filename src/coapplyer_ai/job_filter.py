"""
Module for filtering jobs based on blacklists, deduplication, and prior application history.
"""

# ============================================================
# STEP: Job Filtering
# Responsible for blacklist checks, deduplication, and
# previously-failed-application tracking.
# ============================================================

import json
import re
from pathlib import Path

from src.logging import logger
from src.regex_utils import generate_regex_patterns_for_blacklisting


class JobFilter:
    """Filters jobs based on blacklists and prior application history."""

    def __init__(
        self,
        company_blacklist,
        title_blacklist,
        location_blacklist,
        output_file_directory: Path,
        seen_jobs: list,
        apply_once_at_company: bool,
    ):
        self.company_blacklist_patterns = generate_regex_patterns_for_blacklisting(company_blacklist or [])
        self.title_blacklist_patterns = generate_regex_patterns_for_blacklisting(title_blacklist or [])
        self.location_blacklist_patterns = generate_regex_patterns_for_blacklisting(location_blacklist or [])
        self.output_file_directory = output_file_directory
        self.seen_jobs = seen_jobs
        self.apply_once_at_company = apply_once_at_company

    def is_blacklisted(self, job_title, company, link, job_location) -> bool:
        logger.debug(f"Checking if job is blacklisted: {job_title} at {company} in {job_location}")
        title_blacklisted = any(re.search(pattern, job_title, re.IGNORECASE) for pattern in self.title_blacklist_patterns)
        company_blacklisted = any(re.search(pattern, company, re.IGNORECASE) for pattern in self.company_blacklist_patterns)
        location_blacklisted = any(re.search(pattern, job_location, re.IGNORECASE) for pattern in self.location_blacklist_patterns)
        link_seen = link in self.seen_jobs
        is_blacklisted = title_blacklisted or company_blacklisted or location_blacklisted or link_seen
        logger.debug(f"Job blacklisted status: {is_blacklisted}")
        return is_blacklisted

    def is_already_applied_to_job(self, job_title, company, link) -> bool:
        link_seen = link in self.seen_jobs
        if link_seen:
            logger.debug(f"Already applied to job: {job_title} at {company}, skipping...")
        return link_seen

    def is_already_applied_to_company(self, company) -> bool:
        if not self.apply_once_at_company:
            return False

        output_files = ["success.json"]
        for file_name in output_files:
            file_path = self.output_file_directory / file_name
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    try:
                        existing_data = json.load(f)
                        for applied_job in existing_data:
                            if applied_job['company'].strip().lower() == company.strip().lower():
                                logger.debug(f"Already applied at {company} (once per company policy), skipping...")
                                return True
                    except json.JSONDecodeError:
                        continue
        return False

    def is_previously_failed_to_apply(self, link) -> bool:
        file_name = "failed"
        file_path = self.output_file_directory / f"{file_name}.json"

        if not file_path.exists():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump([], f)

        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                existing_data = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"JSON decode error in file: {file_path}")
                return False

        for data in existing_data:
            data_link = data['link']
            if data_link == link:
                return True

        return False