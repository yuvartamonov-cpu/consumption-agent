# Paperclip AI — coding tasks for 12.05.2026

Updated after today's completed fixes.

## Done today — do not re-open
- Whitelist/access control fixed in `telegram_bot.py`
- Markdown escaping fixed for bot replies
- `/items` date logic fixed via safe month addition
- Vision modules added: `vision_item.py`, `vision_receipt.py`
- `openai-vision` skill created and packaged

## Tasks for coding agents

### P0

#### 1) Fix `/add_item` parsing for natural input
- **Why now:** currently new items like `пиджак lardini 6 мес` are saved incorrectly.
- **Current bug:** `brand = NULL`, `replace_after_months = NULL`, whole phrase lands in `name`.
- **Examples to support:**
  - `пиджак lardini 6 мес`
  - `поло hamington 3 мес`
  - `Носки | бренд Nike | замена 12 мес`
- **Required changes:**
  - improve parsing in `brand_parser.py`
  - in `cmd_add_item` save replacement term to `replace_after_months`
  - do not store replacement term only in `lifespan_months`
  - add tests for natural language parsing
- **Expected result:** new items get correct `name`, `brand`, `replace_after_months`

#### 2) Enable JobQueue in Telegram bot
- **Current state:** bot logs `JobQueue is unavailable; skipping in-process daily schedule`
- **Required changes:**
  - install/use `python-telegram-bot[job-queue]`
  - ensure service starts cleanly after restart
  - verify daily jobs actually register
- **Expected result:** no JobQueue warning in service logs

#### 3) Add replacement alerts for inventory items
- **Depends on:** tasks 1 and 2
- **Required changes:**
  - implement daily scan of `items.replace_after_months`
  - alert 30 days before replacement date
  - add button `✅ Заменено`
  - update `replace_notified_at` or `status='replaced'`
- **Expected result:** replacement reminders work automatically

### P1

#### 4) Debug photo saving in `/add_item`
- **Current state:** code path exists, but DB shows `item_photos = 0`
- **Required changes:**
  - verify `/add_item` + photo flow end to end
  - ensure records are created in `media_assets` and `item_photos`
  - add smoke test or reproducible manual check
- **Expected result:** photo attachments are persisted and linked to items

#### 5) Improve `/find_car` recommendation quality
- **Current state:** `cmd_find_car` already exists
- **Required changes:**
  - use `carsharing_trips` history (39 rows)
  - account for known preferences: `FAW Bestune T77`, `Bay 24`, insurance
  - add test scenarios such as `3ч 80км`, `сутки 120км`
- **Expected result:** recommendations reflect real past usage and preferences

#### 6) Add SQLite retry/backoff for writes
- **Problem:** possible `database is locked` under concurrent writes
- **Required changes:**
  - wrap write paths with retry + exponential backoff
  - prioritize `telegram_bot.py`, `credit_alerts.py`, import scripts
- **Expected result:** fewer transient DB write failures

#### 7) Credit Monitor: add Tinkoff support
- **Required changes:**
  - add sender pattern for Tinkoff credit notifications
  - parse payment amount and due date
  - validate on a real email sample
- **Expected result:** Tinkoff credit alerts appear in `credit_alerts`

### P2

#### 8) Refresh Ozon cookies and restore import
- update `.ozon_cookies.txt`
- verify Ozon receipt import end to end

#### 9) Check WB / Megamarket import paths
- verify IMAP search and sender patterns
- confirm whether emails actually exist and are matched

#### 10) Fix PDF report warning in `gen_report.py`
- remove warning related to `\n` and DejaVu/fpdf2

## Recommended execution order
1. `/add_item` parsing
2. JobQueue
3. replacement alerts
4. `/add_item` photo saving
5. SQLite retry
6. `/find_car`
7. Tinkoff support
8. Ozon / WB / PDF cleanup
