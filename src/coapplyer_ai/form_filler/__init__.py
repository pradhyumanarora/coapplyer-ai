"""Form Filling pipeline step — LinkedIn Easy Apply form detection, parsing, and answering."""

# ============================================================
# STEP: Form Filling
# Responsible for detecting, parsing, and answering all
# LinkedIn Easy Apply form fields (classic and SDUI).
# ============================================================

import sys
import time
from typing import Any, List, Optional

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement

from jobContext import JobContext
from job_application_saver import ApplicationSaver
from src.browser_adapters import BrowserAdapter
from src.coapplyer_ai.llm.llm_manager import GPTAnswerer
from src.coapplyer_ai.utils.answer_store import AnswerStore
from src.coapplyer_ai.utils.sdui import SduiMixin
from src.logging import logger
from app_config import REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT
import src.utils.time_utils


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

class SubmitConfirmationRequired(Exception):
    """Raised when the final Submit step requires human confirmation."""
    pass


# ------------------------------------------------------------------
# FormFiller
# ------------------------------------------------------------------

class FormFiller(SduiMixin):
    """
    Fills the LinkedIn Easy Apply modal form — classic (DOM) and SDUI (shadow-DOM) modes.

    Inherits ``SduiMixin`` for all shadow-DOM helpers (``SDUI_WALKER``, ``_sdui_*``).
    Uses ``AnswerStore`` for JSON-backed answer persistence.

    Args:
        browser: BrowserAdapter instance.
        driver: Raw Selenium WebDriver (may be None for Playwright MCP path).
        gpt_answerer: GPTAnswerer instance for LLM-driven answers.
        resume_generator_manager: Passed through to DocumentUploader for phone-country lookups.
        document_uploader: DocumentUploader instance injected for file-upload routing.
        current_job: Optional initial job reference (synced by facade before each apply).
        require_submit_confirmation: Pause at the Review step for human approval before
            clicking Submit. Defaults to ``REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT``.
    """

    def __init__(self, browser: BrowserAdapter, driver, gpt_answerer: GPTAnswerer,
                 resume_generator_manager, document_uploader, current_job=None,
                 require_submit_confirmation: Optional[bool] = None):
        self.browser = browser
        self.driver = driver
        self.gpt_answerer = gpt_answerer
        self.resume_generator_manager = resume_generator_manager
        self.document_uploader = document_uploader
        self.current_job = current_job
        self.require_submit_confirmation = (
            REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT if require_submit_confirmation is None
            else bool(require_submit_confirmation)
        )
        self._easy_apply_mode = "classic"
        self._store = AnswerStore(current_job=current_job)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_current_job(self, job) -> None:
        """Point the filler and its answer store at the job being applied to."""
        self.current_job = job
        self._store.current_job = job

    def fill_application_form(self, job_context: JobContext) -> None:
        """Drive the multi-step Easy Apply form loop until submission."""
        job = job_context.job
        job_application = job_context.job_application
        logger.debug(f"Filling out application form for job: {job}")
        failed_container_attempts = 0
        while True:
            self._sync_easy_apply_mode()
            if not self.fill_up(job_context):
                failed_container_attempts += 1
                if failed_container_attempts <= 2:
                    logger.warning(
                        f"Easy Apply form container unavailable (attempt {failed_container_attempts}); waiting and retrying"
                    )
                    src.utils.time_utils.short_sleep()
                    self._wait_for_easy_apply_surface(timeout_seconds=8)
                    continue
                raise Exception("Easy Apply form container is unavailable; cannot continue to Next/Submit step")
            if self._next_or_submit():
                ApplicationSaver.save(job_application)
                logger.debug("Application form submitted")
                break

    def fill_up(self, job_context: JobContext) -> bool:
        """Fill the current form step; return True if step was populated or action buttons exist."""
        job = job_context.job
        logger.debug(f"Filling up form sections for job: {job}")

        try:
            self._sync_easy_apply_mode()
            if self._easy_apply_mode == "sdui":
                return self._fill_sdui_fields(job_context)

            try:
                self.browser.switch_to_default_content()
            except Exception:
                pass

            self._wait_for_easy_apply_surface(timeout_seconds=8)

            easy_apply_content = None
            for css_class in [
                'jobs-easy-apply-content',
                'jobs-easy-apply-form',
                'jobs-easy-apply-modal__content',
                'artdeco-modal__content',
            ]:
                elements = self.browser.find_elements(By.CLASS_NAME, css_class)
                if elements:
                    easy_apply_content = elements[0]
                    logger.debug(f"Found form container with class: {css_class}")
                    break

            if easy_apply_content is None:
                dialogs = self.browser.find_elements(
                    By.XPATH,
                    "//dialog | //*[@role='dialog'] | //*[@data-test-modal] | //div[contains(@class,'jobs-easy-apply-modal')]"
                )
                if dialogs:
                    easy_apply_content = dialogs[0]
                    logger.debug("Using dialog element as form container fallback")
                else:
                    form_roots = self.browser.find_elements(
                        By.XPATH,
                        "//form[.//button[@data-easy-apply-next-button or @data-live-test-easy-apply-next-button or @data-easy-apply-submit-button]]"
                    )
                    if form_roots:
                        easy_apply_content = form_roots[0]
                        logger.debug("Using form element as fallback container")

                    action_buttons = self._find_action_buttons()
                    if action_buttons:
                        logger.warning(
                            "Easy Apply form container not found, but action buttons exist; continuing to next/submit step"
                        )
                        return True

                    logger.error("Could not find Easy Apply form container.")
                    return False

            input_elements = easy_apply_content.find_elements(
                By.CLASS_NAME, 'jobs-easy-apply-form-section__grouping'
            )
            for element in input_elements:
                self._process_form_element(element, job_context)
            return True
        except Exception as e:
            logger.error(f"Failed to find form elements: {e}")
            return False

    # ------------------------------------------------------------------
    # Classic form helpers
    # ------------------------------------------------------------------

    def _process_form_element(self, element: WebElement, job_context: JobContext) -> None:
        logger.debug("Processing form element")
        if self._is_upload_field(element):
            self.document_uploader.handle_upload_fields(element, job_context)
        else:
            self._fill_additional_questions(job_context)

    def _fill_additional_questions(self, job_context: JobContext) -> None:
        logger.debug("Filling additional questions")
        form_sections = self.browser.find_elements(
            By.CLASS_NAME, 'jobs-easy-apply-form-section__grouping'
        )
        for section in form_sections:
            self._process_form_section(job_context, section)

    def _process_form_section(self, job_context: JobContext, section: WebElement) -> None:
        logger.debug("Processing form section")
        if self._handle_terms_of_service(job_context, section):
            logger.debug("Handled terms of service")
            return
        if self._find_and_handle_radio_question(job_context, section):
            logger.debug("Handled radio question")
            return
        if self._find_and_handle_textbox_question(job_context, section):
            logger.debug("Handled textbox question")
            return
        if self._find_and_handle_date_question(job_context, section):
            logger.debug("Handled date question")
            return
        if self._find_and_handle_dropdown_question(job_context, section):
            logger.debug("Handled dropdown question")
            return

    def _handle_terms_of_service(self, job_context: JobContext, element: WebElement) -> bool:
        checkbox = element.find_elements(By.TAG_NAME, 'label')
        if checkbox and any(
                term in checkbox[0].text.lower()
                for term in ['terms of service', 'privacy policy', 'terms of use']):
            checkbox[0].click()
            logger.debug("Clicked terms of service checkbox")
            return True
        return False

    def _find_and_handle_radio_question(self, job_context: JobContext, section: WebElement) -> bool:
        job_application = job_context.job_application
        question = section.find_element(By.CLASS_NAME, 'jobs-easy-apply-form-element')
        radios = question.find_elements(By.CLASS_NAME, 'fb-text-selectable__option')
        if radios:
            question_text = section.text.lower()
            options = [radio.text.lower() for radio in radios]

            existing_answer = self._store.find_existing_answer(question_text)
            if existing_answer and existing_answer.get('type') == 'radio':
                self._select_radio(radios, existing_answer['answer'])
                job_application.save_application_data(existing_answer)
                logger.debug("Selected existing radio answer")
                return True

            answer = self.gpt_answerer.answer_question_from_options(question_text, options)
            self._store.save_question({'type': 'radio', 'question': question_text, 'answer': answer})
            job_application.save_application_data({'type': 'radio', 'question': question_text, 'answer': answer})
            self._select_radio(radios, answer)
            logger.debug("Selected new radio answer")
            return True
        return False

    def _find_and_handle_textbox_question(self, job_context: JobContext, section: WebElement) -> bool:
        logger.debug("Searching for text fields in the section.")
        text_fields = (
            section.find_elements(By.TAG_NAME, 'input')
            + section.find_elements(By.TAG_NAME, 'textarea')
        )

        if text_fields:
            text_field = text_fields[0]
            question_text = section.find_element(By.TAG_NAME, 'label').text.lower().strip()
            logger.debug(f"Found text field with label: {question_text}")

            is_numeric = self._is_numeric_field(text_field)
            question_type = 'numeric' if is_numeric else 'textbox'
            is_cover_letter = 'cover letter' in question_text

            existing_answer = None
            if not is_cover_letter:
                existing_answer = self._store.get_existing_answer(question_text, question_type)
                if existing_answer:
                    logger.debug(f"Found existing answer: {existing_answer}")

            if existing_answer and not is_cover_letter:
                answer = existing_answer
            else:
                answer = (
                    self.gpt_answerer.answer_question_numeric(question_text)
                    if is_numeric
                    else self.gpt_answerer.answer_question_textual_wide_range(question_text)
                )
                logger.debug(f"Generated answer: {answer}")

            self._enter_text(text_field, answer)
            job_context.job_application.save_application_data(
                {'type': question_type, 'question': question_text, 'answer': answer}
            )

            if not is_cover_letter and not existing_answer:
                self._store.save_question({'type': question_type, 'question': question_text, 'answer': answer})
            return True

        logger.debug("No text fields found in the section.")
        return False

    def _find_and_handle_date_question(self, job_context: JobContext, section: WebElement) -> bool:
        job_application = job_context.job_application
        date_fields = section.find_elements(By.CLASS_NAME, 'artdeco-datepicker__input ')
        if date_fields:
            date_field = date_fields[0]
            question_text = section.text.lower()
            answer_date = self.gpt_answerer.answer_question_date()
            answer_text = answer_date.strftime("%Y-%m-%d")

            existing = self._store.find_existing_answer(question_text)
            if existing and existing.get('type') == 'date':
                self._enter_text(date_field, existing['answer'])
                job_application.save_application_data(existing)
                logger.debug("Entered existing date answer")
                return True

            self._store.save_question({'type': 'date', 'question': question_text, 'answer': answer_text})
            job_application.save_application_data({'type': 'date', 'question': question_text, 'answer': answer_text})
            self._enter_text(date_field, answer_text)
            logger.debug("Entered new date answer")
            return True
        return False

    def _find_and_handle_dropdown_question(self, job_context: JobContext, section: WebElement) -> bool:
        job_application = job_context.job_application
        try:
            question = section.find_element(By.CLASS_NAME, 'jobs-easy-apply-form-element')
            dropdowns = question.find_elements(By.TAG_NAME, 'select')
            if not dropdowns:
                dropdowns = section.find_elements(By.CSS_SELECTOR, '[data-test-text-entity-list-form-select]')

            if dropdowns:
                dropdown = dropdowns[0]
                options = self.browser.get_select_options(dropdown)
                question_text = question.find_element(By.TAG_NAME, 'label').text.lower()
                current_selection = self.browser.get_selected_option_text(dropdown)

                existing_answer = self._store.get_existing_answer(question_text, 'dropdown')
                if existing_answer:
                    logger.debug(f"Found existing answer: {existing_answer}")
                    job_application.save_application_data(
                        {'type': 'dropdown', 'question': question_text, 'answer': existing_answer}
                    )
                    if current_selection != existing_answer:
                        self.browser.select_by_visible_text(dropdown, existing_answer)
                else:
                    answer = self.gpt_answerer.answer_question_from_options(question_text, options)
                    self._store.save_question({'type': 'dropdown', 'question': question_text, 'answer': answer})
                    job_application.save_application_data(
                        {'type': 'dropdown', 'question': question_text, 'answer': answer}
                    )
                    self.browser.select_by_visible_text(dropdown, answer)
                    logger.debug(f"Selected new dropdown answer: {answer}")
                return True
            else:
                logger.debug("No dropdown found.")
                return False
        except Exception as e:
            logger.warning(f"Failed to handle dropdown question: {e}", exc_info=True)
            return False

    def _handle_dropdown_fields(self, element: WebElement) -> None:
        logger.debug("Handling dropdown fields")
        dropdown = element.find_element(By.TAG_NAME, 'select')
        dropdown_id = dropdown.get_attribute('id')
        if 'phoneNumber-Country' in dropdown_id:
            country = self.resume_generator_manager.get_resume_country()
            if country:
                try:
                    self.browser.select_by_visible_text(dropdown, country)
                    logger.debug(f"Selected phone country: {country}")
                    return
                except NoSuchElementException:
                    logger.warning(f"Country {country} not found in dropdown options")

        options = self.browser.get_select_options(dropdown)
        parent_element = dropdown.find_element(By.XPATH, '../..')
        label_elements = parent_element.find_elements(By.TAG_NAME, 'label')
        question_text = label_elements[0].text.lower() if label_elements else "unknown"

        existing_answer = self._store.get_existing_answer(question_text, 'dropdown')
        if not existing_answer:
            existing_answer = self.gpt_answerer.answer_question_from_options(question_text, options)
            self._store.save_question({'type': 'dropdown', 'question': question_text, 'answer': existing_answer})

        if existing_answer in options:
            self.browser.select_by_visible_text(dropdown, existing_answer)
        else:
            raise Exception(f"Invalid option selected: {existing_answer}")

    def _is_upload_field(self, element: WebElement) -> bool:
        is_upload = bool(element.find_elements(By.XPATH, ".//input[@type='file']"))
        logger.debug(f"Element is upload field: {is_upload}")
        return is_upload

    def _is_numeric_field(self, field: WebElement) -> bool:
        field_type = (field.get_attribute('type') or '').lower()
        field_id = (field.get_attribute("id") or '').lower()
        return self._is_numeric_field_id_or_type(field_id, field_type)

    def _is_numeric_field_id_or_type(self, field_id: str, field_type: str) -> bool:
        field_id_l = (field_id or "").lower()
        field_type_l = (field_type or "").lower()
        return (
            'numeric' in field_id_l
            or field_type_l == 'number'
            or (field_type_l == 'text' and 'numeric' in field_id_l)
        )

    def _enter_text(self, element: WebElement, text: str) -> None:
        logger.debug(f"Entering text: {text}")
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                self.browser.fill_text(element, text)
                self.browser.wait_until(lambda: self.browser.get_element_value(element) == text, 5)
                return
            except Exception as exc:
                last_error = exc
                logger.warning(f"Text entry verification failed on attempt {attempt + 1}: {exc}")
                try:
                    self.browser.execute_script(
                        "arguments[0].value = ''; arguments[0].dispatchEvent(new Event('input', { bubbles: true }));",
                        element,
                    )
                except Exception:
                    pass
                time.sleep(1)
        raise Exception(f"Failed to enter text after retries: {last_error}")

    def _select_radio(self, radios: List[WebElement], answer: str) -> None:
        logger.debug(f"Selecting radio option: {answer}")
        for radio in radios:
            if answer in radio.text.lower():
                radio.find_element(By.TAG_NAME, 'label').click()
                return
        radios[-1].find_element(By.TAG_NAME, 'label').click()

    def _select_dropdown_option(self, element: WebElement, text: str) -> None:
        logger.debug(f"Selecting dropdown option: {text}")
        self.browser.select_by_visible_text(element, text)

    # ------------------------------------------------------------------
    # SDUI field filling (high-level; low-level helpers from SduiMixin)
    # ------------------------------------------------------------------

    def _fill_sdui_fields(self, job_context: JobContext) -> bool:
        fields = self._sdui_read_fields()
        if not fields:
            logger.debug("No SDUI fields found in dialog")
            return False

        logger.debug(f"Processing {len(fields)} SDUI field(s)")
        for field in fields:
            field_id = field.get("id") or ""
            if not field_id:
                continue
            field_type = (field.get("type") or "").lower()
            label = (field.get("label") or "").lower().strip()
            current_value = str(field.get("value") or "")

            if field_type in ("file", "hidden"):
                continue

            if field_type in ("checkbox", "radio"):
                if "top choice" in label:
                    continue
                if any(k in label for k in ("terms", "privacy", "consent", "agree", "declare")):
                    if not bool(field.get("checked")):
                        self._sdui_set_checked(field_id, True)
                continue

            if "location-geo-location" in field_id.lower() and not current_value.strip():
                city = self.gpt_answerer.answer_question_textual_wide_range(label or "current city")
                self._sdui_fill_geo(field_id, city)
                continue

            answer = self._decide_sdui_answer(field)
            if answer is None:
                continue

            if field.get("tag") == "select":
                if current_value.strip() != str(answer).strip():
                    selected = self._sdui_set_select(field_id, str(answer))
                    if selected in ("missing", "noopt"):
                        logger.warning(f"Could not set SDUI select {field_id} to '{answer}': {selected}")
                continue

            if current_value.strip() != str(answer).strip():
                read_back = self._sdui_set_input(field_id, str(answer))
                if str(read_back).strip() != str(answer).strip():
                    logger.warning(
                        f"SDUI input verification mismatch for {field_id}: expected '{answer}', got '{read_back}'"
                    )

            job_context.job_application.save_application_data(
                {'type': 'sdui', 'question': label or field_id, 'answer': str(answer)}
            )
        return True

    def _decide_sdui_answer(self, field: dict) -> Optional[str]:
        label = (field.get("label") or "unknown").lower().strip()
        tag = (field.get("tag") or "").lower()
        field_type = (field.get("type") or "").lower()
        options = field.get("options") or []

        if tag == "select":
            existing = self._store.get_existing_answer(label, "dropdown")
            if existing:
                return existing
            answer = self.gpt_answerer.answer_question_from_options(label, options)
            self._store.save_question({'type': 'dropdown', 'question': label, 'answer': answer})
            return answer

        if tag in ("input", "textarea") and field_type not in ("checkbox", "radio", "file", "hidden"):
            question_type = (
                "numeric" if self._is_numeric_field_id_or_type(field.get("id", ""), field_type)
                else "textbox"
            )
            existing = self._store.get_existing_answer(label, question_type)
            if existing:
                return existing
            answer = (
                self.gpt_answerer.answer_question_numeric(label)
                if question_type == "numeric"
                else self.gpt_answerer.answer_question_textual_wide_range(label)
            )
            self._store.save_question({'type': question_type, 'question': label, 'answer': answer})
            return str(answer)

        return None

    # ------------------------------------------------------------------
    # Navigation / submission
    # ------------------------------------------------------------------

    def _await_submit_confirmation(self) -> None:
        """Block on the Review step until the human approves; raise to skip the job."""
        job_title = getattr(self.current_job, "title", None) or "this job"
        if not sys.stdin or not sys.stdin.isatty():
            raise SubmitConfirmationRequired()
        logger.info(f"Review step reached for {job_title}; review the application in the browser.")
        try:
            response = input("Submit this application? [Enter = submit / s = skip]: ").strip().lower()
        except EOFError:
            raise SubmitConfirmationRequired()
        if response in ("s", "skip", "n", "no"):
            raise SubmitConfirmationRequired()

    def _next_or_submit(self) -> bool:
        logger.debug("Clicking 'Next' or 'Submit' button")
        self._sync_easy_apply_mode()

        if self._easy_apply_mode == "sdui":
            button_labels = [lbl.lower() for lbl in self._sdui_list_buttons()]
            logger.debug(f"Found SDUI button labels: {button_labels}")
            if any("submit application" in lbl for lbl in button_labels):
                if self.require_submit_confirmation:
                    self._await_submit_confirmation()
                self._unfollow_company()
                src.utils.time_utils.short_sleep()
                result = self._sdui_click_button(r"Submit application")
                logger.debug(f"SDUI submit click result: {result}")
                src.utils.time_utils.short_sleep()
                return True
            if any("review your application" in lbl for lbl in button_labels):
                self._sdui_click_button(r"Review your application")
                src.utils.time_utils.medium_sleep()
                self._check_for_errors()
                return False
            if any("continue to next step" in lbl for lbl in button_labels):
                self._sdui_click_button(r"Continue to next step")
                src.utils.time_utils.medium_sleep()
                self._check_for_errors()
                return False

        def _click_button(button: WebElement, is_submit: bool) -> bool:
            if is_submit:
                self._unfollow_company()
                src.utils.time_utils.short_sleep()
                button.click()
                src.utils.time_utils.short_sleep()
                return True
            src.utils.time_utils.short_sleep()
            button.click()
            src.utils.time_utils.medium_sleep()
            self._check_for_errors()
            return False

        try:
            self.browser.wait_until(lambda: len(self._find_action_buttons()) > 0, 8)
        except TimeoutException:
            logger.debug("Timed out waiting for Easy Apply action buttons")

        for button in self._find_action_buttons():
            label = (button.get_attribute("aria-label") or "").lower()
            button_text = (button.text or "").strip().lower()
            has_next = bool(
                button.get_attribute("data-easy-apply-next-button")
                or button.get_attribute("data-live-test-easy-apply-next-button")
            )
            has_submit = bool(button.get_attribute("data-easy-apply-submit-button"))

            if "submit application" in label or has_submit:
                if self.require_submit_confirmation:
                    self._await_submit_confirmation()
                return _click_button(button, is_submit=True)
            if (
                "continue to next step" in label
                or "review your application" in label
                or has_next
                or "next" in button_text
                or "review" in button_text
                or "continue" in button_text
            ):
                return _click_button(button, is_submit=False)

        # Fallback: primary CTA buttons
        for button in self.browser.find_elements(
            By.XPATH, "//button[contains(@class, 'artdeco-button--primary') and not(@disabled)]"
        ):
            button_text = (button.text or "").strip().lower()
            if "submit application" in button_text:
                if self.require_submit_confirmation:
                    self._await_submit_confirmation()
                return _click_button(button, is_submit=True)
            if "next" in button_text or "review" in button_text or "continue" in button_text:
                return _click_button(button, is_submit=False)

        raise Exception("Could not find actionable Easy Apply button (Next/Review/Submit)")

    def _find_action_buttons(self) -> list:
        return self.browser.find_elements(
            By.XPATH,
            "//button["
            "not(@disabled) and ("
            "contains(@aria-label, 'Submit application') or "
            "contains(@aria-label, 'Continue to next step') or "
            "contains(@aria-label, 'Review your application') or "
            "@data-easy-apply-next-button or "
            "@data-live-test-easy-apply-next-button or "
            "@data-easy-apply-submit-button"
            ")"
            "]"
        )

    def _unfollow_company(self) -> None:
        try:
            follow_checkbox = self.browser.find_element(
                By.XPATH, "//label[contains(.,'to stay up to date with their page.')]"
            )
            follow_checkbox.click()
        except Exception as e:
            logger.debug(f"Failed to unfollow company: {e}")

    def _check_for_errors(self) -> None:
        logger.debug("Checking for form errors")
        if self._easy_apply_mode == "sdui":
            sdui_errors = self.browser.execute_script(self.SDUI_WALKER + """
              let dlg=null;
              for(const el of walk(document)){
                if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;}
              }
              if(!dlg) return [];
              return [...walk(dlg)]
                .filter(e=>((e.className||'')+'').includes('artdeco-inline-feedback--error'))
                .map(e=>(e.innerText||'').trim())
                .filter(Boolean);
            """)
            if sdui_errors:
                raise Exception(f"Failed answering or file upload. {str(sdui_errors)}")

        error_elements = self.browser.find_elements(By.CLASS_NAME, 'artdeco-inline-feedback--error')
        if error_elements:
            raise Exception(f"Failed answering or file upload. {str([e.text for e in error_elements])}")

    def _discard_application(self) -> None:
        logger.debug("Discarding application")
        try:
            for dismiss_selector in [
                (By.CLASS_NAME, 'artdeco-modal__dismiss'),
                (By.XPATH, '//button[@aria-label="Dismiss"]'),
                (By.XPATH, '//button[normalize-space(text())="Dismiss"]'),
            ]:
                try:
                    self.browser.find_element(*dismiss_selector).click()
                    break
                except NoSuchElementException:
                    continue
            src.utils.time_utils.medium_sleep()
            for discard_selector in [
                (By.XPATH, '//button[@data-test-dialog-primary-btn]'),
                (By.CLASS_NAME, 'artdeco-modal__confirm-dialog-btn'),
            ]:
                try:
                    buttons = self.browser.find_elements(*discard_selector)
                    if buttons:
                        buttons[0].click()
                        break
                except Exception:
                    continue
            src.utils.time_utils.medium_sleep()
        except Exception as e:
            logger.warning(f"Failed to discard application: {e}")

    def save_job_application_process(self) -> None:
        logger.debug("Application not completed. Saving job to My Jobs, In Progress section")
        try:
            for dismiss_selector in [
                (By.CLASS_NAME, 'artdeco-modal__dismiss'),
                (By.XPATH, '//button[@aria-label="Dismiss"]'),
                (By.XPATH, '//button[normalize-space(text())="Dismiss"]'),
            ]:
                try:
                    self.browser.find_element(*dismiss_selector).click()
                    break
                except NoSuchElementException:
                    continue
            src.utils.time_utils.medium_sleep()
            for save_selector in [
                (By.XPATH, '//button[@data-test-dialog-secondary-btn]'),
                (By.CLASS_NAME, 'artdeco-modal__confirm-dialog-btn'),
            ]:
                try:
                    buttons = self.browser.find_elements(*save_selector)
                    if len(buttons) > 1:
                        buttons[1].click()
                        break
                    elif buttons:
                        buttons[0].click()
                        break
                except Exception:
                    continue
            src.utils.time_utils.medium_sleep()
        except Exception as e:
            logger.error(f"Failed to save application process: {e}")

    def _sync_easy_apply_mode(self) -> None:
        if self._sdui_dialog_present():
            self._easy_apply_mode = "sdui"
            return
        dialogs = self.browser.find_elements(By.XPATH, "//div[@role='dialog'] | //dialog")
        self._easy_apply_mode = "classic" if dialogs else self._easy_apply_mode

    def _wait_for_easy_apply_surface(self, timeout_seconds: int = 10) -> None:
        """Wait until Easy Apply dialog content or its footer actions are rendered."""
        try:
            self.browser.switch_to_default_content()
        except Exception:
            pass

        def _surface_ready() -> bool:
            if self._sdui_dialog_present():
                return True
            has_container = bool(
                self.browser.find_elements(By.CLASS_NAME, 'jobs-easy-apply-content')
                or self.browser.find_elements(By.CLASS_NAME, 'jobs-easy-apply-form')
                or self.browser.find_elements(By.CLASS_NAME, 'jobs-easy-apply-modal__content')
                or self.browser.find_elements(By.CLASS_NAME, 'artdeco-modal__content')
            )
            has_dialog = bool(self.browser.find_elements(
                By.XPATH,
                "//dialog | //*[@role='dialog'] | //*[@data-test-modal] | //div[contains(@class,'jobs-easy-apply-modal')]"
            ))
            has_actions = bool(self._find_action_buttons())
            return has_container or has_dialog or has_actions

        try:
            self.browser.wait_until(_surface_ready, timeout_seconds)
            self._sync_easy_apply_mode()
            logger.debug(f"Detected Easy Apply mode: {self._easy_apply_mode}")
        except TimeoutException:
            logger.warning("Timed out waiting for Easy Apply dialog surface to render")

    # ------------------------------------------------------------------
    # Backward-compat answer helpers (delegated to AnswerStore)
    # ------------------------------------------------------------------

    def answer_contians_company_name(self, answer: Any) -> bool:
        return self._store.answer_contians_company_name(answer)


__all__ = ["FormFiller", "SubmitConfirmationRequired"]