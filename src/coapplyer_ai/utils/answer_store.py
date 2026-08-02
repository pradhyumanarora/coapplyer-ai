"""
Shared answer persistence utilities.

Provides JSON-backed storage and retrieval of previously answered form
questions so that repeated questions are answered consistently without
re-querying the LLM.
"""

import json
import re
import traceback
from typing import Any, List, Optional

from src.logging import logger


# ------------------------------------------------------------------
# Module-level helper
# ------------------------------------------------------------------

def question_already_exists_in_data(question: str, data: List[dict]) -> bool:
    """Return True if a question with matching text already exists in data."""
    return any(item['question'] == question for item in data)


# ------------------------------------------------------------------
# AnswerStore
# ------------------------------------------------------------------

class AnswerStore:
    """
    JSON-backed store for form question/answer pairs.

    Shared by FormFiller (and any other module that needs to persist or
    look up previous answers).

    Args:
        current_job: Optional job reference used to detect company-name
                     contamination in answers. Must be set before calling
                     ``answer_contians_company_name``.
    """

    _OUTPUT_FILE = 'answers.json'

    def __init__(self, current_job=None):
        self.current_job = current_job
        self.all_data: List[dict] = self._load_questions_from_json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-read answers.json from disk into ``all_data``."""
        self.all_data = self._load_questions_from_json()

    def get_existing_answer(self, question_text: str, question_type: str) -> Optional[str]:
        """Return a previously saved answer for *question_text* and *question_type*, or None."""
        question_sanitized = self.sanitize_text(question_text)
        for item in self.all_data:
            if question_sanitized in item.get('question', '') and item.get('type') == question_type:
                return item.get('answer')
        return None

    def find_existing_answer(self, question_text: str) -> Optional[dict]:
        """Return the full answer dict whose sanitized question matches *question_text*, or None."""
        for item in self.all_data:
            if self.sanitize_text(item['question']) == self.sanitize_text(question_text):
                return item
        return None

    def save_question(self, question_data: dict) -> None:
        """Persist *question_data* to answers.json if it is not already stored."""
        question_data = dict(question_data)
        question_data['question'] = self.sanitize_text(question_data['question'])

        logger.debug(f"Checking if question data already exists: {question_data}")
        try:
            with open(self._OUTPUT_FILE, 'r+') as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, list):
                        raise ValueError("JSON file format is incorrect. Expected a list of questions.")
                except json.JSONDecodeError:
                    logger.error("JSON decoding failed")
                    data = []

                should_be_saved = (
                    not question_already_exists_in_data(question_data['question'], data)
                    and not self.answer_contians_company_name(question_data['answer'])
                )

                if should_be_saved:
                    logger.debug("New question found, appending to JSON")
                    data.append(question_data)
                    f.seek(0)
                    json.dump(data, f, indent=4)
                    f.truncate()
                    logger.debug("Question data saved successfully to JSON")
                else:
                    logger.debug("Question already exists, skipping save")
        except FileNotFoundError:
            logger.warning("JSON file not found, creating new file")
            with open(self._OUTPUT_FILE, 'w') as f:
                json.dump([question_data], f, indent=4)
            logger.debug("Question data saved successfully to new JSON file")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error saving questions data to JSON file: {tb_str}")
            raise Exception(f"Error saving questions data to JSON file: \nTraceback:\n{tb_str}")

        # Keep in-memory cache in sync
        self.all_data = self._load_questions_from_json()

    def answer_contians_company_name(self, answer: Any) -> bool:
        """Return True if *answer* contains the current job's company name."""
        return (
            isinstance(answer, str)
            and self.current_job is not None
            and self.current_job.company is not None
            and self.current_job.company in answer
        )

    @staticmethod
    def sanitize_text(text: str) -> str:
        """Normalise question text for consistent comparison and storage."""
        sanitized = text.lower().strip().replace('"', '').replace('\\', '')
        sanitized = re.sub(r'[\x00-\x1F\x7F]', '', sanitized).replace('\n', ' ').replace('\r', '').rstrip(',')
        logger.debug(f"Sanitized text: {sanitized}")
        return sanitized

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_questions_from_json(self) -> List[dict]:
        logger.debug(f"Loading questions from JSON file: {self._OUTPUT_FILE}")
        try:
            with open(self._OUTPUT_FILE, 'r') as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, list):
                        raise ValueError("JSON file format is incorrect. Expected a list of questions.")
                except json.JSONDecodeError:
                    logger.error("JSON decoding failed")
                    data = []
            logger.debug("Questions loaded successfully from JSON")
            return data
        except FileNotFoundError:
            logger.warning("JSON file not found, returning empty list")
            return []
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error loading questions data from JSON file: {tb_str}")
            raise Exception(f"Error loading questions data from JSON file: \nTraceback:\n{tb_str}")