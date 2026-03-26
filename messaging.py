"""
Messaging Module for Booking.com Extranet Bot

Handles listing and reading guest messages from the property inbox.
Uses fallback selector chains for resilience against DOM changes.
"""

import asyncio
import logging
import re
from typing import Optional, List, Dict
from playwright.async_api import Page

logger = logging.getLogger(__name__)


# ── Selector fallback chains ─────────────────────────────────
# Each key maps to a list of selectors tried in order.
# When Booking.com changes their DOM, add the new selector at
# position 0 and keep old ones as fallbacks.

SELECTORS = {
    'conversation_item': [
        'button[data-test-id="inbox-conversation-item"]',
        'button[data-testid="inbox-conversation-item"]',
        'div.messages-list-item',
        'button[class*="conversation"]',
    ],
    'filter_dropdown': [
        'select[data-test-id="inbox-conversation-filter-select"]',
        'select[data-testid="inbox-conversation-filter-select"]',
    ],
    'guest_name': [
        '.list-item__title-text',
        '.messages-list-item__guest-name',
        '[class*="title-text"]',
        '[class*="guest-name"]',
    ],
    'textarea': [
        'textarea[data-test-id="messaging-main-input"]',
        'textarea[data-testid="messaging-main-input"]',
        'textarea.chat-form__textarea',
        'textarea',
    ],
    'send_button': [
        'button[data-test-id="send-message"]',
        'button[data-testid="send-message"]',
        'button:has-text("Send")',
        'button:has-text("Invia")',
    ],
}


async def _find_one(page, selector_key: str, timeout: int = 5000):
    """Try each selector in the fallback chain, return first match."""
    candidates = SELECTORS[selector_key]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            await el.wait_for(state='visible', timeout=timeout)
            logger.debug(f"Selector hit for '{selector_key}': {sel}")
            return el
        except Exception:
            continue
    logger.warning(f"No selector matched for '{selector_key}': tried {candidates}")
    return None


async def _find_all(page, selector_key: str) -> list:
    """Try each selector in the fallback chain, return all matches from first that works."""
    candidates = SELECTORS[selector_key]
    for sel in candidates:
        try:
            items = await page.query_selector_all(sel)
            visible = [it for it in items if await it.is_visible()]
            if visible:
                logger.debug(f"Selector hit for '{selector_key}': {sel} ({len(visible)} items)")
                return visible
        except Exception:
            continue
    logger.warning(f"No selector matched for '{selector_key}': tried {candidates}")
    return []


async def _find_all_filter(page) -> Optional:
    """Find the filter dropdown — try data-test-id first, then first visible <select>."""
    candidates = SELECTORS['filter_dropdown']
    for sel in candidates:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                logger.debug(f"Filter dropdown found: {sel}")
                return el
        except Exception:
            continue
    # Last resort: find any visible <select> in the page
    selects = await page.query_selector_all('select')
    for s in selects:
        if await s.is_visible():
            logger.debug("Filter dropdown found via generic <select> fallback")
            return s
    return None


