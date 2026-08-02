"""Document Upload pipeline step — résumé and cover letter PDF generation and upload."""

# ============================================================
# STEP: Document Upload
# Responsible for generating and uploading tailored résumé
# and cover letter PDFs for each job application.
# ============================================================

import base64
import os
import time
import traceback

from httpx import HTTPStatusError
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from jobContext import JobContext
from src.browser_adapters import BrowserAdapter
from src.coapplyer_ai.llm.llm_manager import GPTAnswerer
from src.coapplyer_ai.utils.pdf_utils import validate_pdf_file
from src.logging import logger


class DocumentUploader:
    """
    Generates and uploads resume and cover letter PDFs for a job application.

    Args:
        browser: BrowserAdapter instance.
        driver: Raw Selenium WebDriver (may be None for Playwright MCP path).
        gpt_answerer: GPTAnswerer instance for LLM calls.
        resume_generator_manager: Manager that produces resume PDF base64.
        resume_path: Path to a pre-existing static resume file, or None.
    """

    def __init__(self, browser: BrowserAdapter, driver, gpt_answerer: GPTAnswerer,
                 resume_generator_manager, resume_path):
        self.browser = browser
        self.driver = driver
        self.gpt_answerer = gpt_answerer
        self.resume_generator_manager = resume_generator_manager
        self.resume_path = resume_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_upload_fields(self, element, job_context: JobContext) -> None:
        """Detect file upload fields and dispatch to resume or cover letter upload."""
        logger.debug("Handling upload fields")

        try:
            show_more_button = self.browser.find_element(
                By.XPATH, "//button[contains(@aria-label, 'Show more resumes')]"
            )
            show_more_button.click()
            logger.debug("Clicked 'Show more resumes' button")
        except NoSuchElementException:
            logger.debug("'Show more resumes' button not found, continuing...")

        file_upload_elements = self.browser.find_elements(By.XPATH, "//input[@type='file']")
        for upload_element in file_upload_elements:
            parent_text = self._get_parent_text(upload_element)
            self.browser.execute_script("arguments[0].classList.remove('hidden')", upload_element)

            output = self.gpt_answerer.resume_or_cover(parent_text)
            if 'resume' in output:
                logger.debug("Uploading resume")
                if self.resume_path is not None and self.resume_path.resolve().is_file():
                    _upload_path = str(self.resume_path.resolve())
                    self._send_file(upload_element, _upload_path)
                    job_context.job.resume_path = _upload_path
                    job_context.job_application.resume_path = _upload_path
                    logger.debug(f"Resume uploaded from path: {_upload_path}")
                else:
                    logger.debug("Resume path not found or invalid, generating new resume")
                    self.create_and_upload_resume(upload_element, job_context)
            elif 'cover' in output:
                logger.debug("Uploading cover letter")
                self.create_and_upload_cover_letter(upload_element, job_context)

        logger.debug("Finished handling upload fields")

    def create_and_upload_resume(self, element, job_context: JobContext) -> None:
        """Generate a tailored resume PDF via LLM and upload it to the file input element."""
        job = job_context.job
        job_application = job_context.job_application
        logger.debug("Starting the process of creating and uploading resume.")

        file_path_pdf = self._generate_resume_pdf(job)
        validate_pdf_file(file_path_pdf, label="Resume")

        try:
            logger.debug(f"Uploading resume from path: {file_path_pdf}")
            _abs_path = os.path.abspath(file_path_pdf)
            self._send_file(element, _abs_path)
            job.resume_path = _abs_path
            job_application.resume_path = _abs_path
            time.sleep(2)
            logger.debug(f"Resume created and uploaded successfully: {file_path_pdf}")
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Resume upload failed: {tb_str}")
            raise Exception(f"Upload failed: \nTraceback:\n{tb_str}")

    def create_and_upload_cover_letter(self, element, job_context: JobContext) -> None:
        """Generate a cover letter PDF via LLM and upload it to the file input element."""
        job = job_context.job
        logger.debug("Starting the process of creating and uploading cover letter.")

        cover_letter_text = self.gpt_answerer.answer_question_textual_wide_range("Write a cover letter")
        file_path_pdf = self._generate_cover_letter_pdf(cover_letter_text)
        validate_pdf_file(file_path_pdf, label="Cover letter")

        try:
            logger.debug(f"Uploading cover letter from path: {file_path_pdf}")
            _abs_path = os.path.abspath(file_path_pdf)
            self._send_file(element, _abs_path)
            job.cover_letter_path = _abs_path
            job_context.job_application.cover_letter_path = _abs_path
            time.sleep(2)
            logger.debug(f"Cover letter created and uploaded successfully: {file_path_pdf}")
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Cover letter upload failed: {tb_str}")
            raise Exception(f"Upload failed: \nTraceback:\n{tb_str}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_parent_text(self, element) -> str:
        """Return lowercased parent element text for resume-vs-cover detection."""
        try:
            parent = element.find_element(By.XPATH, "..")
            return (parent.text if hasattr(parent, "text") else "").lower()
        except Exception:
            try:
                return str(self.browser.execute_script(
                    "const el = arguments[0]; return el && el.parentElement ? el.parentElement.innerText : '';",
                    element,
                ) or "").lower()
            except Exception:
                return ""

    def _send_file(self, element, abs_path: str) -> None:
        """Upload a file via send_keys or browser fill_text depending on element type."""
        if hasattr(element, "send_keys"):
            element.send_keys(abs_path)
        else:
            self.browser.fill_text(element, abs_path)

    def _ensure_folder(self, folder_path: str) -> None:
        try:
            if not os.path.exists(folder_path):
                logger.debug(f"Creating directory at path: {folder_path}")
            os.makedirs(folder_path, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory: {folder_path}. Error: {e}")
            raise

    def _generate_resume_pdf(self, job) -> str:
        """Generate resume PDF with retry on rate-limit; return local file path."""
        folder_path = 'generated_cv'
        self._ensure_folder(folder_path)

        while True:
            try:
                timestamp = int(time.time())
                file_path_pdf = os.path.join(folder_path, f"CV_{timestamp}.pdf")
                logger.debug(f"Generating resume for job: {job.title} at {job.company}")
                resume_pdf_base64 = self.resume_generator_manager.pdf_base64(job_description_text=job.description)
                with open(file_path_pdf, "xb") as f:
                    f.write(base64.b64decode(resume_pdf_base64))
                logger.debug(f"Resume saved to: {file_path_pdf}")
                return file_path_pdf
            except HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait_time = self._parse_retry_after(e) or 20
                    logger.warning(f"Rate limit exceeded, waiting {wait_time}s before retrying...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"HTTP error: {e}")
                    raise
            except Exception as e:
                tb_str = traceback.format_exc()
                logger.error(f"Failed to generate resume: {tb_str}")
                if "RateLimitError" in str(e):
                    logger.warning("Rate limit error encountered, retrying...")
                    time.sleep(20)
                else:
                    raise

    def _generate_cover_letter_pdf(self, cover_letter_text: str) -> str:
        """Render cover letter text to a PDF; return local file path."""
        folder_path = 'generated_cv'
        self._ensure_folder(folder_path)

        try:
            timestamp = int(time.time())
            file_path_pdf = os.path.join(folder_path, f"Cover_Letter_{timestamp}.pdf")
            logger.debug(f"Generating cover letter PDF: {file_path_pdf}")

            c = canvas.Canvas(file_path_pdf, pagesize=A4)
            page_width, page_height = A4
            text_object = c.beginText(50, page_height - 50)
            text_object.setFont("Helvetica", 12)
            max_width = page_width - 100
            bottom_margin = 50

            for line in self._wrap_text(cover_letter_text, "Helvetica", 12, max_width):
                if text_object.getY() > bottom_margin:
                    text_object.textLine(line)
                else:
                    c.drawText(text_object)
                    c.showPage()
                    text_object = c.beginText(50, page_height - 50)
                    text_object.setFont("Helvetica", 12)
                    text_object.textLine(line)

            c.drawText(text_object)
            c.save()
            logger.debug(f"Cover letter saved to: {file_path_pdf}")
            return file_path_pdf
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Failed to generate cover letter: {tb_str}")
            raise

    @staticmethod
    def _wrap_text(text: str, font: str, font_size: int, max_width: float) -> list:
        """Word-wrap *text* to fit within *max_width* points."""
        wrapped_lines = []
        for line in text.splitlines():
            if stringWidth(line, font, font_size) > max_width:
                words = line.split()
                new_line = ""
                for word in words:
                    if stringWidth(new_line + word + " ", font, font_size) <= max_width:
                        new_line += word + " "
                    else:
                        wrapped_lines.append(new_line.strip())
                        new_line = word + " "
                wrapped_lines.append(new_line.strip())
            else:
                wrapped_lines.append(line)
        return wrapped_lines

    @staticmethod
    def _parse_retry_after(e: HTTPStatusError):
        retry_after = e.response.headers.get('retry-after')
        retry_after_ms = e.response.headers.get('retry-after-ms')
        if retry_after:
            return int(retry_after)
        if retry_after_ms:
            return int(retry_after_ms) / 1000.0
        return None


__all__ = ["DocumentUploader"]