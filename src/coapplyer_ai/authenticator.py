import random
import time

from abc import ABC, abstractmethod
from selenium.common.exceptions import NoSuchElementException, TimeoutException, NoAlertPresentException, TimeoutException, UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from src.browser_adapters import BrowserAdapter, SeleniumBrowserAdapter
from src.logging import logger

def get_authenticator(driver=None, platform='linkedin', browser_adapter: BrowserAdapter | None = None):
    if platform == 'linkedin':
        return LinkedInAuthenticator(driver, browser_adapter=browser_adapter)
    else:
        raise NotImplementedError(f"Platform {platform} not implemented yet.")

class CoApplyerAIAuthenticator(ABC):

    @property
    def home_url(self):
        pass

    @abstractmethod
    def navigate_to_login(self):
        pass

    @property
    def is_logged_in(self):
        pass

    def __init__(self, driver=None, browser_adapter: BrowserAdapter | None = None):
        self.driver = driver
        self.browser_adapter = browser_adapter or (SeleniumBrowserAdapter(driver) if driver is not None else None)
        logger.debug(f"CoApplyerAIAuthenticator initialized with driver: {driver}")

    def _browser(self):
        return self.browser_adapter or self.driver

    def _current_url(self):
        """Return current URL via the browser adapter or raw Selenium driver."""
        browser = self._browser()
        if browser is None:
            return ""
        if self.browser_adapter is not None:
            url = browser.current_url()
            return str(url) if url is not None else ""
        # raw Selenium driver
        url = browser.current_url
        return str(url) if url is not None else ""

    def start(self):
        browser = self._browser()
        if browser is None:
            raise RuntimeError("Browser is not initialized for authentication")

        logger.info("Starting browser to log in to CoApplyer AI.")
        browser.get(self.home_url)
        if self.is_logged_in:
            logger.info("User is already logged in. Skipping login process.")
            return
        else:
            logger.info("User is not logged in. Proceeding with login.")
            self.handle_login()

    def handle_login(self):
        try:
            logger.info("Navigating to the CoApplyer AI login page...")
            self.navigate_to_login()
            self.prompt_for_credentials()
        except NoSuchElementException as e:
            logger.error(f"Could not log in to CoApplyer AI. Element not found: {e}")
        self.handle_security_checks()


    def prompt_for_credentials(self):
        try:
            logger.debug("Enter credentials...")
            check_interval = 4  # Interval to log the current URL
            elapsed_time = 0
            browser = self._browser()

            if browser is None:
                raise RuntimeError("Browser is not initialized for credential prompting")

            while True:
                # Log current URL every 4 seconds and remind the user to log in
                current_url = self._current_url()
                logger.info(f"Please login on {current_url}")

                # Check if the user is already on the feed page
                if self.is_logged_in:
                    logger.debug("Login successful, redirected to feed page.")
                    break
                else:
                    if self.browser_adapter is not None:
                        try:
                            browser.wait_until(lambda: len(browser.find_elements(By.ID, "password")) > 0, 10)
                            logger.debug("Password field detected, waiting for login completion.")
                        except TimeoutException:
                            logger.debug("Password field not detected yet; continuing to poll for login completion.")
                    else:
                        # Optionally wait for the password field (or any other element you expect on the login page)
                        WebDriverWait(self.driver, 10).until(
                            EC.presence_of_element_located((By.ID, "password"))
                        )
                        logger.debug("Password field detected, waiting for login completion.")

                time.sleep(check_interval)
                elapsed_time += check_interval

        except TimeoutException:
            logger.error("Login form not found. Aborting login.")

    @abstractmethod
    def handle_security_checks(self):
        pass
        
class LinkedInAuthenticator(CoApplyerAIAuthenticator):

    @property
    def home_url(self):
        return "https://www.linkedin.com"

    def navigate_to_login(self):
        browser = self._browser()
        if browser is None:
            raise RuntimeError("Browser is not initialized for LinkedIn login")
        return browser.get("https://www.linkedin.com/login")
    
    def handle_security_checks(self):
        try:
            logger.debug("Handling security check...")
            if self.browser_adapter is not None:
                browser = self._browser()
                browser.wait_until(lambda: 'https://www.linkedin.com/checkpoint/challengesV2/' in self._current_url(), 10)
                logger.warning("Security checkpoint detected. Please complete the challenge.")
                browser.wait_until(lambda: 'https://www.linkedin.com/feed/' in self._current_url(), 300)
            else:
                WebDriverWait(self.driver, 10).until(
                    EC.url_contains('https://www.linkedin.com/checkpoint/challengesV2/')
                )
                logger.warning("Security checkpoint detected. Please complete the challenge.")
                WebDriverWait(self.driver, 300).until(
                    EC.url_contains('https://www.linkedin.com/feed/')
                )
            logger.info("Security check completed")
        except TimeoutException:
            logger.error("Security check not completed. Please try again later.")
    
    @property
    def is_logged_in(self):
        keywords = ['feed', 'mynetwork','jobs','messaging','notifications']
        current_url = self._current_url()
        return any(item in current_url for item in keywords) and 'linkedin.com' in current_url

    def __init__(self, driver=None, browser_adapter: BrowserAdapter | None = None):
        super().__init__(driver, browser_adapter=browser_adapter)
        pass