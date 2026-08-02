"""
Shared PDF file validation utilities.

Used by DocumentUploader (and any future module that generates PDF files)
to enforce size and extension constraints before uploading.
"""

import os

from src.logging import logger

_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB
_ALLOWED_EXTENSIONS = {'.pdf', '.doc', '.docx'}


def validate_pdf_file(file_path: str, label: str = "file") -> None:
    """
    Validate that *file_path* does not exceed 2 MB and has an allowed extension.

    Args:
        file_path: Absolute or relative path to the generated file.
        label: Human-readable label used in error messages (e.g. "Resume", "Cover letter").

    Raises:
        ValueError: If the file exceeds the size limit or has a disallowed extension.
    """
    file_size = os.path.getsize(file_path)
    logger.debug(f"{label} file size: {file_size} bytes")
    if file_size > _MAX_FILE_SIZE:
        logger.error(f"{label} file size exceeds 2 MB: {file_size} bytes")
        raise ValueError(f"{label} file size exceeds the maximum limit of 2 MB.")

    file_extension = os.path.splitext(file_path)[1].lower()
    logger.debug(f"{label} file extension: {file_extension}")
    if file_extension not in _ALLOWED_EXTENSIONS:
        logger.error(f"Invalid {label} file format: {file_extension}")
        raise ValueError(
            f"{label} file format is not allowed. Only PDF, DOC, and DOCX formats are supported."
        )