class MessagingManager:
    """Handles guest messaging operations from Booking.com extranet"""

    def __init__(self, page: Page):
        self.page = page

    def _get_session(self) -> str:
        match = re.search(r'ses=([a-f0-9]+)', self.page.url)
        return match.group(1) if match else ''

    async def _navigate_to_inbox(self, hotel_id: str) -> bool:
        """Navigate to the reservation messages inbox for a property"""
        try:
            ses = self._get_session()
            url = (
                f"https://admin.booking.com/hotel/hoteladmin/extranet_ng/manage/"
                f"messaging/inbox.html?hotel_id={hotel_id}&ses={ses}&lang=en"
            )
            logger.info(f"Navigating to inbox for property {hotel_id}...")
            await self.page.goto(url, wait_until='domcontentloaded')
            await asyncio.sleep(5)  # Vue SPA needs time to render

            # Wait for conversation list to appear (any fallback)
            items = await _find_all(self.page, 'conversation_item')
            if not items:
                logger.warning("No conversation items found after navigation, waiting longer...")
                await asyncio.sleep(5)

            return True
        except Exception as e:
            logger.error(f"Failed to navigate to inbox: {e}")
            return False

    async def list_messages(
        self,
        hotel_id: str,
        filter_type: str = 'unanswered',
    ) -> Dict:
        """
        List messages from the inbox.

        Args:
            hotel_id: Property hotel ID
            filter_type: 'unanswered' (default), 'sent', or 'all'

        Returns:
            Dict with hotel_id, filter, message_count, messages
        """
        try:
            if not await self._navigate_to_inbox(hotel_id):
                return {'hotel_id': hotel_id, 'filter': filter_type, 'message_count': 0, 'messages': []}

            # Set the filter
            filter_map = {
                'unanswered': 'PENDING_PROPERTY',
                'sent': 'PENDING_GUEST',
                'all': 'ALL',
            }
            # Also try lowercase variants (Booking.com sometimes changes case)
            filter_map_alt = {
                'unanswered': 'pending_property',
                'sent': 'pending_guest',
                'all': '',
            }
            filter_value = filter_map.get(filter_type, 'PENDING_PROPERTY')
            filter_value_alt = filter_map_alt.get(filter_type, 'pending_property')

            filter_el = await _find_all_filter(self.page)
            if filter_el:
                try:
                    await self.page.evaluate(
                        """(args) => {
                            const [el, val, valAlt] = args;
                            // Try exact match first, then alt
                            for (const opt of el.options) {
                                if (opt.value === val || opt.value === valAlt) {
                                    el.value = opt.value;
                                    el.dispatchEvent(new Event('change', {bubbles: true}));
                                    return true;
                                }
                            }
                            return false;
                        }""",
                        [filter_el, filter_value, filter_value_alt],
                    )
                    logger.info(f"Filter set to: {filter_type}")
                    await asyncio.sleep(3)
                except Exception:
                    logger.warning("Could not set filter via JS, trying select_option")
                    try:
                        await filter_el.select_option(filter_value, timeout=3000)
                        await asyncio.sleep(3)
                    except Exception:
                        try:
                            await filter_el.select_option(filter_value_alt, timeout=3000)
                            await asyncio.sleep(3)
                        except Exception:
                            logger.warning("Could not set filter, using default")
            else:
                logger.warning("Filter dropdown not found, using default view")

            # Scrape conversation items
            messages = []
            msg_items = await _find_all(self.page, 'conversation_item')

            for i, item in enumerate(msg_items):
                try:
                    # Guest name — try fallback selectors
                    guest_name = 'Unknown'
                    for name_sel in SELECTORS['guest_name']:
                        name_el = await item.query_selector(name_sel)
                        if name_el:
                            guest_name = (await name_el.inner_text()).strip()
                            break

                    # Date — look for spans with date-like content
                    date = ''
                    spans = await item.query_selector_all('span')
                    months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
                    for span in spans:
                        text = (await span.inner_text()).strip()
                        if text and any(m in text for m in months):
                            date = text
                            break

                    # Preview — full text minus name and date
                    full_text = (await item.inner_text()).strip()
                    preview = full_text.replace(guest_name, '').replace(date, '').strip()
                    preview = ' '.join(preview.split())

                    messages.append({
                        'index': len(messages),
                        'guest_name': guest_name,
                        'date': date,
                        'preview': preview[:200],
                    })

                except Exception as e:
                    logger.debug(f"Error parsing message {i}: {e}")
                    continue

            logger.info(f"Found {len(messages)} messages")

            return {
                'hotel_id': hotel_id,
                'filter': filter_type,
                'message_count': len(messages),
                'messages': messages,
            }

        except Exception as e:
            logger.error(f"Error listing messages: {e}")
            return {'hotel_id': hotel_id, 'filter': filter_type, 'message_count': 0, 'messages': []}

    async def _get_conversation_items(self) -> list:
        """Get visible conversation items from the inbox list"""
        return await _find_all(self.page, 'conversation_item')

    async def read_conversation(
        self,
        hotel_id: str,
        message_index: int = 0,
    ) -> Optional[Dict]:
        """
        Open and read a specific conversation by clicking on it.

        Args:
            hotel_id: Property hotel ID
            message_index: Index of the message in the list (0-based)

        Returns:
            Dict with guest_name, reservation_info, and conversation messages
        """
        try:
            visible_buttons = await self._get_conversation_items()

            if message_index >= len(visible_buttons):
                logger.error(f"Message index {message_index} out of range ({len(visible_buttons)} messages)")
                return None

            await visible_buttons[message_index].click()
            await asyncio.sleep(3)

            conversation = {
                'index': message_index,
                'messages': [],
            }

            # Get the conversation text from the middle panel
            msg_list = await self.page.query_selector('.message-list')
            if msg_list:
                text = await msg_list.inner_text()
                conversation['full_text'] = text[:5000]

            # Get reservation details from the right panel
            detail = await self.page.evaluate("""() => {
                const body = document.body.innerText;
                const result = {};
                const labels = ['Guest name:', 'Booking reference number:', 'Arrival:',
                               'Departure:', 'Total price:', 'Preferred language:',
                               'Total guests:'];
                for (const label of labels) {
                    const idx = body.indexOf(label);
                    if (idx !== -1) {
                        const after = body.substring(idx + label.length, idx + label.length + 100);
                        const val = after.split('\\n')[0].trim() || after.split('\\n')[1]?.trim();
                        if (val) result[label.replace(':', '')] = val;
                    }
                }
                return result;
            }""")
            conversation['reservation'] = detail

            return conversation

        except Exception as e:
            logger.error(f"Error reading conversation: {e}")
            return None

    async def send_reply(
        self,
        hotel_id: str,
        message_index: int,
        reply_text: str,
    ) -> Dict:
        """
        Send a reply to a conversation.

        Args:
            hotel_id: Property hotel ID
            message_index: Index of the message in the inbox list (0-based)
            reply_text: The reply message to send

        Returns:
            Dict with status and details
        """
        try:
            # Make sure we're on the inbox page
            if 'messaging' not in self.page.url or f'hotel_id={hotel_id}' not in self.page.url:
                if not await self._navigate_to_inbox(hotel_id):
                    return {'sent': False, 'error': 'Failed to navigate to inbox'}

            # Click the conversation
            visible_buttons = await self._get_conversation_items()
            if message_index >= len(visible_buttons):
                return {'sent': False, 'error': f'Message index {message_index} out of range ({len(visible_buttons)} messages)'}

            # Get guest name before clicking
            guest_name = 'Unknown'
            for name_sel in SELECTORS['guest_name']:
                name_el = await visible_buttons[message_index].query_selector(name_sel)
                if name_el:
                    guest_name = (await name_el.inner_text()).strip()
                    break

            await visible_buttons[message_index].click()
            logger.info(f"Opened conversation with {guest_name}")
            await asyncio.sleep(3)

            # Check if the thread is closed
            body_text = await self.page.inner_text('body')
            if 'thread is closed' in body_text.lower():
                return {'sent': False, 'error': 'Message thread is closed, cannot reply'}

            # Find the reply textarea (fallback chain)
            textarea = await _find_one(self.page, 'textarea', timeout=5000)
            if not textarea:
                return {'sent': False, 'error': 'Reply textarea not found'}

            # Type the reply
            await textarea.click()
            await asyncio.sleep(1)
            await textarea.fill(reply_text)
            logger.info(f"Typed reply: {reply_text[:50]}...")
            await asyncio.sleep(1)

            # Click Send (fallback chain)
            send_btn = await _find_one(self.page, 'send_button', timeout=5000)
            if not send_btn:
                return {'sent': False, 'error': 'Send button not found'}

            await send_btn.click()
            logger.info("Clicked Send button")
            await asyncio.sleep(3)

            return {
                'sent': True,
                'guest_name': guest_name,
                'reply': reply_text,
                'hotel_id': hotel_id,
            }

        except Exception as e:
            logger.error(f"Error sending reply: {e}")
            return {'sent': False, 'error': str(e)}

    async def list_properties(self) -> List[Dict]:
        """
        Get all properties from the group homepage with unread message counts.

        Returns:
            List of dicts with hotel_id, name, unread_messages
        """
        try:
            ses = self._get_session()
            url = f"https://admin.booking.com/hotel/hoteladmin/groups/home/index.html?ses={ses}&lang=en"
            await self.page.goto(url, wait_until='networkidle')
            await asyncio.sleep(3)

            data = await self.page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a'));
                const properties = {};
                const msgCounts = {};

                for (const a of links) {
                    const match = a.href.match(/hotel_id=(\\d+)/);
                    if (!match) continue;
                    const hid = match[1];

                    const text = a.innerText.trim();
                    if (text.length > 5 && !/^\\d+$/.test(text) && !properties[hid]) {
                        properties[hid] = text;
                    }

                    if (a.href.includes('messaging') && /^\\d+$/.test(text)) {
                        msgCounts[hid] = parseInt(text);
                    }
                }

                return {properties, msgCounts};
            }""")

            result = []
            for hid, name in data['properties'].items():
                result.append({
                    'hotel_id': hid,
                    'name': name,
                    'unread_messages': data['msgCounts'].get(hid, 0),
                })

            logger.info(f"Found {len(result)} properties")
            return result

        except Exception as e:
            logger.error(f"Error listing properties: {e}")
            return []
