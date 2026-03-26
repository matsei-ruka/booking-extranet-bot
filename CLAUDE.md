# Booking.com Extranet Bot — Project Knowledge

## Overview
Automated bot for Booking.com partner extranet. Uses real Chrome (not Playwright's bundled Chromium) to avoid CAPTCHA/bot detection. Connects via Chrome DevTools Protocol (CDP) on port 9222.

## Architecture
- **cli.py** — CLI entry point with subcommands, outputs JSON to stdout, logs to stderr
- **booking_extranet_bot.py** — Main bot class: Chrome launch, login, session management
- **rate_manager.py** — Rate/pricing updates via the calendar inline side panel
- **reservations.py** — Reservation data scraping and Excel export
- **public/seasonal_room_prices_optimized.csv** — Rate pricing data with status tracking

## Account Structure
- **Group account** (multi-property) — lands on group homepage after login
- **Hotel account ID**: 16819008
- **Properties**:
  - Sultan Sunscape (hotel_id: 13616005) — main property, has room rate CSV data
  - Azure Retreat (hotel_id: 10353978)
  - Coral Bay Dream (hotel_id: 10353912)

## Login Flow
1. Navigate to `admin.booking.com/hotel/hoteladmin/`
2. Fill `input[name="loginname"]` → click `button[type="submit"]`
3. Fill `input[name="password"]` → click `button[type="submit"]`
4. If 2FA required: verification method page appears with links:
   - `a:has-text("Text message (SMS)")` → phone selection → code input `input[name="sms_code"]`
   - `a:has-text("Pulse app")`
   - `a:has-text("Phone call")`
5. Session persists in `.chrome-data/` directory — subsequent runs skip login
6. **CRITICAL**: Must use real Chrome, not Playwright Chromium. Playwright Chromium triggers image CAPTCHA.

## Chrome Setup
- Launched with `--remote-debugging-port=9222 --user-data-dir=.chrome-data`
- Cross-platform paths: macOS `/Applications/Google Chrome.app/...`, Linux `/usr/bin/google-chrome`
- Playwright connects via `chromium.connect_over_cdp('http://localhost:9222')`
- **Known issue**: `page.on('download')` does NOT work reliably with CDP connection — downloads must be handled by scraping data directly

## Navigation (Group Level)
- Group homepage: `admin.booking.com/hotel/hoteladmin/groups/home/index.html`
- Group reservations: `admin.booking.com/hotel/hoteladmin/groups/reservations/index.html`
- Nav items use `li[data-nav-tag="..."]`:
  - `group_overview`, `group_reservations`, `group_strategy`, `group_reviews`
  - `group_finance`, `group_bulk_edit`, `group_opportunity_center`, `group_market_insights`

## Navigation (Property Level)
- Property home: `extranet_ng/manage/home.html?hotel_id={id}&ses={ses}`
- Calendar: `extranet_ng/manage/calendar/monthly.html?hotel_id={id}&ses={ses}`
- Nav items: `li[data-nav-tag="availability"]` → submenu with `availability_calendar`
- **Must navigate to property before accessing property-level features**

## Calendar / Rate Management
The calendar uses an **inline side panel** (not modals):
- Date inputs: `#selection-start-date`, `#selection-end-date`
- Rooms to sell: `select#roomsToSell` (often disabled in monthly view)
- Price inputs: `input[id^="price-"]` (e.g., `#price-52150641`)
- Open/Closed toggle: text-based "Open" / "Closed"
- Save/Cancel buttons at bottom
- Rate plans visible: Standard Rate, Non-refundable Rate, Weekly Rate

## Reservations Page (Group Level)
- URL: `groups/reservations/index.html?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD&dateType=ARRIVAL`
- **Date params in URL work** — page loads with correct filter
- Date type dropdown: `select#type` (values: ARRIVAL, DEPARTURE, BOOKING)
- Date picker: `#peg-reservations-ranged` (Vue component, display only)
- Pagination: `select#peg-reservations-table-pagination` (10, 30, 50 per page)
- Next page: `button[aria-label="Next page"]`
- Count text: "1-30 of 117 reservations"
- Download button exists but is unreliable via CDP — **we scrape the table instead**
- Table columns (in order): Property ID, Property Name, Location, Guest name, Check-in, Check-out, Status, Total Payment, Commission and charges, Reservation Number, Booked on
- Data loads async after page — wait for `table tbody tr a` elements to appear (~6-8 seconds)

## Messaging / Inbox (Property Level)
- URL: `extranet_ng/manage/messaging/inbox.html?hotel_id={id}&ses={ses}&lang=en`
- **SPA**: Vue.js app, clicking messages updates right panel without navigation
- **IMPORTANT**: Booking.com changes DOM selectors frequently. All selectors use fallback
  chains defined in `messaging.py` → `SELECTORS` dict. When a selector breaks, add the
  new one at position 0 and keep old ones as fallbacks.
- **Verified selectors** (as of March 2026):
  - Conversation items: `button[data-test-id="inbox-conversation-item"]`
  - Filter dropdown: `select[data-test-id="inbox-conversation-filter-select"]`
    - Values: `PENDING_PROPERTY` (unanswered), `PENDING_GUEST` (sent), `ALL`
  - Guest name inside item: `.list-item__title-text`
  - Reply textarea: `textarea.chat-form__textarea`
  - Send button: `button:has-text("Send")` (NO data-test-id on this button)
- **Reservation details**: extracted via JS `body.innerText` label scanning (right panel)

## CLI Commands
```bash
python cli.py list-properties
python cli.py list-messages --hotel-id ID [--filter unanswered|sent|all]
python cli.py read-message --hotel-id ID --index N
python cli.py send-message --hotel-id ID --index N --message "text"
python cli.py download-reservations --start YYYY-MM-DD --end YYYY-MM-DD [--date-type arrival|departure|booking] [--json] [--output-dir path]
python cli.py update-rates [--hotel-id ID]
```
- JSON output to stdout, logs to stderr + `booking_bot.log`
- Default hotel_id: 13616005 (Sultan Sunscape)

## Dependencies
- playwright (connects to real Chrome, not bundled browser)
- pyotp (optional TOTP)
- python-dotenv
- pandas, openpyxl, xlrd (Excel handling)
- asyncio-throttle, requests

## Environment Variables (.env)
- `BOOKING_USERNAME` — required
- `BOOKING_PASSWORD` — required
- `PULSE_TOTP_SECRET` — optional
- `BOOKING_HOTEL_ID` — optional (default: 13616005)

## Known Issues & Gotchas
1. **Playwright download handler doesn't work with CDP** — use scraping instead
2. **Rooms to sell dropdown** is disabled in monthly calendar view
3. **Rapid login attempts trigger CAPTCHA** — use real Chrome + persistent session
4. **Group page vs property page** — rate management and inbox are property-level, reservations can be group-level
5. **CSS classes are obfuscated** (BUI design system) — prefer `data-test-id` selectors when available
6. **Vue datepicker** on reservations page ignores programmatic value changes — use URL params instead
7. **Reservation data loads async** — must wait 6-8 seconds after page load for table to populate
