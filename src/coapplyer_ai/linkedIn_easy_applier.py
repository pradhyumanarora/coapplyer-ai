import base64
import json
import os
import random
import re
import time
import traceback
from typing import List, Optional, Any, Tuple

from httpx import HTTPStatusError
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from reportlab.pdfbase.pdfmetrics import stringWidth
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement

from jobContext import JobContext
from job_application import JobApplication
from job_application_saver import ApplicationSaver
import src.utils as utils  # noqa: F401
from src.logging import logger
from src.job import Job
from src.coapplyer_ai.llm.llm_manager import GPTAnswerer
from src.browser_adapters import BrowserAdapter, SeleniumBrowserAdapter
from app_config import REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT
import src.utils.time_utils

def question_already_exists_in_data(question: str, data: List[dict]) -> bool:
        """
        Check if a question already exists in the data list.
        
        Args:
            question: The question text to search for
            data: List of question dictionaries to search through
            
        Returns:
            bool: True if question exists, False otherwise
        """
        return any(item['question'] == question for item in data)


class SubmitConfirmationRequired(Exception):
    pass

class CoApplyerAIEasyApplier:
    STATUS_SUBMITTED = "submitted"
    STATUS_SKIPPED_NOT_SUITABLE = "skipped_not_suitable"
    STATUS_AWAITING_HUMAN_CONFIRMATION = "awaiting_human_confirmation"
    SDUI_WALKER = """
function* walk(root){
    for(const el of root.querySelectorAll('*')){
        yield el;
        if(el.shadowRoot) yield* walk(el.shadowRoot);
    }
}
"""

    def __init__(self, driver: Any, resume_dir: Optional[str], set_old_answers: List[Tuple[str, str, str]],
                 gpt_answerer: GPTAnswerer, resume_generator_manager, disable_suitability_filter: bool = False,
                 browser_adapter: Optional[BrowserAdapter] = None):
        logger.debug("Initializing CoApplyerAIEasyApplier")
        if resume_dir is None or not os.path.exists(resume_dir):
            resume_dir = None
        self.driver = driver
        self.browser = browser_adapter or SeleniumBrowserAdapter(driver)
        self.resume_path = resume_dir
        self.set_old_answers = set_old_answers
        self.gpt_answerer = gpt_answerer
        self.resume_generator_manager = resume_generator_manager
        self.disable_suitability_filter = disable_suitability_filter
        self.all_data = self._load_questions_from_json()
        self.current_job = None
        self._easy_apply_mode = "classic"

        logger.debug("CoApplyerAIEasyApplier initialized successfully")

    def _load_questions_from_json(self) -> List[dict]:
        output_file = 'answers.json'
        logger.debug(f"Loading questions from JSON file: {output_file}")
        try:
            with open(output_file, 'r') as f:
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

    def check_for_premium_redirect(self, job_context: JobContext, max_attempts=3):

        job = job_context.job
        current_url = self.browser.current_url()
        attempts = 0

        while "linkedin.com/premium" in current_url and attempts < max_attempts:
            logger.warning("Redirected to linkedIn Premium page. Attempting to return to job page.")
            attempts += 1

            self.browser.get(job.link)
            time.sleep(2)
            current_url = self.browser.current_url()

        if "linkedin.com/premium" in current_url:
            logger.error(f"Failed to return to job page after {max_attempts} attempts. Cannot apply for the job.")
            raise Exception(
                f"Redirected to linkedIn Premium page and failed to return after {max_attempts} attempts. Job application aborted.")
            
    def apply_to_job(self, job: Job) -> str:
        """
        Starts the process of applying to a job.
        :param job: A job object with the job details.
        :return: None
        """
        logger.debug(f"Applying to job: {job}")
        try:
            application_status = self.job_apply(job)
            if application_status == self.STATUS_SUBMITTED:
                logger.info(f"Successfully applied to job: {job.title}")
            elif application_status == self.STATUS_SKIPPED_NOT_SUITABLE:
                logger.info(f"Skipped job after suitability check: {job.title}")
            else:
                logger.warning(f"Received unknown application status '{application_status}' for job: {job.title}")
            return application_status
        except Exception as e:
            logger.error(f"Failed to apply to job: {job.title}, error: {str(e)}")
            raise e

    def job_apply(self, job: Job) -> str:
        logger.debug(f"Starting job application for job: {job}")
        job_context = JobContext()
        job_context.job = job
        job_context.job_application = JobApplication(job)
        try:
            self.browser.get(job.link)
            logger.debug(f"Navigated to job link: {job.link}")
        except Exception as e:
            logger.error(f"Failed to navigate to job link: {job.link}, error: {str(e)}")
            raise

        utils.time_utils.medium_sleep()
        self.check_for_premium_redirect(job_context)

        try:

            self.browser.execute_script("document.activeElement.blur();")
            logger.debug("Focus removed from the active element")

            self.check_for_premium_redirect(job_context)

            easy_apply_button = self._find_easy_apply_button(job_context)

            self.check_for_premium_redirect(job_context)

            logger.debug("Retrieving job description")
            job_description = self._get_job_description()
            job.set_job_description(job_description)
            logger.debug(f"Job description set: {job_description[:100]}")

            logger.debug("Retrieving recruiter link")
            recruiter_link = self._get_job_recruiter()
            job.set_recruiter_link(recruiter_link)
            logger.debug(f"Recruiter link set: {recruiter_link}")


            self.current_job = job

            logger.debug("Passing job information to GPT Answerer")
            self.gpt_answerer.set_job(job)
            
            # Allow applying to extracted links without suitability filtering when configured.
            if self.disable_suitability_filter:
                logger.debug("DISABLE_DESCRIPTION_FILTER is enabled; skipping job suitability check")
            else:
                if not (job.description or "").strip():
                    logger.warning("Job description is empty; skipping suitability check to avoid false negatives")
                elif not self.gpt_answerer.is_job_suitable():
                    return self.STATUS_SKIPPED_NOT_SUITABLE

            logger.debug("Attempting to click 'Easy Apply' button")
            self.browser.click(easy_apply_button, hover_first=True)
            logger.debug("'Easy Apply' button clicked successfully")

            logger.debug("Filling out application form")
            self._fill_application_form(job_context)
            logger.debug(f"Job application process completed successfully for job: {job}")
            return self.STATUS_SUBMITTED

        except SubmitConfirmationRequired:
            logger.info(f"Final submit reached for {job.title}; awaiting human confirmation")
            return self.STATUS_AWAITING_HUMAN_CONFIRMATION

        except Exception as e:

            tb_str = traceback.format_exc()
            logger.error(f"Failed to apply to job: {job}, error: {tb_str}")

            logger.debug("Saving application process due to failure")
            self._save_job_application_process()

            raise Exception(f"Failed to apply to job! Original exception:\nTraceback:\n{tb_str}")

    def _find_easy_apply_button(self, job_context: JobContext) -> WebElement:
        logger.debug("Searching for 'Easy Apply' button")
        attempt = 0

        # New LinkedIn UI (2026): Easy Apply is an <a> element with aria-label="Easy Apply to this job"
        # Old LinkedIn UI: Easy Apply is a <button> with aria-label="Easy Apply to <job> at <company>"
        # Both UIs need to be supported.
        search_methods = [
            {
                'description': "classic Easy Apply button by jobs-apply-button class",
                'css': 'button.jobs-apply-button'
            },
            {
                'description': "SDUI Easy Apply anchor by openSDUIApplyFlow href",
                'css': 'a[href*=\'openSDUIApplyFlow=true\']'
            },
            {
                'description': "anchor or button with aria-label containing 'Easy Apply' (both UIs)",
                'find_elements': True,
                'xpath': '//*[self::a or self::button][contains(@aria-label, "Easy Apply")]'
            },
            {
                'description': "anchor with href containing '/apply/' and 'openSDUIApplyFlow' (new UI)",
                'xpath': '//a[contains(@href, "/apply/") and contains(@href, "openSDUIApplyFlow")]'
            },
            {
                'description': "find 'Easy Apply' buttons using jobs-apply-button class (old UI)",
                'find_elements': True,
                'xpath': '//button[contains(@class, "jobs-apply-button") and contains(., "Easy Apply")]'
            },
            {
                'description': "anchor or button with 'Easy Apply' span child text",
                'find_elements': True,
                'xpath': '//*[self::a or self::button][.//span[normalize-space(text())="Easy Apply"]]'
            },
            {
                'description': "anchor or button normalised text search",
                'xpath': '//*[self::button or self::a][normalize-space(.)="Easy Apply" or normalize-space(.)="Apply now"]'
            }
        ]

        while attempt < 2:
            self.check_for_premium_redirect(job_context)
            self._scroll_page()

            for method in search_methods:
                try:
                    logger.debug(f"Attempting search using {method['description']}")

                    if method.get('css'):
                        button = self.browser.wait_for_presence(By.CSS_SELECTOR, method['css'], 10)
                        self.browser.wait_for_visible(button, 10)
                        self.browser.wait_for_clickable(button, 10)
                        logger.debug("Found 'Easy Apply' button, attempting to click")
                        return button

                    if method.get('find_elements'):
                        buttons = self.browser.find_elements(By.XPATH, method['xpath'])
                        if buttons:
                            for index, button in enumerate(buttons):
                                try:
                                    self.browser.wait_for_visible(button, 10)
                                    self.browser.wait_for_clickable(button, 10)
                                    logger.debug(f"Found 'Easy Apply' button {index + 1}, attempting to click")
                                    return button
                                except Exception as e:
                                    logger.warning(f"Button {index + 1} found but not clickable: {e}")
                        else:
                            raise TimeoutException("No 'Easy Apply' buttons found")
                    else:
                        button = self.browser.wait_for_presence(By.XPATH, method['xpath'], 10)
                        self.browser.wait_for_visible(button, 10)
                        self.browser.wait_for_clickable(button, 10)
                        logger.debug("Found 'Easy Apply' button, attempting to click")
                        return button

                except TimeoutException:
                    logger.warning(f"Timeout during search using {method['description']}")
                except Exception as e:
                    logger.warning(
                        f"Failed to click 'Easy Apply' button using {method['description']} on attempt {attempt + 1}: {e}")

            self.check_for_premium_redirect(job_context)

            if attempt == 0:
                logger.debug("Refreshing page to retry finding 'Easy Apply' button")
                self.browser.refresh()
                time.sleep(random.randint(3, 5))
            attempt += 1

        page_url = self.browser.current_url()
        logger.error(f"No clickable 'Easy Apply' button found after 2 attempts. page url: {page_url}")
        raise Exception("No clickable 'Easy Apply' button found")

    def _get_job_description(self) -> str:
        logger.debug("Getting job description")
        try:
            # Expand description if collapsed
            for see_more_xpath in [
                '//button[@aria-label="Click to see more description"]',
                '//button[contains(@aria-label, "see more") and contains(@aria-label, "description")]',
                '//button[contains(@class, "jobs-description__footer-button")]',
            ]:
                try:
                    see_more_button = self.browser.find_element(By.XPATH, see_more_xpath)
                    self.browser.click(see_more_button, hover_first=True)
                    time.sleep(2)
                    break
                except NoSuchElementException:
                    continue

            description_selectors = [
                (By.CLASS_NAME, 'jobs-description-content__text'),
                (By.CLASS_NAME, 'job-details-about-the-job-module__description'),
                (By.XPATH, '//article[.//h2[text()="About the job"]]'),
                (By.XPATH, '//div[@id="job-details"]'),
                (By.XPATH, '//div[contains(@class, "jobs-description-content")]'),
                (By.XPATH, '//div[contains(@class, "jobs-description")]'),
                (By.XPATH, '//section[contains(@class, "jobs-description")]'),
                (By.XPATH, '//div[contains(@class, "job-view-layout")]//article'),
                (By.ID, 'job-details'),
            ]
            for by, selector in description_selectors:
                try:
                    element = self.browser.find_element(by, selector)
                    description = element.text
                    if description.strip():
                        logger.debug("Job description retrieved successfully")
                        return description
                except NoSuchElementException:
                    continue

            logger.warning("Job description element not found with any known selector, continuing with empty description")
            return ""
        except Exception:
            logger.warning(f"Error getting job description, continuing with empty: {traceback.format_exc()}")
            return ""

    def _get_job_recruiter(self):
        logger.debug("Getting job recruiter information")
        try:
            hiring_team_section = self.browser.wait_for_presence(By.XPATH, '//h2[text()="Meet the hiring team"]', 10)
            logger.debug("Hiring team section found")

            recruiter_elements = hiring_team_section.find_elements(By.XPATH,
                                                                   './/following::a[contains(@href, "linkedin.com/in/")]')

            if recruiter_elements:
                recruiter_element = recruiter_elements[0]
                recruiter_link = recruiter_element.get_attribute('href')
                logger.debug(f"Job recruiter link retrieved successfully: {recruiter_link}")
                return recruiter_link
            else:
                logger.debug("No recruiter link found in the hiring team section")
                return ""
        except Exception as e:
            logger.warning(f"Failed to retrieve recruiter information: {e}")
            return ""

    def _scroll_page(self) -> None:
        logger.debug("Scrolling the page")
        for _ in range(3):
            self.browser.execute_script("window.scrollBy(0, window.innerHeight);")
            time.sleep(0.5)
        self.browser.execute_script("window.scrollTo(0, 0);")

    def _sdui_dialog_present(self) -> bool:
            try:
                    return bool(self.browser.execute_script(self.SDUI_WALKER + """
                        for(const el of walk(document)){
                            if(el.getAttribute && el.getAttribute('role')==='dialog') return true;
                        }
                        return false;
                    """))
            except Exception:
                    return False

    def _sdui_read_fields(self) -> list[dict[str, Any]]:
            return self.browser.execute_script(self.SDUI_WALKER + """
                let dlg=null;
                for(const el of walk(document)){
                    if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;}
                }
                if(!dlg) return [];
                const all=[...walk(dlg)];
                const out=[];
                for(const el of all){
                    const t=(el.tagName||'').toLowerCase();
                    if(t==='input'||t==='select'||t==='textarea'){
                        let label='';
                        if(el.id){
                            const l=all.find(x=>x.tagName==='LABEL'&&x.getAttribute('for')===el.id);
                            if(l) label=(l.innerText||'').trim();
                        }
                        if(!label){
                            const p=el.closest('label');
                            if(p) label=(p.innerText||'').trim();
                        }
                        const o={
                            tag:t,
                            type:el.type||'',
                            id:el.id||'',
                            name:el.name||'',
                            value:el.value||'',
                            checked:!!el.checked,
                            required:!!el.required,
                            label:label,
                        };
                        if(t==='select') o.options=[...el.options].map(op=>op.text);
                        out.push(o);
                    }
                }
                return out;
            """)

    def _sdui_list_buttons(self) -> list[str]:
            return self.browser.execute_script(self.SDUI_WALKER + """
                let dlg=null;
                for(const el of walk(document)){
                    if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;}
                }
                if(!dlg) return [];
                return [...walk(dlg)]
                    .filter(e=>e.tagName==='BUTTON')
                    .map(b=>b.getAttribute('aria-label')||b.innerText.trim())
                    .filter(Boolean);
            """)

    def _sdui_click_button(self, regex: str) -> str:
            return self.browser.execute_script(self.SDUI_WALKER + """
                let dlg=null;
                for(const el of walk(document)){
                    if(el.getAttribute&&el.getAttribute('role')==='dialog'){dlg=el;break;}
                }
                if(!dlg) return 'nodialog';
                const re=new RegExp(arguments[0],'i');
                const b=[...walk(dlg)].filter(e=>e.tagName==='BUTTON')
                    .find(x=>re.test(x.getAttribute('aria-label')||'')||re.test(x.innerText||''));
                if(!b) return 'notfound';
                b.click();
                return 'clicked';
            """, regex)

    def _sdui_set_input(self, field_id: str, value: str) -> str:
            return self.browser.execute_script(self.SDUI_WALKER + """
                const all=[...walk(document)];
                const el=all.find(e=>e.id===arguments[0]);
                if(!el) return 'missing';
                el.value='';
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.value=arguments[1];
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
                const r=all.find(e=>e.id===arguments[0]);
                return r&&r.value!==undefined ? r.value : '';
            """, field_id, value)

    def _sdui_set_select(self, field_id: str, visible_text: str) -> str:
            return self.browser.execute_script(self.SDUI_WALKER + """
                const all=[...walk(document)];
                const el=all.find(e=>e.id===arguments[0]);
                if(!el) return 'missing';
                const o=[...el.options].find(x=>x.text.trim()===arguments[1]);
                if(!o) return 'noopt';
                el.value=o.value;
                el.dispatchEvent(new Event('input',{bubbles:true}));
                el.dispatchEvent(new Event('change',{bubbles:true}));
                const r=all.find(e=>e.id===arguments[0]);
                return r&&r.selectedOptions&&r.selectedOptions.length ? r.selectedOptions[0].text : '';
            """, field_id, visible_text)

    def _sdui_set_checked(self, field_id: str, want_checked: bool) -> bool:
            return bool(self.browser.execute_script(self.SDUI_WALKER + """
                const all=[...walk(document)];
                const el=all.find(e=>e.id===arguments[0]);
                if(!el) return false;
                if(!!el.checked !== !!arguments[1]) el.click();
                const r=all.find(e=>e.id===arguments[0]);
                return !!(r && r.checked);
            """, field_id, want_checked))

    def _sdui_fill_geo(self, field_id: str, city_text: str) -> None:
            logger.debug(f"Filling SDUI GEO field {field_id} with city text: {city_text}")
            # Focus the field via JS, then type via the browser adapter (works for both Selenium and Playwright MCP).
            self.browser.execute_script(self.SDUI_WALKER + """
                const el=[...walk(document)].find(e=>e.id===arguments[0]);
                if(el) el.focus();
            """, field_id)
            if self.driver is not None:
                from selenium.webdriver import ActionChains as _ActionChains
                _ActionChains(self.driver).send_keys(city_text).perform()
            else:
                # Playwright MCP path: type into the focused field via JS input simulation.
                self.browser.execute_script(self.SDUI_WALKER + """
                    const el=[...walk(document)].find(e=>e.id===arguments[0]);
                    if(!el) return;
                    el.value = arguments[1];
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                """, field_id, city_text)
            time.sleep(2.5)
            pick_result = self.browser.execute_script(self.SDUI_WALKER + """
                const q=(arguments[0]||'').toLowerCase();
                const opts=[...walk(document)].filter(e=>e.getAttribute&&e.getAttribute('role')==='option');
                let pick=opts.find(o=>(o.innerText||'').toLowerCase().includes(q));
                if(!pick) pick=opts[0];
                if(!pick) return 'nooption';
                pick.click();
                return 'picked:' + (pick.innerText||'').trim();
            """, city_text)
            logger.debug(f"SDUI GEO pick result: {pick_result}")

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
            # Best-effort reset in case webdriver is already in default content.
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
            has_dialog = bool(
                self.browser.find_elements(
                    By.XPATH,
                    "//dialog | //*[@role='dialog'] | //*[@data-test-modal] | //div[contains(@class,'jobs-easy-apply-modal')]"
                )
            )
            has_actions = bool(
                self.browser.find_elements(
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
            )
            return has_container or has_dialog or has_actions

        try:
            self.browser.wait_until(_surface_ready, timeout_seconds)
            self._sync_easy_apply_mode()
            logger.debug(f"Detected Easy Apply mode: {self._easy_apply_mode}")
        except TimeoutException:
            logger.warning("Timed out waiting for Easy Apply dialog surface to render")

    def _fill_application_form(self, job_context : JobContext):
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
                    utils.time_utils.short_sleep()
                    self._wait_for_easy_apply_surface(timeout_seconds=8)
                    continue
                raise Exception("Easy Apply form container is unavailable; cannot continue to Next/Submit step")
            if self._next_or_submit():
                ApplicationSaver.save(job_application)
                logger.debug("Application form submitted")
                break

    def _get_existing_answer(self, question_text: str, question_type: str) -> Optional[str]:
        question_sanitized = self._sanitize_text(question_text)
        for item in self.all_data:
            if question_sanitized in item.get('question', '') and item.get('type') == question_type:
                return item.get('answer')
        return None

    def _decide_sdui_answer(self, field: dict[str, Any]) -> Optional[str]:
        label = (field.get("label") or "unknown").lower().strip()
        tag = (field.get("tag") or "").lower()
        field_type = (field.get("type") or "").lower()
        options = field.get("options") or []

        if tag == "select":
            existing = self._get_existing_answer(label, "dropdown")
            if existing:
                return existing
            answer = self.gpt_answerer.answer_question_from_options(label, options)
            self._save_questions_to_json({'type': 'dropdown', 'question': label, 'answer': answer})
            self.all_data = self._load_questions_from_json()
            return answer

        if tag in ("input", "textarea") and field_type not in ("checkbox", "radio", "file", "hidden"):
            question_type = "numeric" if self._is_numeric_field_id_or_type(field.get("id", ""), field_type) else "textbox"
            existing = self._get_existing_answer(label, question_type)
            if existing:
                return existing
            if question_type == "numeric":
                answer = self.gpt_answerer.answer_question_numeric(label)
            else:
                answer = self.gpt_answerer.answer_question_textual_wide_range(label)
            self._save_questions_to_json({'type': question_type, 'question': label, 'answer': answer})
            self.all_data = self._load_questions_from_json()
            return str(answer)

        return None

    def _is_numeric_field_id_or_type(self, field_id: str, field_type: str) -> bool:
        field_id_l = (field_id or "").lower()
        field_type_l = (field_type or "").lower()
        return 'numeric' in field_id_l or field_type_l == 'number' or (field_type_l == 'text' and 'numeric' in field_id_l)

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
                        f"SDUI input verification mismatch for field {field_id}: expected '{answer}', got '{read_back}'"
                    )

            job_context.job_application.save_application_data(
                {'type': 'sdui', 'question': label or field_id, 'answer': str(answer)}
            )
        return True

    def _next_or_submit(self):
        logger.debug("Clicking 'Next' or 'Submit' button")

        self._sync_easy_apply_mode()
        if self._easy_apply_mode == "sdui":
            button_labels = [label.lower() for label in self._sdui_list_buttons()]
            logger.debug(f"Found SDUI button labels: {button_labels}")
            if any("submit application" in label for label in button_labels):
                if REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT:
                    logger.info("Submit application detected in SDUI dialog; pausing for human confirmation")
                    raise SubmitConfirmationRequired()
                self._unfollow_company()
                utils.time_utils.short_sleep()
                result = self._sdui_click_button(r"Submit application")
                logger.debug(f"SDUI submit click result: {result}")
                utils.time_utils.short_sleep()
                return True
            if any("review your application" in label for label in button_labels):
                result = self._sdui_click_button(r"Review your application")
                logger.debug(f"SDUI review click result: {result}")
                utils.time_utils.medium_sleep()
                self._check_for_errors()
                return False
            if any("continue to next step" in label for label in button_labels):
                result = self._sdui_click_button(r"Continue to next step")
                logger.debug(f"SDUI continue click result: {result}")
                utils.time_utils.medium_sleep()
                self._check_for_errors()
                return False

        def _click_button(button: WebElement, is_submit: bool) -> bool:
            if is_submit:
                logger.debug("Submit button found, submitting application")
                self._unfollow_company()
                utils.time_utils.short_sleep()
                button.click()
                utils.time_utils.short_sleep()
                return True

            logger.debug("Next/review button found, moving to next step")
            utils.time_utils.short_sleep()
            button.click()
            utils.time_utils.medium_sleep()
            self._check_for_errors()
            return False

        def _find_action_buttons() -> list[WebElement]:
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

        # Wait briefly for modal footer controls to render after field interactions.
        try:
            self.browser.wait_until(lambda: len(_find_action_buttons()) > 0, 8)
        except TimeoutException:
            logger.debug("Timed out waiting for Easy Apply action buttons")

        action_buttons = _find_action_buttons()
        logger.debug(f"Found {len(action_buttons)} action button candidate(s)")
        for button in action_buttons:
            label = (button.get_attribute("aria-label") or "").lower()
            button_text = (button.text or "").strip().lower()
            has_next_data_attr = bool(
                button.get_attribute("data-easy-apply-next-button")
                or button.get_attribute("data-live-test-easy-apply-next-button")
            )
            has_submit_data_attr = bool(button.get_attribute("data-easy-apply-submit-button"))

            if "submit application" in label or has_submit_data_attr:
                if REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT:
                    logger.info("Submit application detected; pausing for human confirmation")
                    raise SubmitConfirmationRequired()
                return _click_button(button, is_submit=True)
            if (
                "continue to next step" in label
                or "review your application" in label
                or has_next_data_attr
                or "next" in button_text
                or "review" in button_text
                or "continue" in button_text
            ):
                return _click_button(button, is_submit=False)

        # Fallback path: primary CTA buttons without reliable aria-label.
        logger.debug("Falling back to primary CTA button detection")
        fallback_buttons = self.browser.find_elements(
            By.XPATH,
            "//button[contains(@class, 'artdeco-button--primary') and not(@disabled)]"
        )
        for button in fallback_buttons:
            button_text = (button.text or "").strip().lower()
            if "submit application" in button_text:
                return _click_button(button, is_submit=True)
            if "next" in button_text or "review" in button_text or "continue" in button_text:
                return _click_button(button, is_submit=False)

        logger.error("Could not find a Next/Review/Submit button in the Easy Apply dialog")
        raise Exception("Could not find actionable Easy Apply button (Next/Review/Submit)")

    def _unfollow_company(self) -> None:
        try:
            logger.debug("Unfollowing company")
            follow_checkbox = self.browser.find_element(
                By.XPATH, "//label[contains(.,'to stay up to date with their page.')]")
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
                logger.error(f"SDUI form submission failed with errors: {sdui_errors}")
                raise Exception(f"Failed answering or file upload. {str(sdui_errors)}")

        error_elements = self.browser.find_elements(By.CLASS_NAME, 'artdeco-inline-feedback--error')
        if error_elements:
            logger.error(f"Form submission failed with errors: {error_elements}")
            raise Exception(f"Failed answering or file upload. {str([e.text for e in error_elements])}")

    def _discard_application(self) -> None:
        logger.debug("Discarding application")
        try:
            # Close the Easy Apply dialog
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
            utils.time_utils.medium_sleep()
            # Click 'Discard' on the confirmation modal
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
            utils.time_utils.medium_sleep()
        except Exception as e:
            logger.warning(f"Failed to discard application: {e}")

    def _save_job_application_process(self) -> None:
        logger.debug("Application not completed. Saving job to My Jobs, In Progess section")
        try:
            # Close the Easy Apply dialog
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
            utils.time_utils.medium_sleep()
            # Click 'Save' on the confirmation modal (second button = Save)
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
            utils.time_utils.medium_sleep()
        except Exception as e:
            logger.error(f"Failed to save application process: {e}")

    def fill_up(self, job_context : JobContext) -> bool:
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

            # Try multiple selectors for the form container
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
                # Fallback: use the dialog element directly
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

                    # Some LinkedIn steps render only footer actions with no form wrapper classes.
                    action_buttons = self.browser.find_elements(
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
                    if action_buttons:
                        logger.warning(
                            "Easy Apply form container not found, but action buttons exist; continuing to next/submit step"
                        )
                        return True

                    logger.error(
                        "Could not find Easy Apply form container."
                        f" dialog_count={len(dialogs)} form_count={len(form_roots)} action_count={len(action_buttons)}"
                    )
                    return False

            input_elements = easy_apply_content.find_elements(By.CLASS_NAME, 'jobs-easy-apply-form-section__grouping')
            for element in input_elements:
                self._process_form_element(element, job_context)
            return True
        except Exception as e:
            logger.error(f"Failed to find form elements: {e}")
            return False

    def _process_form_element(self, element: WebElement, job_context : JobContext) -> None:
        logger.debug("Processing form element")
        if self._is_upload_field(element):
            self._handle_upload_fields(element, job_context)
        else:
            self._fill_additional_questions(job_context)

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
                    return True
                except NoSuchElementException:
                    logger.warning(f"Country {country} not found in dropdown options")

        options = self.browser.get_select_options(dropdown)
        logger.debug(f"Dropdown options found: {options}")

        parent_element = dropdown.find_element(By.XPATH, '../..')

        label_elements = parent_element.find_elements(By.TAG_NAME, 'label')
        if label_elements:
            question_text = label_elements[0].text.lower()
        else:
            question_text = "unknown"

        logger.debug(f"Detected question text: {question_text}")

        existing_answer = None
        current_question_sanitized = self._sanitize_text(question_text) 
        for item in self.all_data:
            if current_question_sanitized in item['question'] and item['type'] == 'dropdown':
                existing_answer = item['answer']
                break

        if existing_answer:
            logger.debug(f"Found existing answer for question '{question_text}': {existing_answer}")
        else:
            logger.debug(f"No existing answer found, querying model for: {question_text}")
            existing_answer = self.gpt_answerer.answer_question_from_options(question_text, options)
            logger.debug(f"Model provided answer: {existing_answer}")
            self._save_questions_to_json({'type': 'dropdown', 'question': question_text, 'answer': existing_answer})
            self.all_data = self._load_questions_from_json()

        if existing_answer in options:
            self.browser.select_by_visible_text(dropdown, existing_answer)
            logger.debug(f"Selected option: {existing_answer}")
            self.job_application.save_application_data({'type': 'dropdown', 'question': question_text, 'answer': existing_answer})
        else:
            logger.error(f"Answer '{existing_answer}' is not a valid option in the dropdown")
            raise Exception(f"Invalid option selected: {existing_answer}")

    def _is_upload_field(self, element: WebElement) -> bool:
        is_upload = bool(element.find_elements(By.XPATH, ".//input[@type='file']"))
        logger.debug(f"Element is upload field: {is_upload}")
        return is_upload

    def _handle_upload_fields(self, element: WebElement, job_context: JobContext) -> None:
        logger.debug("Handling upload fields")

        try:
            show_more_button = self.browser.find_element(By.XPATH,
                                                        "//button[contains(@aria-label, 'Show more resumes')]")
            show_more_button.click()
            logger.debug("Clicked 'Show more resumes' button")
        except NoSuchElementException:
            logger.debug("'Show more resumes' button not found, continuing...")

        file_upload_elements = self.browser.find_elements(By.XPATH, "//input[@type='file']")
        for element in file_upload_elements:
            # Get parent label text for resume-or-cover detection.
            # Use adapter-aware parent traversal: try locator's find_element first, fall back to JS.
            try:
                parent = element.find_element(By.XPATH, "..")
                parent_text = (parent.text if hasattr(parent, "text") else "").lower()
            except Exception:
                try:
                    parent_text = str(self.browser.execute_script(
                        "const el = arguments[0]; return el && el.parentElement ? el.parentElement.innerText : '';",
                        element,
                    ) or "").lower()
                except Exception:
                    parent_text = ""

            self.browser.execute_script("arguments[0].classList.remove('hidden')", element)

            output = self.gpt_answerer.resume_or_cover(parent_text)
            if 'resume' in output:
                logger.debug("Uploading resume")
                if self.resume_path is not None and self.resume_path.resolve().is_file():
                    _upload_path = str(self.resume_path.resolve())
                    if hasattr(element, "send_keys"):
                        element.send_keys(_upload_path)
                    else:
                        self.browser.fill_text(element, _upload_path)
                    job_context.job.resume_path = _upload_path
                    job_context.job_application.resume_path = _upload_path
                    logger.debug(f"Resume uploaded from path: {_upload_path}")
                else:
                    logger.debug("Resume path not found or invalid, generating new resume")
                    self._create_and_upload_resume(element, job_context)
            elif 'cover' in output:
                logger.debug("Uploading cover letter")
                self._create_and_upload_cover_letter(element, job_context)

        logger.debug("Finished handling upload fields")

    def _create_and_upload_resume(self, element, job_context : JobContext):
        job = job_context.job
        job_application = job_context.job_application
        logger.debug("Starting the process of creating and uploading resume.")
        folder_path = 'generated_cv'

        try:
            if not os.path.exists(folder_path):
                logger.debug(f"Creating directory at path: {folder_path}")
            os.makedirs(folder_path, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory: {folder_path}. Error: {e}")
            raise

        while True:
            try:
                timestamp = int(time.time())
                file_path_pdf = os.path.join(folder_path, f"CV_{timestamp}.pdf")
                logger.debug(f"Generated file path for resume: {file_path_pdf}")

                logger.debug(f"Generating resume for job: {job.title} at {job.company}")
                resume_pdf_base64 = self.resume_generator_manager.pdf_base64(job_description_text=job.description)
                with open(file_path_pdf, "xb") as f:
                    f.write(base64.b64decode(resume_pdf_base64))
                logger.debug(f"Resume successfully generated and saved to: {file_path_pdf}")

                break
            except HTTPStatusError as e:
                if e.response.status_code == 429:

                    retry_after = e.response.headers.get('retry-after')
                    retry_after_ms = e.response.headers.get('retry-after-ms')

                    if retry_after:
                        wait_time = int(retry_after)
                        logger.warning(f"Rate limit exceeded, waiting {wait_time} seconds before retrying...")
                    elif retry_after_ms:
                        wait_time = int(retry_after_ms) / 1000.0
                        logger.warning(f"Rate limit exceeded, waiting {wait_time} milliseconds before retrying...")
                    else:
                        wait_time = 20
                        logger.warning(f"Rate limit exceeded, waiting {wait_time} seconds before retrying...")

                    time.sleep(wait_time)
                else:
                    logger.error(f"HTTP error: {e}")
                    raise

            except Exception as e:
                logger.error(f"Failed to generate resume: {e}")
                tb_str = traceback.format_exc()
                logger.error(f"Traceback: {tb_str}")
                if "RateLimitError" in str(e):
                    logger.warning("Rate limit error encountered, retrying...")
                    time.sleep(20)
                else:
                    raise

        file_size = os.path.getsize(file_path_pdf)
        max_file_size = 2 * 1024 * 1024  # 2 MB
        logger.debug(f"Resume file size: {file_size} bytes")
        if file_size > max_file_size:
            logger.error(f"Resume file size exceeds 2 MB: {file_size} bytes")
            raise ValueError("Resume file size exceeds the maximum limit of 2 MB.")

        allowed_extensions = {'.pdf', '.doc', '.docx'}
        file_extension = os.path.splitext(file_path_pdf)[1].lower()
        logger.debug(f"Resume file extension: {file_extension}")
        if file_extension not in allowed_extensions:
            logger.error(f"Invalid resume file format: {file_extension}")
            raise ValueError("Resume file format is not allowed. Only PDF, DOC, and DOCX formats are supported.")

        try:
            logger.debug(f"Uploading resume from path: {file_path_pdf}")
            _abs_path = os.path.abspath(file_path_pdf)
            if hasattr(element, "send_keys"):
                element.send_keys(_abs_path)
            else:
                self.browser.fill_text(element, _abs_path)
            job.resume_path = _abs_path
            job_application.resume_path = _abs_path
            time.sleep(2)
            logger.debug(f"Resume created and uploaded successfully: {file_path_pdf}")
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Resume upload failed: {tb_str}")
            raise Exception(f"Upload failed: \nTraceback:\n{tb_str}")

    def _create_and_upload_cover_letter(self, element: WebElement, job_context : JobContext) -> None:
        job = job_context.job
        logger.debug("Starting the process of creating and uploading cover letter.")

        cover_letter_text = self.gpt_answerer.answer_question_textual_wide_range("Write a cover letter")

        folder_path = 'generated_cv'

        try:

            if not os.path.exists(folder_path):
                logger.debug(f"Creating directory at path: {folder_path}")
            os.makedirs(folder_path, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory: {folder_path}. Error: {e}")
            raise

        while True:
            try:
                timestamp = int(time.time())
                file_path_pdf = os.path.join(folder_path, f"Cover_Letter_{timestamp}.pdf")
                logger.debug(f"Generated file path for cover letter: {file_path_pdf}")

                c = canvas.Canvas(file_path_pdf, pagesize=A4)
                page_width, page_height = A4
                text_object = c.beginText(50, page_height - 50)
                text_object.setFont("Helvetica", 12)

                max_width = page_width - 100
                bottom_margin = 50
                available_height = page_height - bottom_margin - 50

                def split_text_by_width(text, font, font_size, max_width):
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

                lines = split_text_by_width(cover_letter_text, "Helvetica", 12, max_width)

                for line in lines:
                    text_height = text_object.getY()
                    if text_height > bottom_margin:
                        text_object.textLine(line)
                    else:

                        c.drawText(text_object)
                        c.showPage()
                        text_object = c.beginText(50, page_height - 50)
                        text_object.setFont("Helvetica", 12)
                        text_object.textLine(line)

                c.drawText(text_object)
                c.save()
                logger.debug(f"Cover letter successfully generated and saved to: {file_path_pdf}")

                break
            except Exception as e:
                logger.error(f"Failed to generate cover letter: {e}")
                tb_str = traceback.format_exc()
                logger.error(f"Traceback: {tb_str}")
                raise

        file_size = os.path.getsize(file_path_pdf)
        max_file_size = 2 * 1024 * 1024  # 2 MB
        logger.debug(f"Cover letter file size: {file_size} bytes")
        if file_size > max_file_size:
            logger.error(f"Cover letter file size exceeds 2 MB: {file_size} bytes")
            raise ValueError("Cover letter file size exceeds the maximum limit of 2 MB.")

        allowed_extensions = {'.pdf', '.doc', '.docx'}
        file_extension = os.path.splitext(file_path_pdf)[1].lower()
        logger.debug(f"Cover letter file extension: {file_extension}")
        if file_extension not in allowed_extensions:
            logger.error(f"Invalid cover letter file format: {file_extension}")
            raise ValueError("Cover letter file format is not allowed. Only PDF, DOC, and DOCX formats are supported.")

        try:
            logger.debug(f"Uploading cover letter from path: {file_path_pdf}")
            _abs_path = os.path.abspath(file_path_pdf)
            if hasattr(element, "send_keys"):
                element.send_keys(_abs_path)
            else:
                self.browser.fill_text(element, _abs_path)
            job.cover_letter_path = _abs_path
            job_context.job_application.cover_letter_path = _abs_path
            time.sleep(2)
            logger.debug(f"Cover letter created and uploaded successfully: {file_path_pdf}")
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Cover letter upload failed: {tb_str}")
            raise Exception(f"Upload failed: \nTraceback:\n{tb_str}")

    def _fill_additional_questions(self, job_context : JobContext) -> None:
        logger.debug("Filling additional questions")
        form_sections = self.browser.find_elements(By.CLASS_NAME, 'jobs-easy-apply-form-section__grouping')
        for section in form_sections:
            self._process_form_section(job_context,section)

    def _process_form_section(self,job_context : JobContext, section: WebElement) -> None:
        logger.debug("Processing form section")
        if self._handle_terms_of_service(job_context,section):
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

    def _handle_terms_of_service(self,job_context: JobContext, element: WebElement) -> bool:
        checkbox = element.find_elements(By.TAG_NAME, 'label')
        if checkbox and any(
                term in checkbox[0].text.lower() for term in ['terms of service', 'privacy policy', 'terms of use']):
            checkbox[0].click()
            logger.debug("Clicked terms of service checkbox")
            return True
        return False

    def _find_and_handle_radio_question(self,job_context : JobContext, section: WebElement) -> bool:
        job_application = job_context.job_application
        question = section.find_element(By.CLASS_NAME, 'jobs-easy-apply-form-element')
        radios = question.find_elements(By.CLASS_NAME, 'fb-text-selectable__option')
        if radios:
            question_text = section.text.lower()
            options = [radio.text.lower() for radio in radios]

            existing_answer = None
            current_question_sanitized = self._sanitize_text(question_text) 
            for item in self.all_data:
                if current_question_sanitized in item['question'] and item['type'] == 'radio':
                    existing_answer = item

                    break

            if existing_answer:
                self._select_radio(radios, existing_answer['answer'])
                job_application.save_application_data(existing_answer)
                logger.debug("Selected existing radio answer")
                return True

            answer = self.gpt_answerer.answer_question_from_options(question_text, options)
            self._save_questions_to_json({'type': 'radio', 'question': question_text, 'answer': answer})
            self.all_data = self._load_questions_from_json()
            job_application.save_application_data({'type': 'radio', 'question': question_text, 'answer': answer})
            self._select_radio(radios, answer)
            logger.debug("Selected new radio answer")
            return True
        return False

    def _find_and_handle_textbox_question(self,job_context : JobContext, section: WebElement) -> bool:
        logger.debug("Searching for text fields in the section.")
        text_fields = section.find_elements(By.TAG_NAME, 'input') + section.find_elements(By.TAG_NAME, 'textarea')

        if text_fields:
            text_field = text_fields[0]
            question_text = section.find_element(By.TAG_NAME, 'label').text.lower().strip()
            logger.debug(f"Found text field with label: {question_text}")

            is_numeric = self._is_numeric_field(text_field)
            logger.debug(f"Is the field numeric? {'Yes' if is_numeric else 'No'}")

            question_type = 'numeric' if is_numeric else 'textbox'

            # Check if it's a cover letter field (case-insensitive)
            is_cover_letter = 'cover letter' in question_text.lower()
            logger.debug(f"question: {question_text}")
            # Look for existing answer if it's not a cover letter field
            existing_answer = None
            if not is_cover_letter:
                current_question_sanitized = self._sanitize_text(question_text) 
                for item in self.all_data:
                    if item['question'] == current_question_sanitized and item.get('type') == question_type:
                        existing_answer = item['answer']
                        logger.debug(f"Found existing answer: {existing_answer}")
                        break

            if existing_answer and not is_cover_letter:
                answer = existing_answer
                logger.debug(f"Using existing answer: {answer}")
            else:
                if is_numeric:
                    answer = self.gpt_answerer.answer_question_numeric(question_text)
                    logger.debug(f"Generated numeric answer: {answer}")
                else:
                    answer = self.gpt_answerer.answer_question_textual_wide_range(question_text)
                    logger.debug(f"Generated textual answer: {answer}")

            self._enter_text(text_field, answer)
            logger.debug("Entered answer into the textbox.")

            job_context.job_application.save_application_data({'type': question_type, 'question': question_text, 'answer': answer})

            # Save non-cover letter answers
            if not is_cover_letter and not existing_answer:
                self._save_questions_to_json({'type': question_type, 'question': question_text, 'answer': answer})
                self.all_data = self._load_questions_from_json()
                logger.debug("Saved non-cover letter answer to JSON.")
            return True

        logger.debug("No text fields found in the section.")
        return False

    def _find_and_handle_date_question(self, job_context : JobContext, section: WebElement) -> bool:
        job_application = job_context.job_application
        date_fields = section.find_elements(By.CLASS_NAME, 'artdeco-datepicker__input ')
        if date_fields:
            date_field = date_fields[0]
            question_text = section.text.lower()
            answer_date = self.gpt_answerer.answer_question_date()
            answer_text = answer_date.strftime("%Y-%m-%d")

            existing_answer = None
            current_question_sanitized = self._sanitize_text(question_text) 
            for item in self.all_data:
                if current_question_sanitized in item['question'] and item['type'] == 'date':
                    existing_answer = item
                    break

            if existing_answer:
                self._enter_text(date_field, existing_answer['answer'])
                logger.debug("Entered existing date answer")
                job_application.save_application_data(existing_answer)
                return True

            self._save_questions_to_json({'type': 'date', 'question': question_text, 'answer': answer_text})
            self.all_data = self._load_questions_from_json()
            job_application.save_application_data({'type': 'date', 'question': question_text, 'answer': answer_text})
            self._enter_text(date_field, answer_text)
            logger.debug("Entered new date answer")
            return True
        return False

    def _find_and_handle_dropdown_question(self,job_context : JobContext, section: WebElement) -> bool:
        job_application = job_context.job_application
        try:
            question = section.find_element(By.CLASS_NAME, 'jobs-easy-apply-form-element')

            dropdowns = question.find_elements(By.TAG_NAME, 'select')
            if not dropdowns:
                dropdowns = section.find_elements(By.CSS_SELECTOR, '[data-test-text-entity-list-form-select]')

            if dropdowns:
                dropdown = dropdowns[0]
                options = self.browser.get_select_options(dropdown)

                logger.debug(f"Dropdown options found: {options}")

                question_text = question.find_element(By.TAG_NAME, 'label').text.lower()
                logger.debug(f"Processing dropdown or combobox question: {question_text}")

                current_selection = self.browser.get_selected_option_text(dropdown)
                logger.debug(f"Current selection: {current_selection}")

                existing_answer = None
                current_question_sanitized = self._sanitize_text(question_text) 
                for item in self.all_data:
                    if current_question_sanitized in item['question'] and item['type'] == 'dropdown':
                        existing_answer = item['answer']
                        break

                if existing_answer:
                    logger.debug(f"Found existing answer for question '{question_text}': {existing_answer}")
                    job_application.save_application_data({'type': 'dropdown', 'question': question_text, 'answer': existing_answer})
                    if current_selection != existing_answer:
                        logger.debug(f"Updating selection to: {existing_answer}")
                        self.browser.select_by_visible_text(dropdown, existing_answer)
                else:
                    logger.debug(f"No existing answer found, querying model for: {question_text}")
                    answer = self.gpt_answerer.answer_question_from_options(question_text, options)
                    self._save_questions_to_json({'type': 'dropdown', 'question': question_text, 'answer': answer})
                    self.all_data = self._load_questions_from_json()
                    job_application.save_application_data({'type': 'dropdown', 'question': question_text, 'answer': answer})
                    self.browser.select_by_visible_text(dropdown, answer)
                    logger.debug(f"Selected new dropdown answer: {answer}")

                return True

            else:

                logger.debug(f"No dropdown found. Logging elements for debugging.")
                elements = section.find_elements(By.XPATH, ".//*")
                logger.debug(f"Elements found: {[element.tag_name for element in elements]}")
                return False

        except Exception as e:
            logger.warning(f"Failed to handle dropdown or combobox question: {e}", exc_info=True)
            return False

    def _is_numeric_field(self, field: WebElement) -> bool:
        field_type = (field.get_attribute('type') or '').lower()
        field_id = (field.get_attribute("id") or '').lower()
        is_numeric = self._is_numeric_field_id_or_type(field_id, field_type)
        logger.debug(f"Field type: {field_type}, Field ID: {field_id}, Is numeric: {is_numeric}")
        return is_numeric

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

    def _save_questions_to_json(self, question_data: dict) -> None:
        output_file = 'answers.json'
        question_data['question'] = self._sanitize_text(question_data['question'])

        logger.debug(f"Checking if question data already exists: {question_data}")
        try:
            with open(output_file, 'r+') as f:
                try:
                    data = json.load(f)
                    if not isinstance(data, list):
                        raise ValueError("JSON file format is incorrect. Expected a list of questions.")
                except json.JSONDecodeError:
                    logger.error("JSON decoding failed")
                    data = []

                should_be_saved: bool = not question_already_exists_in_data(question_data['question'], data) and not self.answer_contians_company_name(question_data['answer'])

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
            with open(output_file, 'w') as f:
                json.dump([question_data], f, indent=4)
            logger.debug("Question data saved successfully to new JSON file")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error saving questions data to JSON file: {tb_str}")
            raise Exception(f"Error saving questions data to JSON file: \nTraceback:\n{tb_str}")

    def _sanitize_text(self, text: str) -> str:
        sanitized_text = text.lower().strip().replace('"', '').replace('\\', '')
        sanitized_text = re.sub(r'[\x00-\x1F\x7F]', '', sanitized_text).replace('\n', ' ').replace('\r', '').rstrip(',')
        logger.debug(f"Sanitized text: {sanitized_text}")
        return sanitized_text

    def _find_existing_answer(self, question_text):
        for item in self.all_data:
            if self._sanitize_text(item['question']) == self._sanitize_text(question_text):
                return item
        return None

    def answer_contians_company_name(self,answer:Any)->bool:
        return isinstance(answer,str) and not self.current_job.company is None and self.current_job.company in answer

