#!/usr/bin/env python3
"""
Booking.com Extranet CLI Tool

A command-line interface for Booking.com extranet automation.
Designed for use by AI agents — outputs structured JSON to stdout.

Usage:
    python cli.py download-reservations --start 2026-03-01 --end 2026-03-31
    python cli.py update-rates
    python cli.py update-rates --hotel-id 13616005
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


def output_json(data: dict):
    """Print JSON result to stdout for AI agent consumption"""
    print(json.dumps(data, indent=2, default=str))


async def cmd_download_reservations(args):
    """Download reservations as Excel file"""
    from booking_extranet_bot import BookingExtranetBot
    from reservations import ReservationsManager

    bot = BookingExtranetBot()
    try:
        await bot.initialize_browser(headless=False)

        if not await bot.login():
            output_json({'status': 'error', 'action': 'download-reservations', 'error': 'Login failed'})
            return

        reservations = ReservationsManager(bot.page)

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
                'params': {
                    'start': args.start,
                    'end': args.end,
                    'date_type': args.date_type,
                },
            })
        else:
            output_json({
                'status': 'error',
                'action': 'download-reservations',
                'error': 'Download failed or timed out',
            })
    except Exception as e:
        output_json({'status': 'error', 'action': 'download-reservations', 'error': str(e)})
    finally:
        await bot.close()


async def cmd_update_rates(args):
    """Update rates from CSV file"""
    from booking_extranet_bot import BookingExtranetBot, DEFAULT_HOTEL_ID

    bot = BookingExtranetBot()
    try:
        await bot.initialize_browser(headless=False)

        if not await bot.login():
            output_json({'status': 'error', 'action': 'update-rates', 'error': 'Login failed'})
            return

        hotel_id = args.hotel_id or os.getenv('BOOKING_HOTEL_ID', DEFAULT_HOTEL_ID)

        if not await bot.navigate_to_calendar(hotel_id=hotel_id):
            output_json({'status': 'error', 'action': 'update-rates', 'error': 'Failed to navigate to calendar'})
            return

        if bot.rate_manager:
            success = await bot.rate_manager.process_all_rooms()
            progress = bot.rate_manager.get_progress_summary()

            output_json({
                'status': 'success' if success else 'partial',
                'action': 'update-rates',
                'hotel_id': hotel_id,
                'progress': progress,
            })
        else:
            output_json({'status': 'error', 'action': 'update-rates', 'error': 'Rate manager not available'})

    except Exception as e:
        output_json({'status': 'error', 'action': 'update-rates', 'error': str(e)})
    finally:
        await bot.close()


def main():
    parser = argparse.ArgumentParser(
        description='Booking.com Extranet CLI — automation tool for AI agents',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py download-reservations --start 2026-03-01 --end 2026-03-31
  python cli.py download-reservations --start 2026-01-01 --end 2026-12-31 --date-type booking
  python cli.py update-rates
  python cli.py update-rates --hotel-id 13616005
        """,
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # ─── download-reservations ────────────────────────────────
    dl_parser = subparsers.add_parser(
        'download-reservations',
        help='Download reservations as Excel file',
    )
    dl_parser.add_argument(
        '--start', required=True,
        help='Start date (YYYY-MM-DD)',
    )
    dl_parser.add_argument(
        '--end', required=True,
        help='End date (YYYY-MM-DD)',
    )
    dl_parser.add_argument(
        '--date-type', default='arrival',
        choices=['arrival', 'departure', 'booking'],
        help='Date filter type (default: arrival)',
    )
    dl_parser.add_argument(
        '--output-dir', default=None,
        help='Directory to save the file (default: ./downloads/)',
    )

    # ─── update-rates ─────────────────────────────────────────
    rates_parser = subparsers.add_parser(
        'update-rates',
        help='Update room rates from CSV file',
    )
    rates_parser.add_argument(
        '--hotel-id', default=None,
        help='Hotel ID to update (default: from .env or 13616005)',
    )

    args = parser.parse_args()

    if args.command == 'download-reservations':
        asyncio.run(cmd_download_reservations(args))
    elif args.command == 'update-rates':
        asyncio.run(cmd_update_rates(args))


if __name__ == '__main__':
    main()
