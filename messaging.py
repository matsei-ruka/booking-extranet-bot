"""
Messaging Module for Booking.com Extranet Bot

Handles listing and reading guest messages from the property inbox.
"""

import asyncio
import logging
import re
from typing import Optional, List, Dict
from playwright.async_api import Page

logger = logging.getLogger(__name__)


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

            # Wait for conversation list items to appear
            try:
                await self.page.wait_for_selector(
                    'div.messages-list-item',
                    timeout=15000,
                )
            except Exception:
                logger.warning("Message list items not found, page may still be loading")
                await asyncio.sleep(5)

            return True
        except Exception as e:
            logger.error(f"Failed to navigate to inbox: {e}")
            return False

    async def list_messages(
        self,
        hotel_id: str,
        filter_type: str = 'unanswered',
    ) -> List[Dict]:
        """
        List messages from the inbox.

        Args:
            hotel_id: Property hotel ID
            filter_type: 'unanswered' (default), 'sent', or 'all'

        Returns:
            List of message dicts with guest_name, date, preview, status
        """
        try:
            if not await self._navigate_to_inbox(hotel_id):
                return []

            # Set the filter using the visible <select> (no data-test-id)
            filter_map = {
                'unanswered': 'pending_property',
                'sent': 'pending_guest',
                'all': '',
            }
            filter_value = filter_map.get(filter_type, 'pending_property')

            try:
                # Find the visible select element
                selects = await self.page.query_selector_all('select')
                for sel in selects:
                    if await sel.is_visible():
                        await sel.select_option(filter_value)
                        logger.info(f"Filter set to: {filter_type} ({filter_value})")
                        await asyncio.sleep(3)
                        break
            except Exception:
                logger.warning("Could not set filter, using default")

            # Scrape message list items using stable BEM class selectors
            messages = []
            msg_items = await self.page.query_selector_all('div.messages-list-item')

            for i, item in enumerate(msg_items):
                try:
                    visible = await item.is_visible()
                    if not visible:
                        continue

                    # Guest name
                    name_el = await item.query_selector('.messages-list-item__guest-name')
                    guest_name = (await name_el.inner_text()).strip() if name_el else 'Unknown'

                    # Date/timestamp
                    date_el = await item.query_selector('.messages-list-item__timestamp')
                    date = (await date_el.inner_text()).strip() if date_el else ''

                    # Preview text
                    preview_el = await item.query_selector('.messages-list-item__content')
                    preview = (await preview_el.inner_text()).strip() if preview_el else ''

                    # Unread indicator
                    unread_el = await item.query_selector('.messages-list-item__unread-indicator')
                    has_unread = unread_el is not None

                    messages.append({
                        'index': len(messages),
                        'guest_name': guest_name,
                        'date': date,
                        'preview': preview[:200],
                        'unread': has_unread,
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
            return {'hotel_id': hotel_id, 'filter': filter_type, 'unanswered_count': 0, 'messages': []}

    async def _get_conversation_items(self) -> list:
        """Get visible conversation items from the inbox list"""
        items = await self.page.query_selector_all('div.messages-list-item')
        visible = []
        for item in items:
            if await item.is_visible():
                visible.append(item)
        return visible

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

            # Read the conversation from the middle panel
            conversation = {
                'index': message_index,
                'messages': [],
            }

            # Get the conversation container
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
            name_el = await visible_buttons[message_index].query_selector('.messages-list-item__guest-name')
            guest_name = (await name_el.inner_text()).strip() if name_el else 'Unknown'

            await visible_buttons[message_index].click()
            logger.info(f"Opened conversation with {guest_name}")
            await asyncio.sleep(3)

            # Find the reply textarea
            textarea = await self.page.query_selector('textarea[data-test-id="messaging-main-input"]')
            if not textarea:
                # Fallback to class-based selector
                textarea = await self.page.query_selector('textarea.chat-form__textarea')
            if not textarea:
                return {'sent': False, 'error': 'Reply textarea not found'}

            # Check if the thread is closed
            body_text = await self.page.inner_text('body')
            if 'thread is closed' in body_text.lower():
                return {'sent': False, 'error': 'Message thread is closed, cannot reply'}

            # Type the reply
            await textarea.click()
            await asyncio.sleep(1)
            await textarea.fill(reply_text)
            logger.info(f"Typed reply: {reply_text[:50]}...")
            await asyncio.sleep(1)

            # Click Send
            send_btn = self.page.locator('button[data-test-id="send-message"]')
            await send_btn.click(timeout=5000)
            logger.info("Clicked Send button")
            await asyncio.sleep(3)

            # Verify by checking if the message appeared or filter changed
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
            # Navigate to group homepage
            ses = self._get_session()
            url = f"https://admin.booking.com/hotel/hoteladmin/groups/home/index.html?ses={ses}&lang=en"
            await self.page.goto(url, wait_until='networkidle')
            await asyncio.sleep(3)

            # Extract properties and their messaging badge counts
            data = await self.page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a'));
                const properties = {};
                const msgCounts = {};

                for (const a of links) {
                    const match = a.href.match(/hotel_id=(\\d+)/);
                    if (!match) continue;
                    const hid = match[1];

                    // Property name links (longer text, not just numbers)
                    const text = a.innerText.trim();
                    if (text.length > 5 && !/^\\d+$/.test(text) && !properties[hid]) {
                        properties[hid] = text;
                    }

                    // Messaging links have the unread count as their text
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
