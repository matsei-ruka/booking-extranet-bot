"""
Reservations Module for Booking.com Extranet Bot

Handles downloading reservation files from the group-level reservations page.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Optional
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Month names as they appear in the calendar header
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December']


class ReservationsManager:
    """Handles reservation downloads from Booking.com extranet"""

    def __init__(self, page: Page):
        self.page = page
        self.downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
        os.makedirs(self.downloads_dir, exist_ok=True)

    def _get_session(self) -> str:
        """Extract session token from current page URL"""
        match = re.search(r'ses=([a-f0-9]+)', self.page.url)
        return match.group(1) if match else ''

    # ─── Calendar Widget Interaction ──────────────────────────

    async def _get_calendar_month_year(self) -> tuple[str, int]:
        """Get the currently displayed month and year from the calendar header"""
        cal = self.page.locator('#peg-reservations-ranged__calendar')
        # The month/year header is usually in a heading or strong element
        header_text = await cal.inner_text()
        # Look for a line like "March 2026"
        for line in header_text.split('\n'):
            line = line.strip()
            for month in MONTHS:
                if month in line:
                    try:
                        year = int(line.replace(month, '').strip())
                        return month, year
                    except ValueError:
                        continue
        return '', 0

    async def _navigate_calendar_to(self, target_month: int, target_year: int) -> bool:
        """Navigate the calendar to a specific month/year"""
        cal_selector = '#peg-reservations-ranged__calendar'
        next_btn = f'{cal_selector} button[aria-label="Next month"]'
        prev_btn = f'{cal_selector} button[aria-label="Previous month"]'

        for _ in range(24):  # Max 24 months of navigation
            current_month_name, current_year = await self._get_calendar_month_year()
            if not current_month_name:
                logger.error("Could not read calendar month/year")
                return False

            current_month = MONTHS.index(current_month_name) + 1

            if current_month == target_month and current_year == target_year:
                return True

            # Determine direction
            current_total = current_year * 12 + current_month
            target_total = target_year * 12 + target_month

            if target_total > current_total:
                await self.page.click(next_btn)
            else:
                await self.page.click(prev_btn)
            await asyncio.sleep(0.5)

        logger.error(f"Could not navigate to {target_month}/{target_year}")
        return False

    async def _click_calendar_day(self, day: int) -> bool:
        """Click a specific day number in the currently displayed calendar month"""
        cal = self.page.locator('#peg-reservations-ranged__calendar')
        # Find all td cells and click the one with the matching day
        cells = cal.locator('td')
        count = await cells.count()

        for i in range(count):
            cell = cells.nth(i)
            text = (await cell.inner_text()).strip()
            if text == str(day):
                await cell.click()
                await asyncio.sleep(0.3)
                return True

        logger.error(f"Could not find day {day} in calendar")
        return False

    async def _set_date_range(self, start_date: str, end_date: str) -> bool:
        """
        Set the date range using the calendar widget.
        Dates in YYYY-MM-DD format.
        """
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d')

            logger.info(f"Setting date range via calendar: {start_date} to {end_date}")

            # Open the calendar by clicking the date input
            await self.page.click('#peg-reservations-ranged')
            await asyncio.sleep(1)

            # Navigate to start month and click start day
            if not await self._navigate_calendar_to(start.month, start.year):
                return False
            if not await self._click_calendar_day(start.day):
                return False
            logger.info(f"Selected start date: {start_date}")

            await asyncio.sleep(0.5)

            # Navigate to end month and click end day
            if not await self._navigate_calendar_to(end.month, end.year):
                return False
            if not await self._click_calendar_day(end.day):
                return False
            logger.info(f"Selected end date: {end_date}")

            # Click somewhere outside to close the calendar
            await self.page.keyboard.press('Escape')
            await asyncio.sleep(0.5)

            # Verify the value changed
            val = await self.page.locator('#peg-reservations-ranged').get_attribute('value')
            logger.info(f"Date input now shows: '{val}'")

            # Click "Show reservations" to apply the filter
            await self.page.locator('button:has-text("Show reservations")').click()
            await self.page.wait_for_load_state('networkidle', timeout=15000)
            await asyncio.sleep(2)
            logger.info("Applied date filter")

            return True

        except Exception as e:
            logger.error(f"Error setting date range: {e}")
            return False

    # ─── Download ─────────────────────────────────────────────

    async def download_reservations(
        self,
        start_date: str,
        end_date: str,
        date_type: str = 'arrival',
        output_dir: Optional[str] = None,
    ) -> Optional[str]:
        """
        Download reservations as Excel file from the group-level page.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            date_type: Filter type - 'arrival', 'departure', or 'booking'
            output_dir: Directory to save the file (default: ./downloads/)

        Returns:
            Path to the downloaded file, or None on failure
        """
        try:
            save_dir = output_dir or self.downloads_dir
            os.makedirs(save_dir, exist_ok=True)

            ses = self._get_session()
            if not ses:
                logger.error("No session token found")
                return None

            # Navigate to group reservations page
            url = (
                f"https://admin.booking.com/hotel/hoteladmin/groups/reservations/index.html"
                f"?lang=xu&ses={ses}"
            )
            logger.info("Navigating to reservations page...")
            await self.page.goto(url, wait_until='networkidle')
            await asyncio.sleep(3)

            # Set the date type filter if needed
            type_select = self.page.locator('select#type')
            type_map = {'arrival': 'ARRIVAL', 'departure': 'DEPARTURE', 'booking': 'BOOKING'}
            target_type = type_map.get(date_type, 'ARRIVAL')
            try:
                await type_select.select_option(value=target_type, timeout=3000)
                logger.info(f"Set date type to: {date_type}")
            except Exception:
                logger.warning(f"Could not set date type to {date_type}")

            # Set the date range using the calendar widget
            if not await self._set_date_range(start_date, end_date):
                logger.error("Failed to set date range")
                return None

            # Set up download handler
            download_path = None
            download_event = asyncio.Event()

            async def handle_download(download):
                nonlocal download_path
                suggested = download.suggested_filename
                ext = os.path.splitext(suggested)[1] or '.xls'
                filename = f"Reservations_{start_date}_{end_date}{ext}"
                path = os.path.join(save_dir, filename)
                await download.save_as(path)
                download_path = path
                logger.info(f"File downloaded: {path}")
                download_event.set()

            self.page.on('download', handle_download)

            # Click download button
            logger.info("Clicking Download button...")
            dl_btn = self.page.locator('button:has-text("Download")').first
            await dl_btn.click(timeout=10000)

            # Wait for download to complete
            try:
                await asyncio.wait_for(download_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                logger.error("Download timed out after 60 seconds")
                return None
            finally:
                self.page.remove_listener('download', handle_download)

            if download_path and os.path.exists(download_path):
                size = os.path.getsize(download_path)
                logger.info(f"Download complete: {download_path} ({size} bytes)")
                return download_path
            else:
                logger.error("Download file not found")
                return None

        except Exception as e:
            logger.error(f"Error downloading reservations: {e}")
            return None
