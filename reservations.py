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

    async def _set_date_range(self, start_date: str, end_date: str) -> bool:
        """
        Set the date range filter on the reservations page.
        Dates should be in YYYY-MM-DD format.
        """
        try:
            # The group reservations page uses custom datepicker inputs
            # Find and interact with the date range filter
            # The date display is typically a clickable element that opens a picker

            # Try to find the date inputs - they may be inside a form
            date_inputs = await self.page.query_selector_all('input[type="text"]')
            date_fields = []
            for inp in date_inputs:
                cls = (await inp.get_attribute('class') or '').lower()
                placeholder = (await inp.get_attribute('placeholder') or '').lower()
                name = (await inp.get_attribute('name') or '').lower()
                if any(kw in f"{cls} {placeholder} {name}" for kw in ['date', 'from', 'to', 'check']):
                    date_fields.append(inp)

            if len(date_fields) >= 2:
                # Clear and fill start date
                await date_fields[0].click()
                await self.page.keyboard.press('Meta+a')
                await self.page.keyboard.press('Backspace')
                await date_fields[0].type(start_date)
                await self.page.keyboard.press('Tab')
                await asyncio.sleep(0.5)

                # Clear and fill end date
                await date_fields[1].click()
                await self.page.keyboard.press('Meta+a')
                await self.page.keyboard.press('Backspace')
                await date_fields[1].type(end_date)
                await self.page.keyboard.press('Tab')
                await asyncio.sleep(0.5)

                logger.info(f"Date range set: {start_date} to {end_date}")
            else:
                # Fallback: use JavaScript to set the form values
                logger.info("Using JS to set date range")
                await self.page.evaluate(f'''() => {{
                    const inputs = document.querySelectorAll('input[type="text"]');
                    for (const inp of inputs) {{
                        const name = (inp.name || '').toLowerCase();
                        if (name.includes('from') || name.includes('start')) {{
                            inp.value = '{start_date}';
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                        if (name.includes('to') || name.includes('end')) {{
                            inp.value = '{end_date}';
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }}
                }}''')

            # Click "Show reservations" to apply the filter
            try:
                show_btn = self.page.locator('button:has-text("Show reservations")')
                await show_btn.click(timeout=5000)
                await self.page.wait_for_load_state('networkidle', timeout=15000)
                await asyncio.sleep(2)
                logger.info("Applied date filter")
            except Exception:
                logger.warning("Could not find 'Show reservations' button")

            return True

        except Exception as e:
            logger.error(f"Error setting date range: {e}")
            return False

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

            # Navigate to group reservations page with date params
            url = (
                f"https://admin.booking.com/hotel/hoteladmin/groups/reservations/index.html"
                f"?lang=xu&ses={ses}"
                f"&date_from={start_date}&date_to={end_date}"
                f"&date_type={date_type}"
            )
            logger.info(f"Navigating to reservations page...")
            await self.page.goto(url, wait_until='networkidle')
            await asyncio.sleep(3)

            # Try setting dates via the UI to ensure the filter is applied
            await self._set_date_range(start_date, end_date)

            # Set up download handler
            download_path = None
            download_event = asyncio.Event()

            async def handle_download(download):
                nonlocal download_path
                suggested = download.suggested_filename
                # Rename with our date range for clarity
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

            # Wait for download to complete (up to 60 seconds)
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
