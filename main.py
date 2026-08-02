
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

import click
import yaml
from lib_resume_builder_CoApplyerAI import Resume, FacadeManager, ResumeGenerator, StyleManager

from constants import PLAIN_TEXT_RESUME_YAML, SECRETS_YAML, WORK_PREFERENCES_YAML
from src.job_application_profile import JobApplicationProfile
from src.logging import logger
from app_config import BROWSER_ENGINE, REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT
from src.browser_adapters import create_browser_adapter

# Add the src directory to the Python path
sys.path.append(str(Path(__file__).resolve().parent / 'src'))

from src.coapplyer_ai.authenticator import get_authenticator
from src.coapplyer_ai.bot_facade import CoApplyerAIBotFacade
from src.coapplyer_ai.job_manager import CoApplyerAIJobManager
from src.coapplyer_ai.llm.llm_manager import GPTAnswerer


class ConfigError(Exception):
    pass


class ConfigValidator:
    @staticmethod
    def validate_email(email: str) -> bool:
        return re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email) is not None

    @staticmethod
    def validate_yaml_file(yaml_path: Path) -> dict:
        try:
            with open(yaml_path, 'r') as stream:
                return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Error reading file {yaml_path}: {exc}")
        except FileNotFoundError:
            raise ConfigError(f"File not found: {yaml_path}")

    @staticmethod
    def validate_config(config_yaml_path: Path) -> dict:
        parameters = ConfigValidator.validate_yaml_file(config_yaml_path)
        required_keys = {
            'remote': bool,
            'experience_level': dict,
            'job_types': dict,
            'date': dict,
            'positions': list,
            'locations': list,
            'location_blacklist': list,
            'distance': int,
            'company_blacklist': list,
            'title_blacklist': list,
        }

        for key, expected_type in required_keys.items():
            if key not in parameters:
                if key in ['company_blacklist', 'title_blacklist', 'location_blacklist']:
                    parameters[key] = []
                else:
                    raise ConfigError(f"Missing or invalid key '{key}' in config file {config_yaml_path}")
            elif not isinstance(parameters[key], expected_type):
                if key in ['company_blacklist', 'title_blacklist', 'location_blacklist'] and parameters[key] is None:
                    parameters[key] = []
                else:
                    raise ConfigError(f"Invalid type for key '{key}' in config file {config_yaml_path}. Expected {expected_type}.")

        experience_levels = ['internship', 'entry', 'associate', 'mid_senior_level', 'director', 'executive']
        for level in experience_levels:
            if not isinstance(parameters['experience_level'].get(level), bool):
                raise ConfigError(f"Experience level '{level}' must be a boolean in config file {config_yaml_path}")

        job_types = ['full_time', 'contract', 'part_time', 'temporary', 'internship', 'other', 'volunteer']
        for job_type in job_types:
            if not isinstance(parameters['job_types'].get(job_type), bool):
                raise ConfigError(f"Job type '{job_type}' must be a boolean in config file {config_yaml_path}")

        date_filters = ['all_time', 'month', 'week', '24_hours']
        for date_filter in date_filters:
            if not isinstance(parameters['date'].get(date_filter), bool):
                raise ConfigError(f"Date filter '{date_filter}' must be a boolean in config file {config_yaml_path}")

        if not all(isinstance(pos, str) for pos in parameters['positions']):
            raise ConfigError(f"'positions' must be a list of strings in config file {config_yaml_path}")
        if not all(isinstance(loc, str) for loc in parameters['locations']):
            raise ConfigError(f"'locations' must be a list of strings in config file {config_yaml_path}")

        approved_distances = {0, 5, 10, 25, 50, 100}
        if parameters['distance'] not in approved_distances:
            raise ConfigError(f"Invalid distance value in config file {config_yaml_path}. Must be one of: {approved_distances}")

        for blacklist in ['company_blacklist', 'title_blacklist', 'location_blacklist']:
            if not isinstance(parameters.get(blacklist), list):
                raise ConfigError(f"'{blacklist}' must be a list in config file {config_yaml_path}")
            if parameters[blacklist] is None:
                parameters[blacklist] = []

        return parameters

    @staticmethod
    def validate_secrets(secrets_yaml_path: Path) -> str:
        secrets = ConfigValidator.validate_yaml_file(secrets_yaml_path)
        mandatory_secrets = ['llm_api_key']
        for secret in mandatory_secrets:
            if secret not in secrets:
                raise ConfigError(f"Missing secret '{secret}' in file {secrets_yaml_path}")
        if not secrets['llm_api_key']:
            raise ConfigError(f"llm_api_key cannot be empty in secrets file {secrets_yaml_path}.")
        return secrets['llm_api_key']


