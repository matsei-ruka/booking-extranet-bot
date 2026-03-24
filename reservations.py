"""
Reservations Module for Booking.com Extranet Bot

Handles downloading reservation data from the group-level reservations page.
Scrapes the table across all pages and builds an Excel file.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Optional, List, Dict
from playwright.async_api import Page

logger = logging.getLogger(__name__)

COLUMNS = [
    'Property ID', 'Property name', 'Location', 'Guest name',
    'Check-in', 'Check-out', 'Status', 'Total payment',
    'Commission and charges', 'Reservation number', 'Booked on',
]


class ReservationsManager:
    """Handles reservation data extraction from Booking.com extranet"""

    def __init__(self, page: Page):
        self.page = page
        self.downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
        os.makedirs(self.downloads_dir, exist_ok=True)

    def _get_session(self) -> str:
        match = re.search(r'ses=([a-f0-9]+)', self.page.url)
        return match.group(1) if match else ''

    async def _wait_for_table(self) -> int:
        """Wait for table data to load, return number of rows"""
        for _ in range(15):
            await asyncio.sleep(2)
            guest_links = await self.page.query_selector_all('table tbody tr a')
            if len(guest_links) > 0:
                rows = await self.page.query_selector_all('table tbody tr')
                return len(rows)
        return 0

    async def _scrape_current_page(self) -> List[List[str]]:
        """Scrape all rows from the currently displayed table page"""
        rows_data = []
        rows = await self.page.query_selector_all('table tbody tr')
        for row in rows:
            cells = await row.query_selector_all('td')
            if len(cells) < 10:
                continue
            cell_texts = []
            for cell in cells:
                text = (await cell.inner_text()).strip().replace('\n', ' ')
                cell_texts.append(text)
            rows_data.append(cell_texts)
        return rows_data

    async def _get_total_count(self) -> int:
        """Extract total reservation count from pagination text like '1-30 of 117 reservations'"""
        try:
            body_text = await self.page.inner_text('body')
            match = re.search(r'of\s+(\d+)\s+reservation', body_text)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return 0

    async def download_reservations(
        self,
        start_date: str,
        end_date: str,
        date_type: str = 'arrival',
        output_dir: Optional[str] = None,
    ) -> Optional[str]:
        """
        Scrape reservations and build an Excel file.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            date_type: Filter type - 'arrival', 'departure', or 'booking'
            output_dir: Directory to save the file (default: ./downloads/)

        Returns:
            Path to the generated Excel file, or None on failure
        """
        try:
            save_dir = output_dir or self.downloads_dir
            os.makedirs(save_dir, exist_ok=True)

            ses = self._get_session()
            if not ses:
                logger.error("No session token found")
                return None

            # Navigate with date params directly in URL
            type_map = {'arrival': 'ARRIVAL', 'departure': 'DEPARTURE', 'booking': 'BOOKING'}
            url_date_type = type_map.get(date_type, 'ARRIVAL')
            url = (
                f"https://admin.booking.com/hotel/hoteladmin/groups/reservations/index.html"
                f"?lang=xu&ses={ses}"
                f"&dateFrom={start_date}&dateTo={end_date}&dateType={url_date_type}"
            )
            logger.info(f"Navigating to reservations: {start_date} to {end_date} ({date_type})...")
            await self.page.goto(url, wait_until='networkidle')

            # Wait for data to load
            row_count = await self._wait_for_table()
            if row_count == 0:
                logger.warning("No reservations found for this date range")
                # Still create an empty file
                all_data = []
            else:
                total = await self._get_total_count()
                logger.info(f"Found {total} reservations ({row_count} on first page)")

                # Scrape first page
                all_data = await self._scrape_current_page()
                logger.info(f"Scraped page 1: {len(all_data)} rows")

                # Scrape remaining pages
                page_num = 1
                while len(all_data) < total:
                    page_num += 1
                    try:
                        next_btn = self.page.locator('button[aria-label="Next page"]')
                        if not await next_btn.is_visible():
                            break
                        await next_btn.click()
                        await self._wait_for_table()
                        page_data = await self._scrape_current_page()
                        if not page_data:
                            break
                        all_data.extend(page_data)
                        logger.info(f"Scraped page {page_num}: {len(page_data)} rows (total: {len(all_data)})")
                    except Exception as e:
                        logger.warning(f"Error scraping page {page_num}: {e}")
                        break

            # Build Excel file
            import pandas as pd
            df = pd.DataFrame(all_data, columns=COLUMNS[:len(all_data[0])] if all_data else COLUMNS)

            filename = f"Reservations_{start_date}_{end_date}.xlsx"
            file_path = os.path.join(save_dir, filename)
            df.to_excel(file_path, index=False, engine='openpyxl')

            size = os.path.getsize(file_path)
            logger.info(f"Excel file created: {file_path} ({len(all_data)} rows, {size} bytes)")
            return file_path

        except Exception as e:
            logger.error(f"Error downloading reservations: {e}")
            return None
