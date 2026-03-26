import asyncio
import os
import subprocess
import time
import logging
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
import pyotp
from dotenv import load_dotenv
from rate_manager import RateManager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('booking_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BookingExtranetBot:
    """
    Automated bot for Booking.com admin extranet with 2FA support using Pulse app
    """

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.rate_manager: Optional[RateManager] = None

        # Load credentials from environment variables
        self.username = os.getenv('BOOKING_USERNAME')
        self.password = os.getenv('BOOKING_PASSWORD')

        # TOTP secret is optional - we'll use manual 2FA input instead
        self.totp_secret = os.getenv('PULSE_TOTP_SECRET')  # Optional for manual 2FA

        if not all([self.username, self.password]):
            raise ValueError("Missing required environment variables (BOOKING_USERNAME, BOOKING_PASSWORD). Please check .env file.")

    async def initialize_browser(self, headless: bool = False) -> None:
        """Initialize browser using real Chrome to avoid bot detection.
        Reuses an existing Chrome instance and tab if available."""
        try:
            self.playwright = await async_playwright().start()

            # Check if Chrome is already running with remote debugging
            chrome_already_running = False
            try:
                import urllib.request
                urllib.request.urlopen('http://localhost:9222/json/version', timeout=2)
                chrome_already_running = True
            except Exception:
                pass

            if not chrome_already_running:
                # Launch Chrome with remote debugging
                import platform
                system = platform.system()
                if system == 'Darwin':
                    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                elif system == 'Linux':
                    for path in ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium-browser', '/usr/bin/chromium']:
                        if os.path.exists(path):
                            chrome_path = path
                            break
                    else:
                        raise FileNotFoundError("Chrome/Chromium not found. Install with: sudo apt install google-chrome-stable")
                else:
                    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

                chrome_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.chrome-data')

                self.chrome_process = subprocess.Popen(
                    [
                        chrome_path,
                        '--remote-debugging-port=9222',
                        f'--user-data-dir={chrome_data_dir}',
                        '--no-first-run',
                        '--no-default-browser-check',
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                await asyncio.sleep(3)  # Wait for Chrome to start

            # Connect Playwright to the real Chrome instance
            self.browser = await self.playwright.chromium.connect_over_cdp(
                'http://localhost:9222', timeout=30000,
            )
            self.context = self.browser.contexts[0]

            # Reuse the first existing tab, or create one if none exist
            pages = self.context.pages
            if pages:
                self.page = pages[0]
                # Close any extra tabs from previous runs
                for extra_page in pages[1:]:
                    try:
                        await extra_page.close()
                    except Exception:
                        pass
            else:
                self.page = await self.context.new_page()

            # Initialize rate manager with the page
            self.rate_manager = RateManager(self.page)

            logger.info("Browser initialized successfully (real Chrome)")

        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            raise

    async def login(self) -> bool:
        """Login to Booking.com extranet with 2FA support"""
        try:
            if not self.page:
                raise Exception("Browser not initialized")

            if not self.username or not self.password:
                raise Exception("Username or password not configured")

            logger.info("Starting login process...")

            # Navigate to Booking.com admin login page
            await self.page.goto('https://admin.booking.com/hotel/hoteladmin/', wait_until='networkidle')

            # Check if already logged in (session cookie still valid)
            if 'admin.booking.com' in self.page.url and 'sign-in' not in self.page.url and 'account.booking.com' not in self.page.url:
                logger.info("Already logged in (session still valid)!")
                return True

            # Wait for and fill username
            await self.page.wait_for_selector('input[name="loginname"]', timeout=10000)
            await self.page.fill('input[name="loginname"]', self.username)
            logger.info("Username entered")

            # Click Next/Submit button
            await self.page.click('button[type="submit"]', timeout=5000)
            await asyncio.sleep(3)

            # Wait for password field to appear
            await self.page.wait_for_selector('input[name="password"]', state='visible', timeout=15000)
            await self.page.fill('input[name="password"]', self.password)
            logger.info("Password entered")

            # Click login button
            await self.page.click('button[type="submit"]', timeout=5000)
            await asyncio.sleep(5)

            # Check if we landed on the dashboard (no 2FA needed)
            current_url = self.page.url
            if 'admin.booking.com' in current_url and 'sign-in' not in current_url and 'account.booking.com' not in current_url:
                logger.info("Login successful (no 2FA required)!")
                return True

            # 2FA verification method selection page
            logger.info("2FA verification required, checking options...")

            # Try clicking SMS option
            try:
                sms_link = self.page.locator('a:has-text("Text message (SMS)")')
                await sms_link.click(timeout=10000)
                logger.info("Selected SMS verification")
                await asyncio.sleep(5)
            except Exception:
                logger.info("SMS link not found, checking for other verification methods...")

            # Look for phone number selection (if multiple phones)
            try:
                phone_button = self.page.locator('button:has-text("Send")').first
                await phone_button.click(timeout=5000)
                logger.info("Clicked Send SMS button")
                await asyncio.sleep(5)
            except Exception:
                pass

            # Wait for code input - try multiple possible selectors
            code_input = None
            for selector in [
                'input[name="sms_code"]',
                'input[name="code"]',
                'input[name="verification_code"]',
                'input[type="tel"]',
                'input[type="number"]',
                'input[inputmode="numeric"]',
                'input[autocomplete="one-time-code"]',
            ]:
                try:
                    await self.page.wait_for_selector(selector, state='visible', timeout=3000)
                    code_input = selector
                    logger.info(f"Found 2FA code input: {selector}")
                    break
                except Exception:
                    continue

            if not code_input:
                # Last resort: find any visible text input on the page
                inputs = await self.page.query_selector_all('input')
                for inp in inputs:
                    visible = await inp.is_visible()
                    inp_type = await inp.get_attribute('type')
                    if visible and inp_type in ('text', 'tel', 'number', None):
                        code_input = inp
                        logger.info("Found 2FA input via fallback scan")
                        break

            if not code_input:
                logger.error("Could not find 2FA code input field")
                await self.page.screenshot(path='debug_2fa_not_found.png', full_page=True)
                return False

            # Ask for code from terminal
            two_fa_code = input("\nEnter the verification code (SMS/Pulse): ").strip()

            # Enter the code
            if isinstance(code_input, str):
                await self.page.fill(code_input, two_fa_code)
            else:
                await code_input.fill(two_fa_code)
            logger.info("2FA code entered")

            # Submit
            try:
                await self.page.click('button[type="submit"]', timeout=5000)
                logger.info("2FA code submitted")
            except Exception:
                await self.page.keyboard.press('Enter')
                logger.info("2FA code submitted via Enter key")

            await asyncio.sleep(5)

            # Check for successful login
            current_url = self.page.url
            if 'admin.booking.com' in current_url and 'sign-in' not in current_url and 'account.booking.com' not in current_url:
                logger.info("Login successful!")
                return True

            # One more wait in case of redirect
            try:
                await self.page.wait_for_url('**/hoteladmin/**', timeout=15000)
                logger.info("Login successful!")
                return True
            except Exception:
                logger.error(f"Login failed - current URL: {self.page.url}")
                return False

        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False

    async def close(self) -> None:
        """Disconnect from Chrome without closing the browser or tab.
        Chrome stays running so the session persists for next invocation."""
        try:
            # Disconnect Playwright from CDP — does NOT close Chrome or the tab
            if self.browser:
                await self.browser.close()
            # Do NOT terminate the Chrome process — we want to reuse it
            self.page = None
            self.browser = None
            self.context = None
            self.rate_manager = None
            logger.info("Browser closed successfully")
        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    async def navigate_to_calendar(self, hotel_id: str = None) -> bool:
        """Navigate to the rates & availability calendar for a specific property"""
        if not self.rate_manager:
            logger.error("Rate manager not initialized")
            return False
        return await self.rate_manager.navigate_to_calendar(hotel_id=hotel_id)

    async def get_calendar_info(self) -> dict:
        """Get information about the current calendar page for debugging"""
        if not self.rate_manager:
            return {}
        return await self.rate_manager.get_current_page_info()

# ─── Main Entry Point ────────────────────────────────────────

# Default hotel ID for Sultan Sunscape property
DEFAULT_HOTEL_ID = '13616005'

async def main():
    """Run the booking extranet bot to update rates from CSV"""
    bot = BookingExtranetBot()

    try:
        await bot.initialize_browser(headless=False)

        if await bot.login():
            logger.info("Login successful, ready for automation tasks!")

            # Navigate to the property calendar
            hotel_id = os.getenv('BOOKING_HOTEL_ID', DEFAULT_HOTEL_ID)
            if await bot.navigate_to_calendar(hotel_id=hotel_id):
                logger.info("Navigated to calendar successfully")

                if bot.rate_manager:
                    success = await bot.rate_manager.process_all_rooms()
                    if success:
                        logger.info("All records processed successfully!")
                    else:
                        logger.error("Some records failed to process")
                else:
                    logger.error("Rate manager not available")
            else:
                logger.error("Failed to navigate to calendar")
        else:
            logger.error("Login failed!")

    except Exception as e:
        logger.error(f"Error in main execution: {e}")

    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())