class FileManager:
    @staticmethod
    def validate_data_folder(app_data_folder: Path) -> tuple:
        if not app_data_folder.exists() or not app_data_folder.is_dir():
            raise FileNotFoundError(f"Data folder not found: {app_data_folder}")

        required_files = [SECRETS_YAML, WORK_PREFERENCES_YAML, PLAIN_TEXT_RESUME_YAML]
        missing_files = [file for file in required_files if not (app_data_folder / file).exists()]
        if missing_files:
            raise FileNotFoundError(f"Missing files in the data folder: {', '.join(missing_files)}")

        output_folder = app_data_folder / 'output'
        output_folder.mkdir(exist_ok=True)
        return (app_data_folder / SECRETS_YAML, app_data_folder / WORK_PREFERENCES_YAML, app_data_folder / PLAIN_TEXT_RESUME_YAML, output_folder)

    @staticmethod
    def file_paths_to_dict(resume_file: Path | None, plain_text_resume_file: Path) -> dict:
        if not plain_text_resume_file.exists():
            raise FileNotFoundError(f"Plain text resume file not found: {plain_text_resume_file}")
        result = {'plainTextResume': plain_text_resume_file}
        if resume_file:
            if not resume_file.exists():
                raise FileNotFoundError(f"Resume file not found: {resume_file}")
            result['resume'] = resume_file
        return result


# ---------------------------------------------------------------------------
# Selenium path — imported lazily so Playwright-only users don't need Selenium
# ---------------------------------------------------------------------------

