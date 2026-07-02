#!/usr/bin/env python3
"""
Booking.com Extranet CLI Tool

A command-line interface for Booking.com extranet automation.
Designed for use by AI agents — outputs structured JSON to stdout.

Usage:
    python cli.py download-reservations --start 2026-03-01 --end 2026-03-31
    python cli.py download-reservations --start 2026-03-01 --end 2026-03-31 --json
    python cli.py list-messages --hotel-id 13616005
    python cli.py update-rates
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# Send logs to stderr so stdout stays clean for JSON output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('booking_bot.log'),
        logging.StreamHandler(sys.stderr),
    ]
)
logger = logging.getLogger('cli')

DEFAULT_HOTEL_ID = os.getenv('BOOKING_HOTEL_ID', '13616005')


def output_json(data: dict):
    """Print JSON result to stdout for AI agent consumption"""
    print(json.dumps(data, indent=2, default=str))


async def _init_bot():
    """Initialize and login the bot. Returns (bot, success)."""
    from booking_extranet_bot import BookingExtranetBot
    bot = BookingExtranetBot()
    await bot.initialize_browser(headless=False)
    success = await bot.login()
    return bot, success


# ─── download-reservations ────────────────────────────────────

async def cmd_download_reservations(args):
    from reservations import ReservationsManager

    bot, logged_in = await _init_bot()
    try:
        if not logged_in:
            output_json({'status': 'error', 'action': 'download-reservations', 'error': 'Login failed'})
            return

        reservations = ReservationsManager(bot.page)

        if args.json:
            # Return data as JSON instead of Excel
            data = await reservations.get_reservations_data(
                start_date=args.start,
                end_date=args.end,
                date_type=args.date_type,
            )
            output_json({
                'status': 'success',
                'action': 'download-reservations',
                'params': {'start': args.start, 'end': args.end, 'date_type': args.date_type},
                'count': len(data),
                'reservations': data,
            })
        else:
            file_path = await reservations.download_reservations(
                start_date=args.start,
                end_date=args.end,
                date_type=args.date_type,
                output_dir=args.output_dir,
            )
            if file_path:
                output_json({
                    'status': 'success',
                    'action': 'download-reservations',
                    'file': file_path,
                    'params': {'start': args.start, 'end': args.end, 'date_type': args.date_type},
                })
            else:
                output_json({'status': 'error', 'action': 'download-reservations', 'error': 'Failed'})

    except Exception as e:
        output_json({'status': 'error', 'action': 'download-reservations', 'error': str(e)})
    finally:
        await bot.close()


# ─── update-rates ─────────────────────────────────────────────

async def cmd_update_rates(args):
    bot, logged_in = await _init_bot()
    try:
        if not logged_in:
            output_json({'status': 'error', 'action': 'update-rates', 'error': 'Login failed'})
            return

        hotel_id = args.hotel_id or DEFAULT_HOTEL_ID

        if not await bot.navigate_to_calendar(hotel_id=hotel_id):
            output_json({'status': 'error', 'action': 'update-rates', 'error': 'Failed to navigate to calendar'})
            return

        if bot.rate_manager:
            success = await bot.rate_manager.process_all_rooms()
            progress = bot.rate_manager.get_progress_summary()

            result = {
                'status': 'success' if success else 'partial',
                'action': 'update-rates',
                'hotel_id': hotel_id,
                'progress': progress,
            }

            if args.json:
                # Include the CSV data in the response
                result['records'] = bot.rate_manager.csv_data

            output_json(result)
        else:
            output_json({'status': 'error', 'action': 'update-rates', 'error': 'Rate manager not available'})

    except Exception as e:
        output_json({'status': 'error', 'action': 'update-rates', 'error': str(e)})
    finally:
        await bot.close()


# ─── list-messages ────────────────────────────────────────────

async def cmd_list_messages(args):
    from messaging import MessagingManager

    bot, logged_in = await _init_bot()
    try:
        if not logged_in:
            output_json({'status': 'error', 'action': 'list-messages', 'error': 'Login failed'})
            return

        messaging = MessagingManager(bot.page)
        hotel_id = args.hotel_id or DEFAULT_HOTEL_ID

        result = await messaging.list_messages(
            hotel_id=hotel_id,
            filter_type=args.filter,
        )

        output_json({
            'status': 'success',
            'action': 'list-messages',
            **result,
        })

    except Exception as e:
        output_json({'status': 'error', 'action': 'list-messages', 'error': str(e)})
    finally:
        await bot.close()


# ─── read-message ─────────────────────────────────────────────

async def cmd_read_message(args):
    from messaging import MessagingManager

    bot, logged_in = await _init_bot()
    try:
        if not logged_in:
            output_json({'status': 'error', 'action': 'read-message', 'error': 'Login failed'})
            return

        messaging = MessagingManager(bot.page)
        hotel_id = args.hotel_id or DEFAULT_HOTEL_ID

        # First list messages to navigate to inbox
        await messaging.list_messages(hotel_id=hotel_id, filter_type=args.filter)

        # Then read the specific conversation
        conversation = await messaging.read_conversation(
            hotel_id=hotel_id,
            message_index=args.index,
        )

        if conversation:
            output_json({
                'status': 'success',
                'action': 'read-message',
                'hotel_id': hotel_id,
                **conversation,
            })
        else:
            output_json({'status': 'error', 'action': 'read-message', 'error': 'Message not found'})

    except Exception as e:
        output_json({'status': 'error', 'action': 'read-message', 'error': str(e)})
    finally:
        await bot.close()


# ─── send-message ──────────────────────────────────────────────

async def cmd_send_message(args):
    from messaging import MessagingManager

    bot, logged_in = await _init_bot()
    try:
        if not logged_in:
            output_json({'status': 'error', 'action': 'send-message', 'error': 'Login failed'})
            return

        messaging = MessagingManager(bot.page)
        hotel_id = args.hotel_id or DEFAULT_HOTEL_ID

        # Navigate to inbox and set filter before sending
        await messaging.list_messages(hotel_id=hotel_id, filter_type=args.filter)

        result = await messaging.send_reply(
            hotel_id=hotel_id,
            message_index=args.index,
            reply_text=args.message,
        )

        output_json({
            'status': 'success' if result.get('sent') else 'error',
            'action': 'send-message',
            **result,
        })

    except Exception as e:
        output_json({'status': 'error', 'action': 'send-message', 'error': str(e)})
    finally:
        await bot.close()


# ─── list-properties ──────────────────────────────────────────

async def cmd_list_properties(args):
    from messaging import MessagingManager

    bot, logged_in = await _init_bot()
    try:
        if not logged_in:
            output_json({'status': 'error', 'action': 'list-properties', 'error': 'Login failed'})
            return

        messaging = MessagingManager(bot.page)
        properties = await messaging.list_properties()

        output_json({
            'status': 'success',
            'action': 'list-properties',
            'count': len(properties),
            'properties': properties,
        })

    except Exception as e:
        output_json({'status': 'error', 'action': 'list-properties', 'error': str(e)})
    finally:
        await bot.close()


# ─── CLI Parser ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Booking.com Extranet CLI — automation tool for AI agents',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py download-reservations --start 2026-03-01 --end 2026-03-31
  python cli.py download-reservations --start 2026-03-01 --end 2026-09-30 --json
  python cli.py update-rates
  python cli.py update-rates --hotel-id 13616005 --json
  python cli.py list-messages
  python cli.py list-messages --hotel-id 13616005 --filter all
  python cli.py read-message --index 0
        """,
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # ─── download-reservations ────────────────────────────────
    dl_parser = subparsers.add_parser(
        'download-reservations',
        help='Download reservations as Excel file (or JSON with --json)',
    )
    dl_parser.add_argument('--start', required=True, help='Start date (YYYY-MM-DD)')
    dl_parser.add_argument('--end', required=True, help='End date (YYYY-MM-DD)')
    dl_parser.add_argument('--date-type', default='arrival', choices=['arrival', 'departure', 'booking'])
    dl_parser.add_argument('--output-dir', default=None, help='Directory to save Excel (default: ./downloads/)')
    dl_parser.add_argument('--json', action='store_true', help='Return reservation data as JSON instead of Excel')

    # ─── update-rates ─────────────────────────────────────────
    rates_parser = subparsers.add_parser(
        'update-rates',
        help='Update room rates from CSV file',
    )
    rates_parser.add_argument('--hotel-id', default=None, help='Hotel ID (default: from .env)')
    rates_parser.add_argument('--json', action='store_true', help='Include full record data in JSON output')

    # ─── list-messages ────────────────────────────────────────
    msg_parser = subparsers.add_parser(
        'list-messages',
        help='List guest messages from inbox',
    )
    msg_parser.add_argument('--hotel-id', default=None, help='Hotel ID (default: from .env)')
    msg_parser.add_argument('--filter', default='unanswered', choices=['unanswered', 'sent', 'all'],
                            help='Message filter (default: unanswered)')

    # ─── read-message ─────────────────────────────────────────
    read_parser = subparsers.add_parser(
        'read-message',
        help='Read a specific conversation',
    )
    read_parser.add_argument('--index', type=int, default=0, help='Message index from list-messages (default: 0)')
    read_parser.add_argument('--hotel-id', default=None, help='Hotel ID (default: from .env)')
    read_parser.add_argument('--filter', default='unanswered', choices=['unanswered', 'sent', 'all'],
                            help='Message filter to use when listing (default: unanswered)')

    # ─── send-message ──────────────────────────────────────────
    send_parser = subparsers.add_parser(
        'send-message',
        help='Send a reply to a guest conversation',
    )
    send_parser.add_argument('--index', type=int, required=True, help='Message index from list-messages (0-based)')
    send_parser.add_argument('--message', required=True, help='Reply text to send')
    send_parser.add_argument('--hotel-id', default=None, help='Hotel ID (default: from .env)')
    send_parser.add_argument('--filter', default='unanswered', choices=['unanswered', 'sent', 'all'],
                             help='Inbox filter to use when selecting conversation (default: unanswered)')

    # ─── list-properties ───────────────────────────────────────
    subparsers.add_parser(
        'list-properties',
        help='List all properties with unread message counts',
    )

    args = parser.parse_args()

    commands = {
        'download-reservations': cmd_download_reservations,
        'update-rates': cmd_update_rates,
        'list-messages': cmd_list_messages,
        'read-message': cmd_read_message,
        'send-message': cmd_send_message,
        'list-properties': cmd_list_properties,
    }
    asyncio.run(commands[args.command](args))


if __name__ == '__main__':
    main()