def _init_selenium_browser():
    """Lazily import Selenium and launch Chrome. Only called with --selenium flag."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.common.exceptions import SessionNotCreatedException
    from src.utils.chrome_utils import chrome_browser_options

    service = ChromeService(ChromeDriverManager().install())
    try:
        options = chrome_browser_options(use_profile=True)
        return webdriver.Chrome(service=service, options=options)
    except SessionNotCreatedException as e:
        logger.warning("Chrome profile launch failed ({}). Retrying with isolated profile.", e)
        options = chrome_browser_options(use_profile=False)
        return webdriver.Chrome(service=service, options=options)
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Selenium browser: {str(e)}")


# ---------------------------------------------------------------------------
# Playwright MCP path — no Selenium imports
# ---------------------------------------------------------------------------

def _get_chrome_profile_path() -> str | None:
    """Return the Chrome user data dir used by playwright-mcp for session sharing."""
    try:
        from src.utils.chrome_utils import chromeProfilePath
        return str(chromeProfilePath)
    except Exception:
        return None


def _init_playwright_browser():
    """Start playwright-mcp in headless Chromium mode (SSE/HTTP transport).

    We do NOT use --user-data-dir or --browser=chrome because:
    - --browser=chrome requires a running Chrome with CDP on port 9222
    - Playwright's Chromium shares no session with the user's Chrome profile

    Instead, authentication is handled by the authenticator: it navigates
    playwright-mcp's Chromium to LinkedIn and prompts the user to log in
    via that browser window.
    """
    from src.browser_adapters.playwright_mcp_transport import PlaywrightMcpStdioSession

    logger.info("Playwright MCP: starting headless Chromium (no Selenium dependency)")
    session = PlaywrightMcpStdioSession()
    browser_adapter = create_browser_adapter("playwright", mcp_session=session)
    return None, browser_adapter  # no raw driver — everything goes through adapter


# ---------------------------------------------------------------------------
# Runtime selection
# ---------------------------------------------------------------------------

def resolve_browser_engine(use_selenium: bool, configured_engine: str = BROWSER_ENGINE) -> str:
    return "selenium" if use_selenium else configured_engine


def resolve_submit_confirmation(browser_engine: str, auto_complete: bool,
                                configured: bool = REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT) -> bool:
    """Selenium runs pause at the Review step unless --autoComplete is passed."""
    if auto_complete:
        return False
    if (browser_engine or "").strip().lower() == "selenium":
        return True
    return configured


def create_browser_runtime(browser_engine: str):
    normalized_engine = (browser_engine or BROWSER_ENGINE).strip().lower()

    if normalized_engine == "selenium":
        browser = _init_selenium_browser()
        browser_adapter = create_browser_adapter("selenium", selenium_driver=browser)
        return browser, browser_adapter

    # Default: pure Playwright MCP — no Selenium dependency at runtime
    try:
        return _init_playwright_browser()
    except (FileNotFoundError, TimeoutError, OSError, RuntimeError) as exc:
        logger.warning("Playwright MCP unavailable ({}); falling back to Selenium.", exc)
        browser = _init_selenium_browser()
        browser_adapter = create_browser_adapter("selenium", selenium_driver=browser)
        return browser, browser_adapter


def apply_run_profile(parameters: dict, demo: bool) -> dict:
    if demo:
        parameters = dict(parameters)
        parameters["trialJobLimit"] = 1
        parameters["demoMode"] = True
    return parameters


def create_and_run_bot(parameters, llm_api_key, browser_engine: str):
    browser_adapter = None
    try:
        style_manager = StyleManager()
        resume_generator = ResumeGenerator()
        with open(parameters['uploads']['plainTextResume'], "r", encoding='utf-8') as file:
            plain_text_resume = file.read()
        resume_object = Resume(plain_text_resume)
        resume_generator_manager = FacadeManager(llm_api_key, style_manager, resume_generator, resume_object, Path("data_folder/output"))

        if 'resume' not in parameters['uploads']:
            resume_generator_manager.choose_style()

        job_application_profile_object = JobApplicationProfile(plain_text_resume)

        browser, browser_adapter = create_browser_runtime(browser_engine)
        login_component = get_authenticator(driver=browser, platform='linkedin', browser_adapter=browser_adapter)
        apply_component = CoApplyerAIJobManager(browser, browser_adapter=browser_adapter)
        gpt_answerer_component = GPTAnswerer(parameters, llm_api_key)
        bot = CoApplyerAIBotFacade(login_component, apply_component)
        bot.set_job_application_profile_and_resume(job_application_profile_object, resume_object)
        bot.set_gpt_answerer_and_resume_generator(gpt_answerer_component, resume_generator_manager)
        bot.set_parameters(parameters)
        bot.start_login()
        if parameters['collectMode']:
            logger.info('Collecting')
            bot.start_collect_data()
        else:
            logger.info('Applying')
            bot.start_apply()
    except Exception as e:
        # Import WebDriverException lazily to avoid hard dependency
        try:
            from selenium.common.exceptions import WebDriverException
            if isinstance(e, WebDriverException):
                logger.error(f"WebDriver error occurred: {e}")
                return
        except ImportError:
            pass
        raise RuntimeError(f"Error running the bot: {str(e)}")
    finally:
        if browser_adapter is not None:
            browser_adapter.close()


@click.command()
@click.option('--resume', type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path), help="Path to the resume PDF file")
@click.option('--collect', is_flag=True, help="Only collect job data into data.json (no applications)")
@click.option('--selenium', is_flag=True, help="Use Selenium + ChromeDriver instead of Playwright MCP")
@click.option('--demo', is_flag=True, help="Demo mode: apply to one job only")
@click.option('--autoComplete', 'auto_complete', is_flag=True, help="Submit without pausing at the Review step")
def main(collect: bool = False, resume: Optional[Path] = None, selenium: bool = False, demo: bool = False,
         auto_complete: bool = False):
    try:
        data_folder = Path("data_folder")
        secrets_file, config_file, plain_text_resume_file, output_folder = FileManager.validate_data_folder(data_folder)

        parameters = ConfigValidator.validate_config(config_file)
        llm_api_key = ConfigValidator.validate_secrets(secrets_file)

        parameters['uploads'] = FileManager.file_paths_to_dict(resume, plain_text_resume_file)
        parameters['outputFileDirectory'] = output_folder
        parameters['collectMode'] = collect
        parameters = apply_run_profile(parameters, demo)

        if demo:
            logger.info("Demo mode enabled: one job attempt, Playwright default, human-confirmed submit")

        browser_engine = resolve_browser_engine(selenium)
        parameters['requireSubmitConfirmation'] = resolve_submit_confirmation(browser_engine, auto_complete)
        if parameters['requireSubmitConfirmation']:
            logger.info("Submit confirmation enabled: the run pauses at the Review step before submitting")
        create_and_run_bot(parameters, llm_api_key, browser_engine)
    except ConfigError as ce:
        logger.error(f"Configuration error: {str(ce)}")
        logger.error(f"Refer to the configuration guide for troubleshooting: {str(ce)}")
    except FileNotFoundError as fnf:
        logger.error(f"File not found: {str(fnf)}")
        logger.error("Ensure all required files are present in the data folder.")
    except RuntimeError as re:
        logger.error(f"Runtime error: {str(re)}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")


if __name__ == "__main__":
    main()
