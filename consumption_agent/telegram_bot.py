#!/usr/bin/env python3
"""
Consumption Agent Telegram Bot
ÐšÐ¾Ð¼Ð°Ð½Ð´Ñ‹:
  /start â€” Ð¿Ñ€Ð¸Ð²ÐµÑ‚ÑÑ‚Ð²Ð¸Ðµ
  /list  â€” Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ Ð¿Ð¾ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸ÑÐ¼
  /alerts â€” Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ðµ Ð°Ð»ÐµÑ€Ñ‚Ñ‹
  /add <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ> [<Ñ†ÐµÐ½Ð°>] [<ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ>] â€” Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ñ‚Ð¾Ð²Ð°Ñ€
  /check â€” ÑÑ‚Ð°Ñ‚Ð¸ÑÑ‚Ð¸ÐºÐ°
  /help â€” ÑÐ¿Ñ€Ð°Ð²ÐºÐ°

Ð—Ð°Ð¿ÑƒÑÐº: CONSUMPTION_BOT_TOKEN=xxx python3 telegram_bot.py
"""

import logging, os, sys, re, json, subprocess, tempfile, time, html, traceback, random, asyncio
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import urlopen, Request

def get_db_with_retry(max_retries=3, backoff_base=0.5):
    """ÐŸÐ¾Ð´ÐºÐ»ÑŽÑ‡ÐµÐ½Ð¸Ðµ Ðº Ð‘Ð” Ñ retry Ð¿Ñ€Ð¸ Ð±Ð»Ð¾ÐºÐ¸Ñ€Ð¾Ð²ÐºÐµ (database is locked)."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'consumption.db')
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < max_retries - 1:
                sleep_time = backoff_base * (2 ** attempt) + random.uniform(0, 0.1)
                time.sleep(sleep_time)
                continue
            raise
    raise sqlite3.OperationalError("Database is locked after retries")


def db_execute_with_retry(conn, query, params=(), max_retries=3, backoff_base=0.5):
    """Ð’Ñ‹Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ðµ Ð·Ð°Ð¿Ñ€Ð¾ÑÐ° Ñ retry Ð¿Ñ€Ð¸ Ð±Ð»Ð¾ÐºÐ¸Ñ€Ð¾Ð²ÐºÐµ."""
    for attempt in range(max_retries):
        try:
            return conn.execute(query, params)
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < max_retries - 1:
                sleep_time = backoff_base * (2 ** attempt) + random.uniform(0, 0.1)
                time.sleep(sleep_time)
                continue
            raise
    raise sqlite3.OperationalError("Database is locked after retries")


def esc_md(text):
    """Escape Markdown V1 special characters for Telegram."""
    if not text:
        return text
    for ch in ('\\', '`', '*', '_', '[', ']', '(', ')'):
        text = str(text).replace(ch, '\\' + ch)
    return text


def add_months_safe(dt, months):
    """Ð”Ð¾Ð±Ð°Ð²Ð»ÑÐµÑ‚ Ð¼ÐµÑÑÑ†Ñ‹ Ðº Ð´Ð°Ñ‚Ðµ Ð±ÐµÐ· Ð¿Ð°Ð´ÐµÐ½Ð¸Ñ Ð½Ð° 29/30/31 Ñ‡Ð¸ÑÐ»Ðµ."""
    months = int(months)
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def parse_drive_request(text: str):
    """ÐŸÐ°Ñ€ÑÐ¸Ñ‚ '3Ñ‡ 80ÐºÐ¼' Ð¸Ð»Ð¸ '2 Ñ‡Ð°ÑÐ° 60 ÐºÐ¼'"""
    hours = None
    km = None
    t = text.lower()
    h_match = re.search(r'(\d+)[\s]*(?:Ñ‡|Ñ‡Ð°Ñ|Ñ‡Ð°ÑÐ°|Ñ‡Ð°ÑÐ¾Ð²|h)', t)
    k_match = re.search(r'(\d+)[\s]*(?:ÐºÐ¼|km)', t)
    if h_match:
        hours = float(h_match.group(1))
    if k_match:
        km = float(k_match.group(1))
    return hours, km


def calculate_drive_cost(tariff, hours, km):
    """Ð Ð°ÑÑ‡Ñ‘Ñ‚ ÑÑ‚Ð¾Ð¸Ð¼Ð¾ÑÑ‚Ð¸ Ð¿Ð¾ÐµÐ·Ð´ÐºÐ¸ Ð¿Ð¾ Ñ‚Ð°Ñ€Ð¸Ñ„Ñƒ Ð¿Ñ€Ð¾Ð²Ð°Ð¹Ð´ÐµÑ€Ð°."""
    km_rate = tariff['km_rate'] or 0
    rate_type = tariff['rate_type']

    if rate_type == 'flat_km':
        # Ð¤Ð¸ÐºÑÐ¸Ñ€Ð¾Ð²Ð°Ð½Ð½Ñ‹Ð¹ Ñ‚Ð°Ñ€Ð¸Ñ„ (ÑÑƒÑ‚ÐºÐ¸/Ñ‡Ð°ÑÑ‹) + ÑÑ‚Ð¾Ð¸Ð¼Ð¾ÑÑ‚ÑŒ Ð·Ð° ÐºÐ¼
        base = (tariff['hourly_rate'] or 0) + km * km_rate
    else:
        # ÐŸÐ¾Ð¼Ð¸Ð½ÑƒÑ‚Ð½Ñ‹Ð¹/Ð¿Ð¾Ñ‡Ð°ÑÐ¾Ð²Ð¾Ð¹ Ñ‚Ð°Ñ€Ð¸Ñ„ + ÑÑ‚Ð¾Ð¸Ð¼Ð¾ÑÑ‚ÑŒ Ð·Ð° ÐºÐ¼
        h_rate = tariff['hourly_rate'] or 0
        base = h_rate * hours + km * km_rate

    return max(round(base, -1), 500)  # Ð¾ÐºÑ€ÑƒÐ³Ð»ÑÐµÐ¼ Ð´Ð¾ 10â‚½, Ð¼Ð¸Ð½Ð¸Ð¼ÑƒÐ¼ 500â‚½

from telegram import Update, PhotoSize, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# ÐÐ¾Ð²Ñ‹Ð¹ Ð¼Ð¾Ð´ÑƒÐ»ÑŒ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð·Ð°Ñ†Ð¸Ð¸ (Ð¨Ð°Ð³ 5 Ñ€ÐµÑ„Ð°ÐºÑ‚Ð¾Ñ€Ð¸Ð½Ð³Ð°)
try:
    from consumption.categorize import categorize as auto_categorize, slug_to_cat_id
except ImportError:
    auto_categorize = lambda n: None
    slug_to_cat_id = lambda s: None

try:
    from consumption.db import DB_PATH as SHARED_DB_PATH, connect as db_connect
except ImportError:
    SHARED_DB_PATH = None
    db_connect = None

DB_PATH = SHARED_DB_PATH or os.path.join(SCRIPT_DIR, 'consumption.db')
RECEIPTS_DIR = os.path.join(SCRIPT_DIR, 'receipts')
Path(RECEIPTS_DIR).mkdir(exist_ok=True)
TOKEN = os.environ.get('CONSUMPTION_BOT_TOKEN', '')
# OWNER_CHAT_ID â€” ID Ð²Ð»Ð°Ð´ÐµÐ»ÑŒÑ†Ð° Ð±Ð¾Ñ‚Ð°. ÐžÐ±ÑÐ·Ð°Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ð¹ Ð¿Ð°Ñ€Ð°Ð¼ÐµÑ‚Ñ€ Ð¾ÐºÑ€ÑƒÐ¶ÐµÐ½Ð¸Ñ.
# Default Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð´Ð»Ñ Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ð¾Ð¹ Ñ€Ð°Ð·Ñ€Ð°Ð±Ð¾Ñ‚ÐºÐ¸. Ð’ Ð¿Ñ€Ð¾Ð´Ð°ÐºÑˆÐµÐ½Ðµ Ð·Ð°Ð´Ð°Ñ‘Ñ‚ÑÑ Ñ‡ÐµÑ€ÐµÐ· .env.
_owner_default = os.environ.get('OWNER_CHAT_ID_DEFAULT', '1477860192')
OWNER_CHAT_ID = int(os.environ.get('OWNER_CHAT_ID', _owner_default))


def _parse_allowed_chat_ids(value: str | None) -> set[int]:
    ids: set[int] = set()
    if not value:
        return ids
    for part in value.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logging.getLogger(__name__).warning("Invalid Telegram chat id ignored: %r", part)
    return ids


ALLOWED_CHAT_IDS = _parse_allowed_chat_ids(
    os.environ.get('TELEGRAM_ALLOWED_CHAT_IDS') or os.environ.get('ALLOWED_CHAT_IDS')
)
if not ALLOWED_CHAT_IDS and OWNER_CHAT_ID:
    ALLOWED_CHAT_IDS = {OWNER_CHAT_ID}


def get_credit_alert(alert_id: int):
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    row = conn.execute(
        'SELECT id, sender_name, payment_date, payment_amount, paid_confirmed_at FROM credit_alerts WHERE id = ?',
        (alert_id,)
    ).fetchone()
    conn.close()
    return row


def get_fine(fine_id: int):
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    row = conn.execute(
        'SELECT id, type, number, amount, description, vehicle, fine_date, vendor, paid_confirmed_at FROM fines WHERE id = ?',
        (fine_id,)
    ).fetchone()
    conn.close()
    return row


def confirm_fine_paid(fine_id: int, via: str = 'telegram_button') -> bool:
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    cur = conn.execute(
        '''
        UPDATE fines
        SET paid_confirmed_at = datetime('now'),
            type = CASE WHEN type = 'new' THEN 'fined' ELSE type END
        WHERE id = ? AND paid_confirmed_at IS NULL
        ''',
        (fine_id,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def confirm_credit_alert_paid(alert_id: int, via: str = 'telegram_button') -> bool:
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    cur = conn.execute(
        '''
        UPDATE credit_alerts
        SET paid_confirmed_at = datetime('now'),
            paid_confirmed_via = ?,
            is_active = 0
        WHERE id = ? AND paid_confirmed_at IS NULL
        ''',
        (via, alert_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def decode_qr(image_path: str) -> dict:
    """Decode QR code from image and return structured data."""
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
    except ImportError:
        log.error("pyzbar or PIL not installed. Install with: pip install pyzbar pillow")
        return {}
    try:
        decoded = decode(Image.open(image_path))
        if not decoded:
            return {}
        data = decoded[0].data.decode('utf-8', errors='replace')
        # Parse Ozon QR: "t=20260504T2051&s=1234.56&fn=9999078900005412&i=12345&fp=1234567890"
        result = {}
        for part in data.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                result[k] = v
        return result
    except Exception as e:
        log.error(f"QR decode failed: {e}")
        return {}


TAG_BRANDS = [
    'ETRO', 'MASSIMO DUTTI', 'ZEGNA', 'GUCCI', 'PRADA', 'ARMANI', 'BOSS', 'HUGO', 'ZARA',
    'MASSIMO', 'LACOSTE', 'TOMMY', 'RALPH', 'POLO', 'DIOR', 'VALENTINO',
    'BURBERRY', 'DOLCE', 'GABBANA', 'LOUIS', 'VUITTON', 'BRIONI', 'CANALI'
]
TAG_COLOR_WORDS = {
    'MULTICOL', 'MULTICOLOR', 'BLACK', 'WHITE', 'BLUE', 'NAVY', 'GREY', 'GRAY',
    'RED', 'GREEN', 'BEIGE', 'BROWN', 'ROSA', 'PINK', 'YELLOW', 'ORANGE', 'IVORY'
}
TAG_MODEL_WORDS = {'CAMICIE', 'CAMICIA', 'SHIRT', 'POLO', 'TSHIRT', 'T-SHIRT', 'JEANS', 'PANTS'}


def _clean_ocr_lines(text: str) -> str:
    lines = []
    for raw_line in (text or '').splitlines():
        line = re.sub(r'[^\w\s.,%â‚½â‚¬$Â£/#:-]', ' ', raw_line, flags=re.UNICODE)
        line = re.sub(r'\s+', ' ', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


def _ocr_crop(image_path: str, box_ratio: tuple[float, float, float, float], lang: str = 'eng', psm: str = '6') -> str:
    """OCR helper Ð´Ð»Ñ Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ñ‹Ñ… Ð·Ð¾Ð½ Ð±Ð¸Ñ€ÐºÐ¸."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
        img = Image.open(image_path)
        w, h = img.size
        box = (int(w * box_ratio[0]), int(h * box_ratio[1]), int(w * box_ratio[2]), int(h * box_ratio[3]))
        crop = ImageOps.grayscale(img.crop(box))
        crop = crop.resize((crop.width * 5, crop.height * 5))
        crop = ImageOps.autocontrast(crop)
        crop = ImageEnhance.Contrast(crop).enhance(3.0)
        path = image_path.rsplit('.', 1)[0] + f'_crop_{int(box_ratio[0]*100)}_{int(box_ratio[1]*100)}.png'
        crop.save(path)
        result = subprocess.run(
            ['tesseract', path, 'stdout', '-l', lang, '--oem', '1', '--psm', psm],
            capture_output=True, text=True, check=False, timeout=30
        )
        return _clean_ocr_lines(result.stdout.strip())
    except Exception as e:
        log.warning(f"Crop OCR failed: {e}")
        return ''


def _score_ocr_text(text: str) -> int:
    if not text:
        return 0
    lower = text.lower()
    digits = len(re.findall(r'\d', text))
    latin_words = len(re.findall(r'\b[A-Za-z]{3,}\b', text))
    cyr_words = len(re.findall(r'\b[Ð-Ð¯Ð°-ÑÐÑ‘]{3,}\b', text))
    markers = len(re.findall(r'[â‚½â‚¬$Â£]|\b(?:EUR|USD|RUB|SIZE|TAGLIA|Ð¤Ð|Ð¤ÐŸ|Ð˜Ð¢ÐžÐ“Ðž)\b', text, flags=re.I))
    score = digits + latin_words * 3 + cyr_words * 2 + markers * 8

    # ÐžÑ‡ÐµÐ½ÑŒ ÑÐ¸Ð»ÑŒÐ½Ñ‹Ðµ ÑÐ¸Ð³Ð½Ð°Ð»Ñ‹ Ð½Ð°ÑÑ‚Ð¾ÑÑ‰ÐµÐ³Ð¾ Ñ‡ÐµÐºÐ°/Ð±Ð¸Ñ€ÐºÐ¸. Ð­Ñ‚Ð¾ Ð·Ð°Ñ‰Ð¸Ñ‰Ð°ÐµÑ‚ Ñ‡ÐµÐºÐ¸ Ð¾Ñ‚ Ð²Ñ‹Ð±Ð¾Ñ€Ð° ÑˆÑƒÐ¼Ð½Ð¾Ð³Ð¾ OCR-Ð²Ð°Ñ€Ð¸Ð°Ð½Ñ‚Ð°.
    for kw in ['ÐºÐ°ÑÑÐ¾Ð²Ñ‹Ð¹ Ñ‡ÐµÐº', 'Ñ„Ð¸ÑÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹', 'Ñ„Ð½', 'Ñ„Ð¿', 'Ð¸Ñ‚Ð¾Ð³', 'Ð±ÐµÐ·Ð½Ð°Ð»Ð¸Ñ‡Ð½Ñ‹Ð¼Ð¸']:
        if kw in lower:
            score += 120
    for kw in ['etro', 'camicie', 'multicol', 'taglia', 'size']:
        if kw in lower:
            score += 100
    if re.search(r'\b\d{4,6}[ /-]\d{6,10}\b', text):
        score += 120
    if re.search(r'\b\d{12,14}\b', text):
        score += 60
    return score


def ocr_image(image_path: str) -> str:
    """Run Tesseract OCR with several preprocessing variants and keep the best result."""
    prepared_paths = [image_path]
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        img = Image.open(image_path)
        variants = []

        gray = ImageOps.grayscale(img)
        variants.append(('gray', gray))

        upscaled = gray.resize((gray.width * 3, gray.height * 3))
        upscaled = ImageOps.autocontrast(upscaled)
        upscaled = ImageEnhance.Contrast(upscaled).enhance(2.4)
        variants.append(('up', upscaled))

        bw = upscaled.point(lambda x: 0 if x < 170 else 255)
        bw = bw.filter(ImageFilter.SHARPEN)
        variants.append(('bw', bw))

        for suffix, variant in variants:
            prep_path = image_path.rsplit('.', 1)[0] + f'_{suffix}.png'
            variant.save(prep_path)
            prepared_paths.append(prep_path)
    except Exception as e:
        log.warning(f"OCR preprocess failed: {e}")

    best_text = ''
    best_score = -1
    for candidate in prepared_paths:
        for psm in ('6', '11'):
            try:
                result = subprocess.run(
                    ['tesseract', candidate, 'stdout', '-l', 'rus+eng', '--oem', '1', '--psm', psm],
                    capture_output=True, text=True, check=True, timeout=30
                )
                text = _clean_ocr_lines(result.stdout.strip())
                score = _score_ocr_text(text)
                if score > best_score:
                    best_text = text
                    best_score = score
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                log.warning(f'OCR timeout for {candidate} psm={psm}')
                continue

    if not best_text:
        log.error("OCR failed: no usable text")
    return best_text


def classify_image_type(ocr_text: str) -> str:
    """ÐžÐ¿Ñ€ÐµÐ´ÐµÐ»ÑÐµÑ‚ Ñ‚Ð¸Ð¿ Ñ„Ð¾Ñ‚Ð¾: 'receipt', 'tag' Ð¸Ð»Ð¸ 'unknown'"""
    text = (ocr_text or '').lower()
    receipt_score = 0
    tag_score = 0

    if any(kw in text for kw in ['ÐºÐ°ÑÑÐ¾Ð²Ñ‹Ð¹ Ñ‡ÐµÐº', 'Ñ„Ð¸ÑÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹', 'Ñ„Ð½', 'Ñ„Ð¿', 'Ð¸Ñ‚Ð¾Ð³Ð¾', 'ÑÐ´Ð°Ñ‡Ð°', 'Ð½Ð°Ð»Ð¸Ñ‡Ð½Ñ‹Ð¼Ð¸', 'Ð±ÐµÐ·Ð½Ð°Ð»Ð¸Ñ‡']):
        receipt_score += 4
    if 'â‚½' in text or 'Ñ€ÑƒÐ±' in text:
        receipt_score += 2
    if len(re.findall(r'\d+[.,]\d{2}\s*â‚½?', text)) >= 2:
        receipt_score += 2

    if any(kw.lower() in text for kw in ['â‚¬', '$', 'eur', 'usd', 'gbp', 'hkd', 'multicol', 'taglia', 'size', 'article', 'camicie', 'camicia']):
        tag_score += 3
    if any(brand.lower() in text for brand in TAG_BRANDS):
        tag_score += 4
    if re.search(r'\b\d{12,14}\b', text):
        tag_score += 2
    if re.search(r'\b\d{4,6}[ /-]\d{6,10}\b', text):
        tag_score += 3
    if any(word.lower() in text for word in TAG_COLOR_WORDS):
        tag_score += 2

    if receipt_score >= tag_score + 2:
        return 'receipt'
    if tag_score >= receipt_score + 1:
        return 'tag'
    return 'unknown'


def _extract_barcode(image_path: str) -> str | None:
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
        decoded = decode(Image.open(image_path))
        for item in decoded:
            value = item.data.decode('utf-8', errors='ignore').strip()
            if value.isdigit() and 8 <= len(value) <= 14:
                return value
    except Exception as e:
        log.warning(f"Barcode decode failed: {e}")
    return None


def _extract_tag_size_from_image(image_path: str) -> str | None:
    """ÐŸÑ‹Ñ‚Ð°ÐµÑ‚ÑÑ Ð´Ð¾ÑÑ‚Ð°Ñ‚ÑŒ Ñ€Ð°Ð·Ð¼ÐµÑ€ Ð¸Ð· Ð¿Ñ€Ð°Ð²Ð¾Ð¹ Ñ‡Ð°ÑÑ‚Ð¸ Ð±Ð¸Ñ€ÐºÐ¸ (Ñ‡Ð°ÑÑ‚Ð¾ Ñ‡Ð¸ÑÐ»Ð¾ Ð² Ñ€Ð°Ð¼ÐºÐµ)."""
    try:
        from PIL import Image, ImageOps, ImageEnhance
        img = Image.open(image_path)
        w, h = img.size
        # ÐŸÑ€Ð°Ð²Ð°Ñ Ñ†ÐµÐ½Ñ‚Ñ€Ð°Ð»ÑŒÐ½Ð°Ñ Ð·Ð¾Ð½Ð° â€” Ñ‚Ð¸Ð¿Ð¸Ñ‡Ð½Ð¾Ðµ Ð¼ÐµÑÑ‚Ð¾ Ñ€Ð°Ð·Ð¼ÐµÑ€Ð° Ð½Ð° fashion-Ð±Ð¸Ñ€ÐºÐ°Ñ….
        boxes = [
            # Ð£Ð·ÐºÐ¸Ð¹ crop Ð¿Ð¾ Ñ€Ð°Ð¼ÐºÐµ Ñ€Ð°Ð·Ð¼ÐµÑ€Ð° ÑÐ¿Ñ€Ð°Ð²Ð°.
            (int(w * 0.63), int(h * 0.42), int(w * 0.81), int(h * 0.58)),
            (int(w * 0.62), int(h * 0.38), int(w * 0.82), int(h * 0.60)),
            (int(w * 0.60), int(h * 0.35), int(w * 0.84), int(h * 0.62)),
            # Ð‘Ð¾Ð»ÐµÐµ ÑˆÐ¸Ñ€Ð¾ÐºÐ¸Ðµ fallback-Ð·Ð¾Ð½Ñ‹.
            (int(w * 0.60), int(h * 0.30), int(w * 0.90), int(h * 0.58)),
            (int(w * 0.55), int(h * 0.25), int(w * 0.92), int(h * 0.65)),
        ]
        for box in boxes:
            crop = ImageOps.grayscale(img.crop(box))
            crop = crop.resize((crop.width * 6, crop.height * 6))
            crop = ImageOps.autocontrast(crop)
            crop = ImageEnhance.Contrast(crop).enhance(4.0)
            crop = crop.point(lambda x: 0 if x < 155 else 255)
            path = image_path.rsplit('.', 1)[0] + '_size.png'
            crop.save(path)
            for psm in ('6', '11'):
                result = subprocess.run(
                    ['tesseract', path, 'stdout', '-l', 'eng', '--oem', '1', '--psm', psm, '-c', 'tessedit_char_whitelist=0123456789'],
                    capture_output=True, text=True, check=False, timeout=30
                )
                nums = re.findall(r'\b(3[8-9]|4[0-9]|5[0-4])\b', result.stdout)
                if nums:
                    return nums[-1]
    except Exception as e:
        log.warning(f"Tag size crop OCR failed: {e}")
    return None


def _parse_receipt_lines(text: str, known_total: float | None = None) -> list[dict]:
    """ÐŸÐ°Ñ€ÑÐ¸Ñ‚ ÑÑ‚Ñ€Ð¾ÐºÐ¸ Ñ‡ÐµÐºÐ° Ozon / Ð»ÑŽÐ±Ð¾Ð¹ Ñ„Ð¾Ñ€Ð¼Ð°Ñ‚.
    Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ ÑÐ¿Ð¸ÑÐ¾Ðº Ñ‚Ð¾Ð²Ð°Ñ€Ð¾Ð²: [{name, price, qty, total}, ...].
    """
    items = []
    lines = (text or '').split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue

        # Ð¤Ð¾Ñ€Ð¼Ð°Ñ‚ Ozon: Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ Ð½Ð° 1-2 ÑÑ‚Ñ€Ð¾ÐºÐ°Ñ…, Ð·Ð°Ñ‚ÐµÐ¼ "1 x Ð¦Ð•ÐÐ" Ð½Ð° ÑÐ»ÐµÐ´ÑƒÑŽÑ‰ÐµÐ¹
        # Ð˜Ñ‰ÐµÐ¼ ÑÑ‚Ñ€Ð¾ÐºÑƒ Ð²Ð¸Ð´Ð° "1 x 123,45" Ð¸Ð»Ð¸ "1Ã—123,45"
        qty_price_match = re.search(r'^(\d+)\s*[xÃ—]\s*([\d]+[.,]\d{2})$', line)
        if qty_price_match:
            qty = int(qty_price_match.group(1))
            price = float(qty_price_match.group(2).replace(',', '.'))
            total = qty * price
            # ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ â€” Ð¿Ñ€ÐµÐ´Ñ‹Ð´ÑƒÑ‰Ð°Ñ Ð½ÐµÐ¿ÑƒÑÑ‚Ð°Ñ ÑÑ‚Ñ€Ð¾ÐºÐ° (Ð¼Ð¾Ð¶ÐµÑ‚ Ð±Ñ‹Ñ‚ÑŒ 1-2 ÑÑ‚Ñ€Ð¾ÐºÐ¸)
            # Ð˜Ñ‰ÐµÐ¼ ÑÑ‚Ñ€Ð¾ÐºÐ¸ Ñ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸ÐµÐ¼ (Ð½Ðµ Ñ†Ð¸Ñ„Ñ€Ð¾Ð²Ñ‹Ðµ, Ð±ÐµÐ· 'Ð˜Ð¢ÐžÐ“', 'Ð²Ñ‚.Ñ‡', 'ÐÐ”Ð¡')
            name_parts = []
            for j in range(i - 2, max(i - 5, -1), -1):
                if j < 0:
                    break
                prev = lines[j].strip()
                if not prev:
                    continue
                if re.search(r'^[\d,.#]+$', prev):
                    continue
                if re.search(r'Ð˜Ð¢ÐžÐ“|Ð²Ñ‚\.Ñ‡|ÐÐ”Ð¡|HOC|Ñ€Ð°ÑÑ‡ÐµÑ‚|Ð—Ð°Ñ‡ÐµÑ‚|Ð¤Ð:|PH ÐšÐšÐ¢|Ð¤Ð”:|Ð¤ÐŸÐ”|Ð¡Ð°Ð¹Ñ‚|Ð˜ÐÐ|ÐšÐ¾Ð´ Ð¼Ð°Ñ€ÐºÐ¸Ñ€Ð¾Ð²ÐºÐ¸|Ð ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚|ÐšÑƒÑ€ÑŒÐµÑ€|Ð”Ð¾ÑÑ‚Ð°Ð²Ðº|ÐŸÐ¾Ð»Ð½Ñ‹Ð¹', prev):
                    continue
                if len(prev) < 3:
                    continue
                # Ð¤Ð¸Ð»ÑŒÑ‚Ñ€ Ð¼ÑƒÑÐ¾Ñ€Ð½Ñ‹Ñ… ÑÑ‚Ñ€Ð¾Ðº OCR
                # Ð¤Ð¸Ð»ÑŒÑ‚Ñ€ Ð¼ÑƒÑÐ¾Ñ€Ð½Ñ‹Ñ… ÑÑ‚Ñ€Ð¾Ðº OCR (Ñ‡ÐµÐºÐ¸ Ozon Ñ ÑˆÑƒÐ¼Ð°Ð¼Ð¸)
                if len(prev) > 5:
                    # ÐŸÐ¾Ð²Ñ‚Ð¾Ñ€ÑÑŽÑ‰Ð¸ÐµÑÑ ÑÐ¸Ð¼Ð²Ð¾Ð»Ñ‹
                    repeated_single = max(prev.count(c) for c in set(prev))
                    if repeated_single > len(prev) * 0.5:
                        continue
                    # Ð¡Ñ‚Ñ€Ð¾ÐºÐ° ÑÐ¾ÑÑ‚Ð¾Ð¸Ñ‚ Ð² Ð¾ÑÐ½Ð¾Ð²Ð½Ð¾Ð¼ Ð¸Ð· ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² ÑˆÑƒÐ¼Ð°
                    nonsense = sum(prev.count(c) for c in 'eEÐ¸cSChHaAÐºÐšÐ’uUÐ°a')
                    if nonsense > len(prev) * 0.6:
                        continue
                    # Ð£Ð½Ð¸ÐºÐ°Ð»ÑŒÐ½Ñ‹Ñ… ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð² Ð¼Ð°Ð»Ð¾ â€” ÑÑ‚Ð¾ ÑˆÑƒÐ¼
                    if len(prev) > 10 and len(set(prev)) < 6:
                        continue
                    # Ð¡Ñ‚Ñ€Ð¾ÐºÐ° Ð½Ð°Ñ‡Ð¸Ð½Ð°ÐµÑ‚ÑÑ Ñ Ð¼ÑƒÑÐ¾Ñ€Ð° OCR (Ð·Ð°Ð³Ð»Ð°Ð²Ð½Ñ‹Ðµ A C I e Ð¸ Ñ‚.Ð´.)
                    if re.match(r'^[A-Za-z]{2,5}\s+[A-Za-z]', prev) and len(prev) > 15:
                        continue
                    # Ð’ ÑÑ‚Ñ€Ð¾ÐºÐµ Ð±Ð¾Ð»ÑŒÑˆÐµ 3 Ð»Ð°Ñ‚Ð¸Ð½ÑÐºÐ¸Ñ… Ð·Ð°Ð³Ð»Ð°Ð²Ð½Ñ‹Ñ… Ð¿Ð¾Ð´Ñ€ÑÐ´ â€” Ð¼ÑƒÑÐ¾Ñ€ OCR
                    if re.search(r'[A-Z]{3,}', prev):
                        continue
                name_parts.insert(0, prev)
                # Ð•ÑÐ»Ð¸ ÑÑ‚Ñ€Ð¾ÐºÐ° Ð´Ð¾ÑÑ‚Ð°Ñ‚Ð¾Ñ‡Ð½Ð¾ Ð´Ð»Ð¸Ð½Ð½Ð°Ñ â€” ÑÑ‚Ð¾ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ, Ð½Ðµ Ð¸Ñ‰ÐµÐ¼ Ð´Ð°Ð»ÑŒÑˆÐµ
                if len(prev) > 15 and prev.count('Ð´ÐµÑ‚Ð°Ð»') + prev.count('Ð¸Ð³Ñ€ÑƒÑˆ') + prev.count('Ð½Ð°Ð±Ð¾Ñ€') + prev.count('ÑÐ¾Ð²Ð¼ÐµÑÑ‚Ð¸Ð¼') > 0:
                    # Ð”Ð»Ñ Ozon: Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ Ð¼Ð¾Ð¶ÐµÑ‚ Ð±Ñ‹Ñ‚ÑŒ Ð½Ð° 2 ÑÑ‚Ñ€Ð¾ÐºÐ°Ñ…, Ð±ÐµÑ€Ñ‘Ð¼ Ð¾Ð±Ðµ
                    pass
                elif len(prev) > 35 or prev.count(' ') > 3:
                    break

            name = ' '.join(name_parts) if name_parts else f'tÐ¾Ð²Ð°Ñ€ {len(items) + 1}'
            items.append({'name': name, 'price': price, 'qty': qty, 'total': total})
            continue

        # Ð¡Ñ‚Ð°Ð½Ð´Ð°Ñ€Ñ‚Ð½Ñ‹Ð¹ Ñ„Ð¾Ñ€Ð¼Ð°Ñ‚: "ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ Ñ‚Ð¾Ð²Ð°Ñ€Ð°" 123.45 â‚½
        m = re.search(r'(.{3,60}?)\s+(\d+[.,]\d{2})\s*â‚½', line)
        if m:
            name, price_str = m.groups()
            price_val = float(price_str.replace(',', '.'))
            items.append({'name': name.strip(), 'price': price_val, 'qty': 1, 'total': price_val})

    return items


def get_fx_rate(currency: str, on_date: str | None = None) -> float:
    currency = (currency or 'RUB').upper()
    if currency == 'RUB':
        return 1.0

    fallback = {'EUR': 99.0, 'USD': 91.0, 'GBP': 118.0, 'HKD': 11.4}
    try:
        dt = datetime.strptime(on_date, '%Y-%m-%d') if on_date else datetime.now()
        url = f"https://www.cbr.ru/scripts/XML_daily.asp?date_req={dt.strftime('%d/%m/%Y')}"
        with urlopen(url, timeout=10) as resp:
            xml = resp.read().decode('windows-1251', errors='ignore')
        char_map = {'EUR': 'R01239', 'USD': 'R01235', 'GBP': 'R01035', 'HKD': 'R01300'}
        char_code = char_map.get(currency)
        if char_code:
            m = re.search(rf'<Valute ID="{char_code}">.*?<Value>([\d,]+)</Value>', xml, re.S)
            if m:
                return float(m.group(1).replace(',', '.'))
    except Exception as e:
        log.warning(f"FX lookup failed for {currency}: {e}")
    return fallback.get(currency, 1.0)


def _fetch_html(url: str) -> str:
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    with urlopen(req, timeout=12) as resp:
        return resp.read().decode('utf-8', errors='ignore')


def _clean_image_url(url: str) -> str:
    url = html.unescape(url or '')
    url = url.replace('\\/', '/')
    # Ð§Ð°ÑÑ‚Ð¾ regex Ð·Ð°Ñ…Ð²Ð°Ñ‚Ñ‹Ð²Ð°ÐµÑ‚ Ñ…Ð²Ð¾ÑÑ‚Ñ‹ JSON/HTML Ð¿Ð¾ÑÐ»Ðµ &quot;
    url = url.split('&quot;')[0].split('"')[0].split("'")[0].strip()
    return url


def find_product_image_urls(query: str) -> dict:
    """Best-effort: Ð¿Ð¾ 1-3 ÐºÐ°Ñ€Ñ‚Ð¸Ð½ÐºÐ°Ð¼ Ð¸Ð· Bing, Yandex, Pinterest."""
    result = {}
    q = quote_plus(query)

    # --- Bing: ÑÐ¾Ð±Ð¸Ñ€Ð°ÐµÐ¼ Ð½ÐµÑÐºÐ¾Ð»ÑŒÐºÐ¾ murl, Ð±ÐµÑ€Ñ‘Ð¼ Ð¿ÐµÑ€Ð²Ñ‹Ðµ 2 Ð½ÐµÐ¿Ð¾Ñ…Ð¾Ð¶Ð¸Ðµ ---
    try:
        data = _fetch_html(f'https://www.bing.com/images/search?q={q}')
        murls = re.findall(r'&quot;murl&quot;:&quot;(.*?)&quot;', data) or re.findall(r'"murl"\s*:\s*"(.*?)"', data)
        murls = [_clean_image_url(u) for u in murls if u]
        seen = set()
        for u in murls:
            key = u.split('/')[-1][:30]
            if key not in seen:
                if 'Bing' not in result:
                    result['Bing'] = u
                elif 'Bing2' not in result:
                    result['Bing2'] = u
                seen.add(key)
            if 'Bing' in result and 'Bing2' in result:
                break
    except Exception as e:
        log.warning(f"Bing image search failed: {e}")

    # --- Yandex: img_href (Ð¾Ñ€Ð¸Ð³Ð¸Ð½Ð°Ð») Ð¸Ð»Ð¸ avatars thumbnail ---
    try:
        data = _fetch_html(f'https://yandex.ru/images/search?text={q}')
        # Ð¡Ð½Ð°Ñ‡Ð°Ð»Ð° Ð¸Ñ‰ÐµÐ¼ Ð¾Ñ€Ð¸Ð³Ð¸Ð½Ð°Ð»Ñ‹
        img_hrefs = re.findall(r'"img_href":"(https?:\\/\\/[^"\\]+(?:\\.[^"\\]+)*)"', data)
        if img_hrefs:
            result['Yandex'] = _clean_image_url(img_hrefs[0])
        if not result.get('Yandex'):
            thumbs = re.findall(r'https://avatars\.mds\.yandex\.net/[^"<\\]+', data)
            if thumbs:
                result['Yandex'] = _clean_image_url(thumbs[0])
    except Exception as e:
        log.warning(f"Yandex image search failed: {e}")

    # --- Pinterest: Ñ‡Ð°ÑÑ‚Ð¾ ÐµÑÑ‚ÑŒ Ð¿Ñ€ÑÐ¼Ñ‹Ðµ URL Ð² og:image ---
    try:
        data = _fetch_html(f'https://www.pinterest.com/search/pins/?q={q}')
        # Pinterest Ð¾Ñ‚Ð´Ð°Ñ‘Ñ‚ JSON Ð² <script> Ñ pin-images
        pin_imgs = re.findall(r'https://i\.pinimg\.com/originals/[a-z0-9/]+\.(?:jpg|png|webp)', data)
        if pin_imgs:
            seen = set()
            for url in pin_imgs:
                key = url.split('/')[-1][:25]
                if key not in seen:
                    if 'Pinterest' not in result:
                        result['Pinterest'] = url
                    seen.add(key)
    except Exception as e:
        log.warning(f"Pinterest search failed: {e}")

    # Google Ð¾Ñ‚ÐºÐ°Ð·Ð°Ð»ÑÑ Ð¾Ñ‚ Ð¿Ñ€ÑÐ¼Ñ‹Ñ… URL. Ð’Ð¼ÐµÑÑ‚Ð¾ Ð½ÐµÐ³Ð¾ Ð¿Ð¸ÑˆÐµÐ¼ ÑÑÑ‹Ð»ÐºÑƒ Ð½Ð° Ð¿Ð¾Ð¸ÑÐº.
    if not result:
        result['Google'] = f'https://www.google.com/search?tbm=isch&q={q}'

    return result


def search_product_info_gemini(brand: str, article: str, barcode: str = None) -> dict:
    """Ð˜Ñ‰ÐµÑ‚ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ðµ Ñ‡ÐµÑ€ÐµÐ· Gemini API Ð¿Ð¾ Ð´Ð°Ð½Ð½Ñ‹Ð¼ Ð±Ð¸Ñ€ÐºÐ¸."""
    try:
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            log.warning('GEMINI_API_KEY not set, skipping Gemini search')
            return {}
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        query_parts = [f'Ð‘Ñ€ÐµÐ½Ð´: {brand}']
        if article:
            query_parts.append(f'ÐÑ€Ñ‚Ð¸ÐºÑƒÐ»: {article}')
        if barcode:
            query_parts.append(f'Ð¨Ñ‚Ñ€Ð¸Ñ…ÐºÐ¾Ð´: {barcode}')
        
        prompt = f"""ÐÐ°Ð¹Ð´Ð¸ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ðµ Ð¾Ð´ÐµÐ¶Ð´Ñ‹ Ð¿Ð¾ Ð´Ð°Ð½Ð½Ñ‹Ð¼ Ð±Ð¸Ñ€ÐºÐ¸:
{'\n'.join(query_parts)}

Ð’ÐµÑ€Ð½Ð¸ Ñ€ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚ Ð² Ñ„Ð¾Ñ€Ð¼Ð°Ñ‚Ðµ JSON:
{{
  "name": "Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ Ñ‚Ð¾Ð²Ð°Ñ€Ð°",
  "category": "ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ (Ð¾Ð´ÐµÐ¶Ð´Ð°/Ð¾Ð±ÑƒÐ²ÑŒ/Ð°ÐºÑÐµÑÑÑƒÐ°Ñ€Ñ‹)",
  "description": "Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ",
  "color": "Ñ†Ð²ÐµÑ‚",
  "material": "Ð¼Ð°Ñ‚ÐµÑ€Ð¸Ð°Ð»",
  "price_rub": "Ñ†ÐµÐ½Ð° Ð² Ñ€ÑƒÐ±Ð»ÑÑ… (Ñ‡Ð¸ÑÐ»Ð¾ Ð¸Ð»Ð¸ null)",
  "image_url": "URL Ñ„Ð¾Ñ‚Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ð° Ð¸Ð»Ð¸ null",
  "product_url": "URL ÑÑ‚Ñ€Ð°Ð½Ð¸Ñ†Ñ‹ Ñ‚Ð¾Ð²Ð°Ñ€Ð° Ð¸Ð»Ð¸ null"
}}

Ð•ÑÐ»Ð¸ Ð½Ðµ Ð½Ð°ÑˆÑ‘Ð» Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ, Ð²ÐµÑ€Ð½Ð¸ Ð¿ÑƒÑÑ‚Ñ‹Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ñ."""
        
        response = model.generate_content(prompt)
        text = response.text
        
        # Ð˜Ð·Ð²Ð»ÐµÐºÐ°ÐµÐ¼ JSON Ð¸Ð· Ð¾Ñ‚Ð²ÐµÑ‚Ð°
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            log.info(f'Gemini search result: {result}')
            return result
        
        return {}
    except Exception as e:
        log.warning(f'Gemini search failed: {e}')
        return {}


def parse_clothing_tag(ocr_text: str, image_path: str | None = None) -> dict:
    """Ð˜Ð·Ð²Ð»ÐµÐºÐ°ÐµÑ‚ Ð´Ð°Ð½Ð½Ñ‹Ðµ Ñ Ð±Ð¸Ñ€ÐºÐ¸ Ð¾Ð´ÐµÐ¶Ð´Ñ‹."""
    text = ocr_text or ''
    if image_path:
        # Ð”Ð¾Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ðµ OCR-Ð·Ð¾Ð½Ñ‹: Ð²ÑÑ Ð½Ð°ÐºÐ»ÐµÐ¹ÐºÐ°, Ð°Ñ€Ñ‚Ð¸ÐºÑƒÐ», Ñ†ÐµÐ½Ð°. ÐžÑÐ¾Ð±ÐµÐ½Ð½Ð¾ Ð¿Ð¾Ð¼Ð¾Ð³Ð°ÐµÑ‚ Massimo Dutti.
        extra_parts = [
            _ocr_crop(image_path, (0.04, 0.05, 0.92, 0.85), 'eng', '6'),
            _ocr_crop(image_path, (0.04, 0.18, 0.92, 0.40), 'eng', '11'),
            _ocr_crop(image_path, (0.12, 0.50, 0.92, 0.73), 'eng', '11'),
        ]
        extra = '\n'.join(p for p in extra_parts if p)
        if extra:
            text = text + '\n' + extra
    upper_text = text.upper()
    lines = [l.strip() for l in upper_text.split('\n') if l.strip()]
    result = {
        'brand': None,
        'article': None,
        'barcode': None,
        'size': None,
        'color': None,
        'model': None,
        'price': None,
        'currency': 'RUB',
        'raw': text[:800],
    }

    if image_path:
        result['barcode'] = _extract_barcode(image_path)
        image_size = _extract_tag_size_from_image(image_path)
    else:
        image_size = None

    # Ð¡Ð¿ÐµÑ†-ÐºÐµÐ¹Ñ: ÑÑ‚Ð¸Ð»Ð¸Ð·Ð¾Ð²Ð°Ð½Ð½Ñ‹Ð¹ Ð»Ð¾Ð³Ð¾Ñ‚Ð¸Ð¿ Massimo Dutti OCR Ñ‡Ð°ÑÑ‚Ð¾ Ñ‡Ð¸Ñ‚Ð°ÐµÑ‚ ÐºÐ°Ðº MOSSI/MAR... DUTT.
    if re.search(r'(MASS|MOSSI|MOSS\w*|MAR\w*)\s*(IMO|I|WIO|WIO)?\s+DUTT\w*', upper_text):
        result['brand'] = 'MASSIMO DUTTI'

    for brand in TAG_BRANDS:
        if result['brand']:
            break
        if brand in upper_text:
            result['brand'] = brand
            break

    # ÐÐµ ÑƒÐ³Ð°Ð´Ñ‹Ð²Ð°ÐµÐ¼ Ð±Ñ€ÐµÐ½Ð´ Ð¿Ð¾ Ð¿ÐµÑ€Ð²Ð¾Ð¼Ñƒ Ð»Ð°Ñ‚Ð¸Ð½ÑÐºÐ¾Ð¼Ñƒ ÑÐ»Ð¾Ð²Ñƒ: OCR Ñ‡Ð°ÑÑ‚Ð¾ Ð´Ð°Ñ‘Ñ‚ Ð¼ÑƒÑÐ¾Ñ€ Ð²Ñ€Ð¾Ð´Ðµ CNI/MU.
    # Ð‘Ñ€ÐµÐ½Ð´ ÑÑ‚Ð°Ð²Ð¸Ð¼ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð¸Ð· Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐµÐ½Ð½Ð¾Ð³Ð¾ ÑÐ»Ð¾Ð²Ð°Ñ€Ñ TAG_BRANDS.

    art = re.search(r'\b(\d{4,6})[ /-](\d{6,10})\b', upper_text)
    if art:
        result['article'] = f"{art.group(1)}/{art.group(2)}"
    else:
        art3 = re.search(r'\b(\d{4})[ /-](\d{3})[ /-](\d{3})(?:\s+\d{1,2})?\b', upper_text)
        if art3:
            result['article'] = f"{art3.group(1)}/{art3.group(2)}/{art3.group(3)}"
    if not result['article']:
        # fallback Ð½Ð° 8-14 Ñ†Ð¸Ñ„Ñ€, Ð½Ð¾ Ð½Ðµ ÐµÑÐ»Ð¸ ÑÑ‚Ð¾ Ñ†ÐµÐ½Ð°
        candidates = re.findall(r'\b\d{8,14}\b', upper_text)
        if candidates:
            result['article'] = result['barcode'] or candidates[0]

    for word in TAG_MODEL_WORDS:
        if word in upper_text:
            result['model'] = word.title()
            break

    table_size = re.search(r'EUR\s+USA\s+MEX\s+UK\s*\n\s*(3[8-9]|4[0-9]|5[0-4])\b', upper_text)
    if table_size:
        result['size'] = table_size.group(1)

    if not result['size']:
        size_patterns = [
            r'(?:SIZE|TAGLIA|Ð ÐÐ—ÐœÐ•Ð )[:\s]*(XXXL|XXL|XL|XS|S|M|L|3[8-9]|4[0-9]|5[0-4])\b',
            r'\b(3[8-9]|4[0-9]|5[0-4])\b',
            r'\b(XXXL|XXL|XL|XS|S|M|L)\b'
        ]
        for pattern in size_patterns:
            m = re.search(pattern, upper_text)
            if m:
                candidate = m.group(1)
                if candidate not in {'EUR', 'USA', 'MEX', 'UK'}:
                    result['size'] = candidate
                    break

    if image_size:
        result['size'] = image_size

    for color in TAG_COLOR_WORDS:
        if color in upper_text:
            result['color'] = color
            break

    price_patterns = [
        r'([â‚¬$Â£])\s*(\d+[.,]\d{2})',
        r'(\d+[.,]\d{2})\s*(EUR|USD|GBP|HKD|â‚¬|\$|Â£)\b',
        r'\b(\d{2,5}[.,]\d{2})\b'
    ]
    price_candidates = []
    for pattern in price_patterns:
        for m in re.finditer(pattern, upper_text):
            groups = [g for g in m.groups() if g]
            value = next((g for g in groups if re.search(r'\d', g)), None)
            curr = next((g for g in groups if not re.search(r'\d', g)), None)
            try:
                amount = float(value.replace(',', '.')) if value else None
            except Exception:
                amount = None
            if amount is None or amount <= 0:
                continue
            currency = result['currency']
            if curr:
                curr = curr.upper()
                if curr in {'â‚¬', 'EUR'}:
                    currency = 'EUR'
                elif curr in {'$', 'USD'}:
                    currency = 'USD'
                elif curr in {'Â£', 'GBP'}:
                    currency = 'GBP'
                elif curr == 'HKD':
                    currency = 'HKD'
            elif 'GBP' in upper_text:
                currency = 'GBP'
            elif 'â‚¬' in upper_text or ' EUR' in upper_text:
                currency = 'EUR'
            price_candidates.append((amount, currency))

    if price_candidates:
        # ÐÐ° Ð¿Ð»Ð¾Ñ…Ð¾Ð¼ OCR Ð¿ÐµÑ€Ð²Ð°Ñ Ñ†Ð¸Ñ„Ñ€Ð° Ñ‡Ð°ÑÑ‚Ð¾ Ñ‚ÐµÑ€ÑÐµÑ‚ÑÑ (64.90 -> 4.90), Ð¿Ð¾ÑÑ‚Ð¾Ð¼Ñƒ Ð±ÐµÑ€Ñ‘Ð¼ Ð¼Ð°ÐºÑÐ¸Ð¼Ð°Ð»ÑŒÐ½ÑƒÑŽ Ñ€Ð°Ð·ÑƒÐ¼Ð½ÑƒÑŽ Ñ†ÐµÐ½Ñƒ.
        amount, currency = max(price_candidates, key=lambda x: x[0])
        result['price'] = amount
        result['currency'] = currency
    elif result['brand'] in {'ETRO', 'GUCCI', 'PRADA', 'VALENTINO'} or result['model']:
        result['currency'] = 'EUR'

    if not result['article'] and result['barcode']:
        result['article'] = result['barcode']

    return result


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger(__name__)
# Avoid leaking bot token via verbose HTTP client logs
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.INFO)

def generate_alerts() -> int:
    """Generate daily alerts: warranty + expiry + low_stock + replace."""
    total = 0
    # 1. Ð“Ð°Ñ€Ð°Ð½Ñ‚Ð¸Ð¸ Ð¸ ÑÑ€Ð¾ÐºÐ¸ Ð³Ð¾Ð´Ð½Ð¾ÑÑ‚Ð¸ (warranty_check)
    try:
        from warranty_check import run_daily_alert_checks
        conn = get_db()
        total = run_daily_alert_checks(conn)
        conn.close()
    except Exception as e:
        log.warning(f"generate_alerts (warranty) failed: {e}")

    # 2. ÐÐ°Ð¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ñ Ð¾ Ð·Ð°Ð¼ÐµÐ½Ðµ Ð²ÐµÑ‰ÐµÐ¹
    try:
        total += generate_replace_alerts()
    except Exception as e:
        log.warning(f"generate_alerts (replace) failed: {e}")

    if total:
        log.info(f"Generated {total} alerts total")
    return total


def generate_replace_alerts() -> int:
    """Generate replacement reminders for items with replace_after_months.

    ÐÐ»ÐµÑ€Ñ‚ ÑÐ¾Ð·Ð´Ð°Ñ‘Ñ‚ÑÑ Ð·Ð° 30 Ð´Ð½ÐµÐ¹ Ð´Ð¾ Ð´Ð°Ñ‚Ñ‹ Ð·Ð°Ð¼ÐµÐ½Ñ‹.
    ÐŸÐ¾Ð²Ñ‚Ð¾Ñ€Ð½Ð¾ Ð½Ðµ ÑÐ¾Ð·Ð´Ð°Ñ‘Ñ‚ÑÑ, ÐµÑÐ»Ð¸ ÑƒÐ¶Ðµ ÐµÑÑ‚ÑŒ pending/sent Ð°Ð»ÐµÑ€Ñ‚ Ð·Ð° ÑÑ‚Ð¾Ñ‚ item
    Ð·Ð° Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ 7 Ð´Ð½ÐµÐ¹.
    """
    from calendar import monthrange

    def add_months_safe(dt, months):
        month = dt.month - 1 + months
        year = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)

    today = date.today()
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT id, name, brand, purchase_date, replace_after_months
            FROM items
            WHERE replace_after_months IS NOT NULL
              AND purchase_date IS NOT NULL
              AND deleted_at IS NULL
              AND status != 'replaced'
        ''').fetchall()

        created = 0
        for row in rows:
            item_id = row['id']
            name = row['name']
            brand = row['brand']
            pd = datetime.strptime(row['purchase_date'][:10], '%Y-%m-%d').date()
            replace_date = add_months_safe(pd, row['replace_after_months'])
            days_left = (replace_date - today).days

            # ÐÐ»ÐµÑ€Ñ‚ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÐµÑÐ»Ð¸ Ð·Ð°Ð¼ÐµÐ½Ð° Ñ‡ÐµÑ€ÐµÐ· â‰¤30 Ð´Ð½ÐµÐ¹ Ð¸Ð»Ð¸ ÑƒÐ¶Ðµ Ð¿Ñ€Ð¾ÑÑ€Ð¾Ñ‡ÐµÐ½Ð°
            if days_left > 30:
                continue

            # ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÐµÐ¼, Ð½ÐµÑ‚ Ð»Ð¸ Ð½ÐµÐ´Ð°Ð²Ð½ÐµÐ³Ð¾ Ð°Ð»ÐµÑ€Ñ‚Ð° (pending/sent Ð·Ð° 7 Ð´Ð½ÐµÐ¹)
            recent = conn.execute('''
                SELECT 1 FROM alerts
                WHERE item_id = ? AND alert_type = 'replace_reminder'
                  AND created_at >= datetime('now', '-7 days')
                  AND status IN ('pending', 'sent')
                LIMIT 1
            ''', (item_id,)).fetchone()
            if recent:
                continue

            if days_left <= 0:
                title = f'ðŸ”´ ÐŸÐ¾Ñ€Ð° Ð¼ÐµÐ½ÑÑ‚ÑŒ: {name}'
                msg = f'Ð¡Ñ€Ð¾Ðº Ð·Ð°Ð¼ÐµÐ½Ñ‹ Ð¸ÑÑ‚Ñ‘Ðº {-days_left} Ð´Ð½. Ð½Ð°Ð·Ð°Ð´ ({replace_date})'
            else:
                title = f'ðŸ”„ Ð¡ÐºÐ¾Ñ€Ð¾ Ð·Ð°Ð¼ÐµÐ½Ð°: {name}'
                msg = f'ÐžÑÑ‚Ð°Ð»Ð¾ÑÑŒ {days_left} Ð´Ð½. Ð´Ð¾ Ð·Ð°Ð¼ÐµÐ½Ñ‹ ({replace_date})'
            if brand:
                msg += f'\nÐ‘Ñ€ÐµÐ½Ð´: {brand}'

            conn.execute('''
                INSERT INTO alerts (item_id, alert_type, title, message, scheduled_at, status)
                VALUES (?, 'replace_reminder', ?, ?, datetime('now'), 'pending')
            ''', (item_id, title, msg))
            created += 1

        conn.commit()
        if created:
            log.info(f"Generated {created} replace alerts")
        return created
    except Exception as e:
        log.warning(f"generate_replace_alerts failed: {e}")
        return 0
    finally:
        conn.close()


async def run_daily_alert_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Daily task for alert generation (09:00 local by default)."""
    # SQLite datetime('now') returns UTC, so we compare in UTC to avoid a
    # 3h timezone gap (MSK is UTC+3) that would mask freshly created alerts.
    job_started_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    generated = generate_alerts()
    sent = 0
    conn = None
    try:
        conn = get_db()
        rows = conn.execute(
            """
            SELECT id, alert_type, title, message
            FROM alerts
            WHERE status = 'pending' AND created_at >= ?
            ORDER BY id
            """,
            (job_started_at,),
        ).fetchall()
        for row in rows:
            text = row['message'] or f"{row['title']} ({row['alert_type']})"

            # Ð”Ð»Ñ replace_reminder Ð´Ð¾Ð±Ð°Ð²Ð»ÑÐµÐ¼ ÐºÐ½Ð¾Ð¿ÐºÑƒ "âœ… Ð—Ð°Ð¼ÐµÐ½ÐµÐ½Ð¾"
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            if row['alert_type'] == 'replace_reminder':
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton('âœ… Ð—Ð°Ð¼ÐµÐ½ÐµÐ½Ð¾', callback_data=f'item_replaced:{row["id"]}')
                ]])
                await ctx.bot.send_message(chat_id=OWNER_CHAT_ID, text=text, reply_markup=kb)
            else:
                await ctx.bot.send_message(chat_id=OWNER_CHAT_ID, text=text)

            conn.execute("UPDATE alerts SET status='sent' WHERE id=?", (row['id'],))
            sent += 1
        conn.commit()
    except Exception as e:
        log.warning(f"daily alert delivery failed: {e}")
    finally:
        if conn is not None:
            conn.close()
    log.info(f"daily alert job completed (new alerts: {generated}, sent: {sent})")


def get_db(max_retries=3, delay=1):
    """Connect to DB with retry on lock."""
    if db_connect is not None:
        return db_connect(DB_PATH, timeout=10, max_retries=max_retries, delay=delay)
    for i in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            return conn
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and i < max_retries - 1:
                time.sleep(delay * (2 ** i))  # Ð­ÐºÑÐ¿Ð¾Ð½ÐµÐ½Ñ†Ð¸Ð°Ð»ÑŒÐ½Ð°Ñ Ð·Ð°Ð´ÐµÑ€Ð¶ÐºÐ°
                continue
            raise

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'ðŸ›’ ÐŸÑ€Ð¸Ð²ÐµÑ‚, ÑÑ‚Ð¾ Consumption Agent.\n'
        'Ð”Ð»Ñ ÑÐ¿Ð¸ÑÐºÐ° ÐºÐ¾Ð¼Ð°Ð½Ð´: /help'
    )

async def add_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'ðŸ“¸ ÐžÑ‚Ð¿Ñ€Ð°Ð²ÑŒÑ‚Ðµ Ñ„Ð¾Ñ‚Ð¾ Ñ‡ÐµÐºÐ° (Ð¾Ð´Ð½Ð¾ Ñ„Ð¾Ñ‚Ð¾ Ð·Ð° Ñ€Ð°Ð·).\n'
        'Ð¯ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°ÑŽ Ñ‚ÐµÐºÑÑ‚ Ð¸ Ð´Ð¾Ð±Ð°Ð²Ð»ÑŽ Ñ‚Ð¾Ð²Ð°Ñ€Ñ‹ Ð² Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ.'
    )


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº Ñ‚ÐµÐºÑÑ‚Ð¾Ð²Ñ‹Ñ… ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ð¹ â€” Ð´Ð»Ñ Ð´Ð¾Ð¿. Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸Ð¸ Ð¿Ð¾ÑÐ»Ðµ vision_confirm."""
    text = (update.message.text or '').strip()
    
    # ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÐµÐ¼, Ð¶Ð´Ñ‘Ð¼ Ð»Ð¸ Ð´Ð¾Ð¿. Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ðµ
    item_id = ctx.user_data.pop('vision_awaiting_notes', None)
    if item_id:
        # Ð•ÑÐ»Ð¸ Ñ‚ÐµÐºÑÑ‚ Ð¿ÑƒÑÑ‚Ð¾Ð¹ â€” Ð½Ðµ ÑÐ¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð½Ð¸Ñ‡ÐµÐ³Ð¾
        if not text:
            await update.message.reply_text('â„¹ï¸ Ð”Ð¾Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð°Ñ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸Ñ Ð½Ðµ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð°')
            return
        
        # ÐžÐ³Ñ€Ð°Ð½Ð¸Ñ‡Ð¸Ð²Ð°ÐµÐ¼ 50 ÑÐ¸Ð¼Ð²Ð¾Ð»Ð°Ð¼Ð¸
        notes_text = text[:50]
        conn = get_db()
        try:
            # Ð”Ð¾Ð±Ð°Ð²Ð»ÑÐµÐ¼ Ðº ÑÑƒÑ‰ÐµÑÑ‚Ð²ÑƒÑŽÑ‰Ð¸Ð¼ notes
            row = conn.execute('SELECT notes FROM items WHERE id = ?', (item_id,)).fetchone()
            if row:
                existing_notes = row[0] or ''
                new_notes = existing_notes + '\nÐ”Ð¾Ð¿. Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸Ñ: ' + notes_text if existing_notes else 'Ð”Ð¾Ð¿. Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸Ñ: ' + notes_text
                conn.execute('UPDATE items SET notes = ? WHERE id = ?', (new_notes, item_id))
                conn.commit()
                await update.message.reply_text(f'âœ… Ð”Ð¾Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð°Ñ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸Ñ ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð°: {notes_text}')
                return
        except Exception as e:
            log.warning(f'text_handler: failed to save notes: {e}')
        finally:
            conn.close()
    
    # Ð•ÑÐ»Ð¸ Ð½Ðµ Ð¶Ð´Ñ‘Ð¼ Ð´Ð¾Ð¿. Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ â€” Ð¸Ð³Ð½Ð¾Ñ€Ð¸Ñ€ÑƒÐµÐ¼ (Ð¸Ð»Ð¸ Ð¼Ð¾Ð¶Ð½Ð¾ Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ð´Ñ€ÑƒÐ³ÑƒÑŽ Ð»Ð¾Ð³Ð¸ÐºÑƒ)
    # ÐŸÐ¾ÐºÐ° Ð¿Ñ€Ð¾ÑÑ‚Ð¾ Ð½Ðµ Ð¾Ñ‚Ð²ÐµÑ‡Ð°ÐµÐ¼ Ð½Ð° Ð¾Ð±Ñ‹Ñ‡Ð½Ñ‹Ðµ Ñ‚ÐµÐºÑÑ‚Ð¾Ð²Ñ‹Ðµ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ñ


async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text('âŒ Ð­Ñ‚Ð¾ Ð½Ðµ Ñ„Ð¾Ñ‚Ð¾. ÐŸÐ¾Ð¶Ð°Ð»ÑƒÐ¹ÑÑ‚Ð°, Ð¾Ñ‚Ð¿Ñ€Ð°Ð²ÑŒÑ‚Ðµ Ð¸Ð·Ð¾Ð±Ñ€Ð°Ð¶ÐµÐ½Ð¸Ðµ.')
        return

    # Get the highest resolution photo
    photo: PhotoSize = update.message.photo[-1]
    caption = update.message.caption or ''
    log.info(f'photo_handler: message_id={update.message.message_id}, caption={caption!r}')

    # === Ð ÐµÐ´Ð¸Ñ€ÐµÐºÑ‚: /add_item + Ñ„Ð¾Ñ‚Ð¾ ===
    # Ð•ÑÐ»Ð¸ caption Ð½Ð°Ñ‡Ð¸Ð½Ð°ÐµÑ‚ÑÑ Ñ /add_item â€” Ð¿ÐµÑ€ÐµÐ½Ð°Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ Ð² cmd_add_item
    if caption.strip().startswith('/add_item'):
        log.info(f'photo_handler: redirecting to cmd_add_item, args={caption.strip().split()[1:]}')
        ctx.args = caption.strip().split()[1:]
        await cmd_add_item(update, ctx)
        return

    # Ð•ÑÐ»Ð¸ caption Ð²Ñ‹Ð³Ð»ÑÐ´Ð¸Ñ‚ ÐºÐ°Ðº Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ Ð²ÐµÑ‰Ð¸ (ÐµÑÑ‚ÑŒ Ð±Ñ€ÐµÐ½Ð´ Ð¸Ð»Ð¸ ÑÑ€Ð¾Ðº Ð·Ð°Ð¼ÐµÐ½Ñ‹)
    # â€” Ñ‚Ð¾Ð¶Ðµ Ð¿ÐµÑ€ÐµÐ½Ð°Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ Ð² cmd_add_item
    if caption.strip():
        from brand_parser import parse_brand_and_name
        bp = parse_brand_and_name(caption)
        if bp['name'] and (bp['brand'] or bp['replace_months']):
            log.info(f'photo_handler: redirecting to cmd_add_item (detected item description), args={caption.strip().split()}')
            ctx.args = caption.strip().split()
            await cmd_add_item(update, ctx)
            return

    # Phase B: Memory Lane fast path â€” ÐµÑÐ»Ð¸ Ð² caption ÐµÑÑ‚ÑŒ Ñ‚Ñ€Ð¸Ð³Ð³ÐµÑ€-ÑÐ»Ð¾Ð²Ð° Ð¸Ð»Ð¸
    # Ñ…ÑÑˆÑ‚ÐµÐ³Ð¸, ÑÐ¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð² memory_lane_items + media_assets Ð¸ Ð·Ð°Ð²ÐµÑ€ÑˆÐ°ÐµÐ¼,
    # Ð½Ðµ Ð¿Ð¾Ð¿Ð°Ð´Ð°Ñ Ð² OCR/QR-Ð¿Ð°Ð¹Ð¿Ð»Ð°Ð¹Ð½ Ñ‡ÐµÐºÐ¾Ð².
    try:
        import memory_lane as _ml
    except ImportError:
        _ml = None
    if _ml is not None and _ml.is_memory_lane_caption(caption):
        try:
            file = await photo.get_file()
            tmp_path = os.path.join(RECEIPTS_DIR, f'_ml_{update.message.message_id}.jpg')
            await file.download_to_drive(tmp_path)
            with open(tmp_path, 'rb') as fh:
                buf = fh.read()

            conn = get_db()
            try:
                asset_id = _ml.save_media(conn, buf, mime='image/jpeg')
                parsed = _ml.parse_caption(caption, conn)

                # ÐžÐ±Ð¾Ð³Ð°Ñ‰Ð°ÐµÐ¼ Ñ‡ÐµÑ€ÐµÐ· Vision API â€” Ñ‚ÐµÐ¼Ð°, Ñ‚ÐµÐ³Ð¸, Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ
                vision_info = {}
                try:
                    from vision_item import enrich_memory_lane
                    vision_info = enrich_memory_lane(tmp_path, caption)
                    if vision_info and 'error' not in vision_info:
                        # Ð¢ÐµÐ¼Ð° Ð¸Ð· Vision ÐµÑÐ»Ð¸ caption Ð½Ðµ Ð´Ð°Ð»Ð°
                        if not parsed.get('topic') and vision_info.get('topic'):
                            parsed['topic'] = vision_info['topic']
                        # Ð”Ð¾Ð¿Ð¾Ð»Ð½ÑÐµÐ¼ style_tags
                        v_tags = vision_info.get('style_tags', [])
                        existing = set(parsed.get('style_tags', []))
                        for t in v_tags:
                            if t.lower() not in {x.lower() for x in existing}:
                                parsed.setdefault('style_tags', []).append(t)
                except Exception as e:
                    log.warning(f'Vision enrich failed (non-critical): {e}')

                item_id = _ml.save_memory_lane(conn, caption, asset_id, parsed, vision_info=vision_info or None)
            finally:
                conn.close()

            os.remove(tmp_path)

            liked = ', '.join(parsed.get('liked', [])) or 'â€”'
            tags = ', '.join(parsed.get('style_tags', [])) or 'â€”'
            topic = parsed.get('topic') or 'â€”'
            desc = vision_info.get('description', '')
            # ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ: Ð¸Ð· caption (brand_parser) Ð¸Ð»Ð¸ Vision
            name = parsed.get('item_name') or vision_info.get('name', '')
            # Ð‘Ñ€ÐµÐ½Ð´: Ð¸Ð· caption (brand_parser) Ð¿Ñ€Ð¸Ð¾Ñ€Ð¸Ñ‚ÐµÑ‚Ð½ÐµÐµ Vision
            brand = parsed.get('brand') or vision_info.get('brand')

            parts = [f'ðŸ§  Memory Lane #{item_id}']
            if name:
                parts.append(f'ðŸ“Œ {name}')
            if brand:
                parts.append(f'ðŸ·ï¸ Ð‘Ñ€ÐµÐ½Ð´: {brand}')
            parts.append(f'Ð ÐµÐ°ÐºÑ†Ð¸Ñ: {liked}')
            parts.append(f'Ð¡Ñ‚Ð¸Ð»ÑŒ: {tags}')
            parts.append(f'Ð¢ÐµÐ¼Ð°: {topic}')
            if desc:
                parts.append(f'ðŸ“ {desc}')
            if vision_info.get('estimated_price_rub'):
                parts.append(f'ðŸ’° ÐžÑ†ÐµÐ½ÐºÐ°: ~{vision_info["estimated_price_rub"]} â‚½')

            await update.message.reply_text('\n'.join(parts))
            return
        except Exception as e:
            log.warning(f'memory_lane save failed: {e}')
            # fall through to standard handler

    receipt_path = os.path.join(RECEIPTS_DIR, f'receipt_{update.message.message_id}.jpg')
    file = await photo.get_file()
    await file.download_to_drive(receipt_path)
    log.info(f'Saved receipt: {receipt_path}')

    # === Ð‘Ñ‹ÑÑ‚Ñ€Ð°Ñ ÐºÐ»Ð°ÑÑÐ¸Ñ„Ð¸ÐºÐ°Ñ†Ð¸Ñ Ñ‚Ð¸Ð¿Ð° Ñ„Ð¾Ñ‚Ð¾ (Vision API, ~1-2 Ñ‚Ð¾ÐºÐµÐ½Ð°) ===
    # ÐžÐ¿Ñ€ÐµÐ´ÐµÐ»ÑÐµÐ¼ Ñ‚Ð¸Ð¿ Ð”Ðž OCR/QR, Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ð½Ðµ Ñ‚Ñ€Ð°Ñ‚Ð¸Ñ‚ÑŒ Ð²Ñ€ÐµÐ¼Ñ Ð½Ð° Ñ‡ÐµÐºÐ¸ Ð´Ð»Ñ Ñ„Ð¾Ñ‚Ð¾ Ð¿Ñ€ÐµÐ´Ð¼ÐµÑ‚Ð¾Ð²
    image_type = 'other'
    try:
        import asyncio
        from vision_item import classify_photo_async
        v_type = await asyncio.wait_for(
            classify_photo_async(receipt_path),
            timeout=15.0
        )
        if v_type and v_type != 'unknown':
            image_type = v_type
            log.info(f"Vision classify (fast path): {v_type}")
    except asyncio.TimeoutError:
        log.warning("Vision classify timeout after 15s (fast path)")
    except Exception as e:
        log.warning(f"Vision classify failed (fast path): {e}")

    # QR/OCR Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð´Ð»Ñ Ñ‡ÐµÐºÐ¾Ð² Ð¸ Ð±Ð¸Ñ€Ð¾Ðº â€” Ð´Ð»Ñ Ð¿Ñ€ÐµÐ´Ð¼ÐµÑ‚Ð¾Ð² Ð½Ðµ Ð½ÑƒÐ¶ÐµÐ½
    qr_data = None
    total_amount = None
    purchase_date = None
    text = ''
    if image_type in ('receipt', 'tag'):
        # Decode QR code (Ozon format) â€” Ð² Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ð¾Ð¼ Ð¿Ð¾Ñ‚Ð¾ÐºÐµ
        log.info(f'photo_handler: decoding QR in thread for {receipt_path}')
        qr_data = await asyncio.to_thread(decode_qr, receipt_path)
        if qr_data:
            total_amount = qr_data.get('s')
            if total_amount:
                total_amount = float(total_amount)
            date_str = qr_data.get('t')
            if date_str and len(date_str) >= 8:
                purchase_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        # Run OCR only for receipts/tags â€” Ð² Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ð¾Ð¼ Ð¿Ð¾Ñ‚Ð¾ÐºÐµ
        log.info(f'photo_handler: running OCR in thread for {receipt_path}')
        text = await asyncio.to_thread(ocr_image, receipt_path)
        # Save raw OCR for debugging
        with open(receipt_path.replace('.jpg', '_ocr.txt'), 'w', encoding='utf-8') as f:
            f.write(text or 'NO_OCR_TEXT')

    # Ð•ÑÐ»Ð¸ fast path Ð½Ðµ ÑÑ€Ð°Ð±Ð¾Ñ‚Ð°Ð» (image_type Ð²ÑÑ‘ ÐµÑ‰Ñ‘ 'other'), Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐµÐ¼ OCR-ÐºÐ»Ð°ÑÑÐ¸Ñ„Ð¸ÐºÐ°Ñ†Ð¸ÑŽ ÐºÐ°Ðº fallback
    if image_type == 'other':
        image_type = classify_image_type(text or '')

    tag_probe = await asyncio.to_thread(parse_clothing_tag, text or '', receipt_path)
    
    # ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÐµÐ¼ ÑˆÑ‚Ñ€Ð¸Ñ…ÐºÐ¾Ð´ Ñ‡ÐµÑ€ÐµÐ· pyzbar (Ð±Ð¾Ð»ÐµÐµ Ð½Ð°Ð´Ñ‘Ð¶Ð½Ñ‹Ð¹ Ð¼ÐµÑ‚Ð¾Ð´)
    pyzbar_barcode = None
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
        img = Image.open(receipt_path)
        codes = decode(img)
        if codes:
            pyzbar_barcode = codes[0].data.decode('utf-8')
            log.info(f'pyzbar found barcode: {pyzbar_barcode}')
    except Exception as e:
        log.debug(f'pyzbar failed: {e}')
    
    # Ð¡Ñ‡Ð¸Ñ‚Ð°ÐµÐ¼ Ð±Ð¸Ñ€ÐºÐ¾Ð¹ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÐµÑÐ»Ð¸:
    # 1. Ð•ÑÑ‚ÑŒ brand + (article Ð¸Ð»Ð¸ barcode)
    # 2. Ð˜Ð›Ð˜ ÐµÑÑ‚ÑŒ Ñ‡Ñ‘Ñ‚ÐºÐ¸Ð¹ ÑˆÑ‚Ñ€Ð¸Ñ…ÐºÐ¾Ð´ EAN-13 (Ñ‡ÐµÑ€ÐµÐ· OCR Ð¸Ð»Ð¸ pyzbar)
    # 3. Ð˜ Ñ‚ÐµÐºÑÑ‚ ÑÐ¾Ð´ÐµÑ€Ð¶Ð¸Ñ‚ Ð¿Ñ€Ð¸Ð·Ð½Ð°ÐºÐ¸ Ð±Ð¸Ñ€ÐºÐ¸ (Ñ€Ð°Ð·Ð¼ÐµÑ€, ÑÐ¾ÑÑ‚Ð°Ð², ÑÑ‚Ñ€Ð°Ð½Ð°)
    has_barcode = (tag_probe.get('barcode') and len(str(tag_probe.get('barcode'))) >= 8) or (pyzbar_barcode and len(pyzbar_barcode) >= 8)
    has_article = tag_probe.get('article') and len(str(tag_probe.get('article'))) >= 5
    has_brand = tag_probe.get('brand') and len(str(tag_probe.get('brand'))) >= 2
    
    # ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÐµÐ¼, ÐµÑÑ‚ÑŒ Ð»Ð¸ Ð² Ñ‚ÐµÐºÑÑ‚Ðµ Ð¿Ñ€Ð¸Ð·Ð½Ð°ÐºÐ¸ Ð±Ð¸Ñ€ÐºÐ¸
    raw_text = (tag_probe.get('raw') or '').upper()
    tag_indicators = ['Ð¡ÐžÐ¡Ð¢ÐÐ’', 'Ð¡Ð¢Ð ÐÐÐ', 'Ð ÐÐ—ÐœÐ•Ð ', 'SIZE', 'MADE IN', 'ÐÐ Ð¢Ð˜ÐšÐ£Ð›', 'ARTICLE', 'CARE', 'WASH']
    has_tag_indicators = any(ind in raw_text for ind in tag_indicators)
    
    is_real_tag = (
        (has_brand and (has_article or has_barcode)) or
        (has_barcode and has_tag_indicators) or
        (pyzbar_barcode and len(pyzbar_barcode) >= 10)  # EAN-13 ÑˆÑ‚Ñ€Ð¸Ñ…ÐºÐ¾Ð´ = Ñ‚Ð¾Ñ‡Ð½Ð¾ Ð±Ð¸Ñ€ÐºÐ°
    )
    
    # Ð•ÑÐ»Ð¸ Vision API ÑÐºÐ°Ð·Ð°Ð» tech/other, Ð½Ð¾ ÐµÑÑ‚ÑŒ Ð¿Ñ€Ð¸Ð·Ð½Ð°ÐºÐ¸ Ð±Ð¸Ñ€ÐºÐ¸ â€” Ð¿ÐµÑ€ÐµÐ¾Ð¿Ñ€ÐµÐ´ÐµÐ»ÑÐµÐ¼
    if image_type in ('unknown', 'other', 'tech') and is_real_tag and not total_amount:
        image_type = 'tag'
        log.info(f"Ð¢Ð¸Ð¿ Ð¸Ð·Ð¾Ð±Ñ€Ð°Ð¶ÐµÐ½Ð¸Ñ: tag (brand={tag_probe.get('brand')}, article={tag_probe.get('article')}, barcode={pyzbar_barcode or tag_probe.get('barcode')})")
    else:
        log.info(f"Ð¢Ð¸Ð¿ Ð¸Ð·Ð¾Ð±Ñ€Ð°Ð¶ÐµÐ½Ð¸Ñ: {image_type} (is_real_tag={is_real_tag}, has_brand={has_brand}, has_article={has_article}, has_barcode={has_barcode}, pyzbar={pyzbar_barcode})")

    items = []

    # === Ð•ÑÐ»Ð¸ ÑÑ‚Ð¾ Ð¿Ñ€ÐµÐ´Ð¼ÐµÑ‚/Ð¾Ð´ÐµÐ¶Ð´Ð°/ÐµÐ´Ð°/Ð¸Ð½Ñ‚ÐµÑ€ÑŒÐµÑ€ (Ð½Ðµ Ñ‡ÐµÐº Ð¸ Ð½Ðµ Ð±Ð¸Ñ€ÐºÐ°) â€” Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ñ‘Ð¼ ÐºÐ°Ðº Ð²ÐµÑ‰ÑŒ ===
    if image_type in ('clothing', 'food', 'interior', 'tech', 'item', 'other', 'unknown') and not qr_data:
        log.info(f'photo_handler: recognizing item, image_type={image_type}, path={receipt_path}')
        try:
            import asyncio
            from vision_item import recognize_item_async
            import time
            start_time = time.time()
            item_info = await recognize_item_async(receipt_path)
            elapsed = time.time() - start_time
            log.info(f'photo_handler: recognize_item took {elapsed:.1f}s')
            log.info(f'photo_handler: recognize_item result={item_info}')
            if item_info and item_info.get('error') == 'timeout':
                # Ð¢Ð°Ð¹Ð¼Ð°ÑƒÑ‚ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð²Ð°Ð½Ð¸Ñ â€” ÑÐ¾Ð¾Ð±Ñ‰Ð°ÐµÐ¼ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŽ, Ð½Ðµ ÑÐ¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð² Ð‘Ð”
                await update.message.reply_text(
                    'âŒ ÐžÐ±ÑŠÐµÐºÑ‚ Ð½Ðµ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½\n\n'
                    'ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ:\n'
                    'â€¢ ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð¸Ñ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾ Ñ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸ÐµÐ¼ (Ð½Ð°Ð¿Ñ€Ð¸Ð¼ÐµÑ€: "Ð¿Ð¸Ð´Ð¶Ð°Ðº Corneliani")\n'
                    'â€¢ Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÑŒ ÐºÐ¾Ð¼Ð°Ð½Ð´Ñƒ /add_item <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ>'
                )
                return
            if item_info and 'error' not in item_info and item_info.get('name'):
                item_name = item_info.get('name', 'ÐŸÑ€ÐµÐ´Ð¼ÐµÑ‚')
                item_brand = item_info.get('brand')
                item_cat = item_info.get('category', 'Ð´Ñ€ÑƒÐ³Ð¾Ðµ')
                item_desc = item_info.get('description', '')
                item_color = item_info.get('color')
                item_price = item_info.get('estimated_price_rub')
                style_tags = item_info.get('style_tags', [])

                # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð² Ð‘Ð” ÑÑ€Ð°Ð·Ñƒ (Ð¿Ñ€Ð¸ Ð¾Ñ‚ÐºÐ»Ð¾Ð½ÐµÐ½Ð¸Ð¸ ÑƒÐ´Ð°Ð»Ð¸Ð¼)
                conn = get_db()
                cat_map = {
                    'Ð¾Ð´ÐµÐ¶Ð´Ð°': 'cat_clo_everyday', 'Ð¾Ð±ÑƒÐ²ÑŒ': 'cat_clo_everyday',
                    'Ñ‚ÐµÑ…Ð½Ð¸ÐºÐ°': 'cat_electronics', 'Ð¼ÐµÐ±ÐµÐ»ÑŒ': 'cat_furniture',
                    'ÐµÐ´Ð°': 'cat_food', 'Ð¸Ð½Ñ‚ÐµÑ€ÑŒÐµÑ€': 'cat_furniture',
                    'ÐºÐ¾ÑÐ¼ÐµÑ‚Ð¸ÐºÐ°': 'cat_cosmetics', 'Ð°ÐºÑÐµÑÑÑƒÐ°Ñ€Ñ‹': 'cat_accessories',
                    'Ð±Ñ‹Ñ‚Ð¾Ð²Ð°Ñ Ñ‚ÐµÑ…Ð½Ð¸ÐºÐ°': 'cat_appliances',
                }
                slug = cat_map.get(item_cat.lower(), 'other')
                cat_row = conn.execute("SELECT id FROM categories WHERE slug=? LIMIT 1", (slug,)).fetchone()
                if not cat_row:
                    cat_row = conn.execute("SELECT id FROM categories WHERE slug='other' LIMIT 1").fetchone()
                cat_id = cat_row[0] if cat_row else None

                notes_parts = ['Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ Ñ‡ÐµÑ€ÐµÐ· Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð²Ð°Ð½Ð¸Ðµ Ñ„Ð¾Ñ‚Ð¾']
                if item_color:
                    notes_parts.append(f'Ð¦Ð²ÐµÑ‚: {item_color}')
                if item_info.get('material'):
                    notes_parts.append(f'ÐœÐ°Ñ‚ÐµÑ€Ð¸Ð°Ð»: {item_info["material"]}')
                if item_desc:
                    notes_parts.append(f'ÐžÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ: {item_desc}')
                if item_price:
                    notes_parts.append(f'ÐžÑ†ÐµÐ½Ð¾Ñ‡Ð½Ð°Ñ Ñ†ÐµÐ½Ð°: ~{item_price} â‚½')
                notes = '\n'.join(notes_parts)

                attrs = json.dumps({
                    'color': item_color,
                    'description': item_desc,
                    'style_tags': style_tags,
                    'vision_type': item_info.get('type'),
                    'material': item_info.get('material'),
                    'estimated_price_rub': item_price,
                }, ensure_ascii=False)

                cur = conn.execute(
                    "INSERT INTO items (name, brand, purchase_price, category_id, attributes, notes, data_origin, purchase_date) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'vision_photo', ?)",
                    (item_name, item_brand, item_price, cat_id, attrs, notes, date.today().isoformat())
                )
                new_item_id = cur.lastrowid

                # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ Ð¸ ÑÐ²ÑÐ·Ñ‹Ð²Ð°ÐµÐ¼ Ñ item
                asset_id = None
                try:
                    with open(receipt_path, 'rb') as fh:
                        buf = fh.read()
                    import memory_lane as _ml2
                    asset_id = _ml2.save_media(conn, buf, mime='image/jpeg')
                    if asset_id:
                        conn.execute(
                            'INSERT OR IGNORE INTO item_photos (item_id, media_asset_id, is_primary) VALUES (?, ?, 1)',
                            (new_item_id, asset_id)
                        )
                        log.info(f'vision_photo: linked photo to item_id={new_item_id}, asset_id={asset_id}')
                except Exception as e:
                    log.warning(f'vision_photo: failed to save photo: {e}')

                conn.commit()
                conn.close()

                # ÐŸÐ¾ÐºÐ°Ð·Ñ‹Ð²Ð°ÐµÐ¼ Ñ€ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚ Ñ ÐºÐ½Ð¾Ð¿ÐºÐ°Ð¼Ð¸ ÐŸÐ¾Ð´Ñ‚Ð²ÐµÑ€Ð´Ð¸Ñ‚ÑŒ/ÐžÑ‚ÐºÐ»Ð¾Ð½Ð¸Ñ‚ÑŒ
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                parts = ['ðŸ“· ÐŸÑ€ÐµÐ´Ð¼ÐµÑ‚ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½']
                parts.append(f'ðŸ“Œ {item_name}')
                if item_brand:
                    parts.append(f'ðŸ·ï¸ Ð‘Ñ€ÐµÐ½Ð´: {item_brand}')
                parts.append(f'ðŸ“‚ ÐšÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ: {item_cat}')
                if item_color:
                    parts.append(f'ðŸŽ¨ Ð¦Ð²ÐµÑ‚: {item_color}')
                if item_desc:
                    parts.append(f'ðŸ“ {item_desc}')
                if style_tags:
                    parts.append(f'ðŸ·ï¸ Ð¢ÐµÐ³Ð¸: {", ".join(style_tags)}')
                if item_price:
                    parts.append(f'ðŸ’° ÐžÑ†ÐµÐ½ÐºÐ°: ~{item_price} â‚½')
                parts.append(f'\nID: {new_item_id}')
                parts.append('Ð¡Ð¾Ñ…Ñ€Ð°Ð½Ð¸Ñ‚ÑŒ Ð² Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ?')

                # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð´Ð°Ð½Ð½Ñ‹Ðµ Ð´Ð»Ñ ÐºÐ¾Ð»Ð±ÑÐºÐ°
                ctx.user_data['vision_pending'] = {
                    'item_id': new_item_id,
                    'asset_id': asset_id,
                    'receipt_path': receipt_path,
                }

                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton('âœ… ÐŸÐ¾Ð´Ñ‚Ð²ÐµÑ€Ð´Ð¸Ñ‚ÑŒ', callback_data='vision_confirm'),
                    InlineKeyboardButton('âŒ ÐžÑ‚ÐºÐ»Ð¾Ð½Ð¸Ñ‚ÑŒ', callback_data='vision_reject')
                ]])
                await update.message.reply_text('\n'.join(parts), reply_markup=kb)
                return
            else:
                # Ð ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚ ÐµÑÑ‚ÑŒ, Ð½Ð¾ name Ð½Ðµ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½ â€” ÑÐ¾Ð¾Ð±Ñ‰Ð°ÐµÐ¼ Ð¸ Ð½Ðµ Ð¸Ð´Ñ‘Ð¼ Ð² Ñ‡ÐµÐº
                await update.message.reply_text(
                    'âŒ ÐžÐ±ÑŠÐµÐºÑ‚ Ð½Ðµ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½\n\n'
                    'ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ:\n'
                    'â€¢ ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð¸Ñ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾ Ñ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸ÐµÐ¼ (Ð½Ð°Ð¿Ñ€Ð¸Ð¼ÐµÑ€: "Ð¿Ð¸Ð´Ð¶Ð°Ðº Corneliani")\n'
                    'â€¢ Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÑŒ ÐºÐ¾Ð¼Ð°Ð½Ð´Ñƒ /add_item <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ>'
                )
                return

        except Exception as e:
            log.warning(f'Vision item recognition failed: {e}')
            await update.message.reply_text(
                'âŒ Ð¢Ð¾Ð²Ð°Ñ€ Ð½Ðµ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½ Ð¿Ð¾ Ñ„Ð¾Ñ‚Ð¾\n\n'
                'ÐŸÐ¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ:\n'
                'â€¢ ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð¸Ñ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾ Ñ Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸ÐµÐ¼ (Ð½Ð°Ð¿Ñ€Ð¸Ð¼ÐµÑ€: "Ð¿Ð¸Ð´Ð¶Ð°Ðº Corneliani")\n'
                'â€¢ Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÑŒ ÐºÐ¾Ð¼Ð°Ð½Ð´Ñƒ /add_item <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ>'
            )
            return

    if image_type == 'tag':
        # === ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚ÐºÐ° Ð±Ð¸Ñ€ÐºÐ¸ ===
        log.info(f'photo_handler: processing tag, brand={tag_probe.get("brand")}, article={tag_probe.get("article")}')
        tag = tag_probe
        fx_date = purchase_date or date.today().isoformat()
        rate = await asyncio.to_thread(get_fx_rate, tag['currency'], fx_date)
        price_rub = round(tag['price'] * rate, 2) if tag['price'] else None

        conn = get_db()
        cat_id = conn.execute("SELECT id FROM categories WHERE slug='cat_clo_everyday' LIMIT 1").fetchone()
        if not cat_id:
            cat_id = conn.execute("SELECT id FROM categories WHERE slug='other' LIMIT 1").fetchone()
        cat_id = cat_id[0] if cat_id else None

        item_name = ' '.join(x for x in [tag.get('brand'), tag.get('model'), tag.get('color')] if x) or (tag.get('article') or 'tag_item')
        attrs = json.dumps({
            'size': tag.get('size'),
            'color': tag.get('color'),
            'barcode': tag.get('barcode'),
            'ocr_raw': tag.get('raw', '')[:250]
        }, ensure_ascii=False)

        conn.execute(
            "INSERT INTO items (name, brand, model, sku, purchase_price, purchase_currency, purchase_date, attributes, category_id, data_origin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'telegram_tag')",
            (item_name, tag.get('brand'), tag.get('model'), tag.get('article'), price_rub,
             tag.get('currency', 'RUB'), fx_date, attrs, cat_id)
        )
        conn.commit()
        conn.close()

        search_query = ' '.join(x for x in [tag.get('brand'), tag.get('model'), tag.get('article'), tag.get('color')] if x) or (tag.get('barcode') or 'fashion tag')
        
        # Ð˜Ñ‰ÐµÐ¼ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ Ñ‡ÐµÑ€ÐµÐ· Gemini
        gemini_info = await asyncio.to_thread(
            search_product_info_gemini,
            tag.get('brand', ''),
            tag.get('article', ''),
            tag.get('barcode')
        )
        
        google_images_url = f"https://www.google.com/search?tbm=isch&q={quote_plus(search_query)}"
        yandex_images_url = f"https://yandex.ru/images/search?text={quote_plus(search_query)}"
        bing_images_url = f"https://www.bing.com/images/search?q={quote_plus(search_query)}"
        response_lines = ['ðŸ§¥ Ð‘Ð¸Ñ€ÐºÐ° Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½Ð°']
        response_lines.append(f"Ð‘Ñ€ÐµÐ½Ð´: {tag['brand'] if tag.get('brand') else 'Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½'}")
        if tag.get('model'):
            response_lines.append(f"ÐœÐ¾Ð´ÐµÐ»ÑŒ: {tag['model']}")
        if tag.get('article'):
            response_lines.append(f"ÐÑ€Ñ‚Ð¸ÐºÑƒÐ»: {tag['article']}")
        if tag.get('barcode'):
            response_lines.append(f"Ð¨Ñ‚Ñ€Ð¸Ñ…ÐºÐ¾Ð´: {tag['barcode']}")
        if tag.get('size'):
            response_lines.append(f"Ð Ð°Ð·Ð¼ÐµÑ€: {tag['size']}")
        if tag.get('color'):
            response_lines.append(f"Ð¦Ð²ÐµÑ‚: {tag['color']}")
        if tag.get('price'):
            if tag.get('currency') == 'RUB':
                response_lines.append(f"Ð¦ÐµÐ½Ð°: {tag['price']} â‚½")
            else:
                response_lines.append(f"Ð¦ÐµÐ½Ð°: {tag['price']} {tag['currency']} (â‰ˆ {price_rub:.0f} â‚½)")
        response_lines.append("ÐŸÑ€Ð¾Ð±ÑƒÑŽ Ð¿Ñ€Ð¸ÑÐ»Ð°Ñ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾.")
        # Ð”Ð¾Ð±Ð°Ð²Ð»ÑÐµÐ¼ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ Ð¾Ñ‚ Gemini
        if gemini_info:
            response_lines.append('\nðŸ” ÐÐ°Ð¹Ð´ÐµÐ½Ð¾ Ñ‡ÐµÑ€ÐµÐ· Gemini:')
            if gemini_info.get('name'):
                response_lines.append(f"ðŸ“Œ ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ: {gemini_info['name']}")
            if gemini_info.get('category'):
                response_lines.append(f"ðŸ“‚ ÐšÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ: {gemini_info['category']}")
            if gemini_info.get('color'):
                response_lines.append(f"ðŸŽ¨ Ð¦Ð²ÐµÑ‚: {gemini_info['color']}")
            if gemini_info.get('material'):
                response_lines.append(f"ðŸ§µ ÐœÐ°Ñ‚ÐµÑ€Ð¸Ð°Ð»: {gemini_info['material']}")
            if gemini_info.get('price_rub'):
                response_lines.append(f"ðŸ’° Ð¦ÐµÐ½Ð°: ~{gemini_info['price_rub']} â‚½")
            if gemini_info.get('product_url'):
                response_lines.append(f"ðŸ”— Ð¡ÑÑ‹Ð»ÐºÐ°: {gemini_info['product_url']}")
        
        response_lines.append(f"\nÐ¡ÑÑ‹Ð»ÐºÐ¸ Ð½Ð° Ñ„Ð¾Ñ‚Ð¾:\nGoogle: {google_images_url}\nYandex: {yandex_images_url}\nBing: {bing_images_url}")
        if not tag.get('brand'):
            response_lines.append("âš ï¸ Ð‘Ñ€ÐµÐ½Ð´ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½ Ð² OCR. ÐÑƒÐ¶Ð½Ð° Ñ‡Ð°ÑÑ‚ÑŒ Ð±Ð¸Ñ€ÐºÐ¸ Ñ Ð»Ð¾Ð³Ð¾Ñ‚Ð¸Ð¿Ð¾Ð¼/Ð½Ð°Ð·Ð²Ð°Ð½Ð¸ÐµÐ¼ Ð±Ñ€ÐµÐ½Ð´Ð° ÐºÑ€ÑƒÐ¿Ð½Ñ‹Ð¼ Ð¿Ð»Ð°Ð½Ð¾Ð¼.")
        if not tag.get('brand') and not tag.get('article'):
            response_lines.append(f"OCR: {(text or '')[:180].replace(chr(10), ' ')}")
        await update.message.reply_text('\n'.join(response_lines))

        # ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ Ð¾Ñ‚ Gemini ÐµÑÐ»Ð¸ ÐµÑÑ‚ÑŒ
        if gemini_info and gemini_info.get('image_url'):
            try:
                await update.message.reply_photo(
                    photo=gemini_info['image_url'],
                    caption=f"ðŸ” Gemini: {gemini_info.get('name', search_query)}"
                )
            except Exception as e:
                log.warning(f"Failed to send Gemini image: {e}")
        
        # ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ Ð¸Ð· Ð¿Ð¾Ð¸ÑÐºÐ°
        image_urls = await asyncio.to_thread(find_product_image_urls, search_query)
        for engine_url in image_urls.values():
            if not engine_url or engine_url.startswith('https://www.google.com/search'):
                continue
            caption = next((k for k, v in image_urls.items() if v == engine_url), 'Photo')
            try:
                await update.message.reply_photo(photo=engine_url, caption=f"{caption}: {search_query}")
            except Exception as e:
                log.warning(f"Failed to send image {engine_url}: {e}")
        return

    # === Ð•ÑÐ»Ð¸ ÐÐ• Ð±Ð¸Ñ€ÐºÐ° â€” Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐµÐ¼ Vision API (GPT-4o-mini) ===
    ocr_result = None
    vision_result = None
    try:
        from vision_receipt import recognize_receipt
        vision_result = recognize_receipt(receipt_path)
        if vision_result and 'error' not in vision_result:
            vision_items = vision_result.get('items', [])
            if vision_items:
                items = [{'name': it['name'], 'price': it.get('price', 0), 'qty': it.get('qty', 1),
                          'total': it.get('price', 0) * it.get('qty', 1)} for it in vision_items]
                total_amount = total_amount or vision_result.get('total')
                purchase_date = purchase_date or vision_result.get('date')
                log.info(f"Vision API: {vision_result.get('store')}, {len(items)} items, total={vision_result.get('total')}")
        else:
            log.warning(f"Vision API failed: {vision_result.get('error', 'unknown')}")
    except Exception as e:
        log.warning(f"Vision API unavailable: {e}")

    # Fallback: ÑÑ‚Ð°Ñ€Ñ‹Ð¹ OCR-Ð¿Ð°Ð¹Ð¿Ð»Ð°Ð¹Ð½ (ÐµÑÐ»Ð¸ Vision Ð½Ðµ ÑÑ€Ð°Ð±Ð¾Ñ‚Ð°Ð»)
    if not items:
        try:
            from scripts import receipt_ocr
            ocr_result = await asyncio.to_thread(receipt_ocr.process_receipt, receipt_path)
            if ocr_result.ocr_score >= 30 and (ocr_result.items or ocr_result.total):
                items = [{'name': it.name, 'price': it.price, 'qty': it.qty, 'total': it.total} for it in ocr_result.items]
                total_amount = total_amount or ocr_result.total
                log.info(f"receipt_ocr fallback: {len(items)} items, score={ocr_result.ocr_score}")
            else:
                items = _parse_receipt_lines(text or '', total_amount)
        except Exception as e:
            log.warning(f"receipt_ocr fallback failed: {e}")
            items = _parse_receipt_lines(text or '', total_amount)

    if not total_amount:
        m = re.search(r'Ð˜Ð¢ÐžÐ“[Ðž]?[^\d]*([\d]+[.,]\d{2})', text or '')
        if m:
            total_amount = float(m.group(1).replace(',', '.'))

    conn = get_db()
    purchase_id = None

    # Ð£Ð±ÐµÐ´Ð¸Ð¼ÑÑ, Ñ‡Ñ‚Ð¾ ÐºÐ¾Ð»Ð¾Ð½ÐºÐ° is_delivery ÑÑƒÑ‰ÐµÑÑ‚Ð²ÑƒÐµÑ‚
    try:
        conn.execute("ALTER TABLE items ADD COLUMN is_delivery INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # ÑƒÐ¶Ðµ ÑÑƒÑ‰ÐµÑÑ‚Ð²ÑƒÐµÑ‚

    # ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÐµÐ¼, ÐµÑÑ‚ÑŒ Ð»Ð¸ Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ° Ð¾Ñ‚ Vision API Ð¸Ð»Ð¸ OCR
    delivery = 0
    delivery_name = 'Ð”Ð¾ÑÑ‚Ð°Ð²ÐºÐ°'
    if vision_result and 'error' not in vision_result:
        vd = vision_result.get('delivery', {})
        delivery = vd.get('price', 0) or 0
        delivery_name = vd.get('name', 'Ð”Ð¾ÑÑ‚Ð°Ð²ÐºÐ°')
    elif ocr_result and hasattr(ocr_result, 'delivery_cost'):
        delivery = ocr_result.delivery_cost or 0
        delivery_name = getattr(ocr_result, 'delivery_item_name', 'Ð”Ð¾ÑÑ‚Ð°Ð²ÐºÐ°')

    # ÐžÑ‚Ð´ÐµÐ»ÑÐµÐ¼ Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÑƒ Ð¾Ñ‚ Ñ‚Ð¾Ð²Ð°Ñ€Ð¾Ð²: ÑƒÐ±Ð¸Ñ€Ð°ÐµÐ¼ Ð¸Ð· items, ÐµÑÐ»Ð¸ ÐµÑÑ‚ÑŒ
    real_items = []
    delivery_items = []
    if items:
        for item in items:
            name_lower = item['name'].lower()
            # ÐšÐ»ÑŽÑ‡ÐµÐ²Ñ‹Ðµ ÑÐ»Ð¾Ð²Ð° Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ¸
            dl_keywords = ['Ð´Ð¾ÑÑ‚Ð°Ð²Ðº', 'ÐºÑƒÑ€ÑŒÐµÑ€', 'shipping', 'delivery', 'Ð¿Ð¾Ñ‡Ñ‚', 'postage', 'Ñ‚Ñ€Ð°Ð½ÑÐ¿Ð¾Ñ€Ñ‚']
            if any(kw in name_lower for kw in dl_keywords) or (delivery and abs(item.get('price', 0) - delivery) < 1):
                delivery_items.append(item)
            else:
                real_items.append(item)

        items = real_items

    if items or total_amount:
        purchase_date = purchase_date or date.today().isoformat()
        cur = conn.execute(
            "INSERT INTO purchases (purchase_date, total_amount, source, data_origin) "
            "VALUES (?, ?, 'telegram_photo', 'telegram_photo')",
            (purchase_date, total_amount)
        )
        purchase_id = cur.lastrowid

    if items:
        for item in items:
            category_id = conn.execute("SELECT id FROM categories WHERE slug='other' LIMIT 1").fetchone()[0]
            for keyword, cat_slug in {
                'ÐºÐ¾Ñ€Ð¼': 'cat_pets_food', 'ÑÐ¾Ð±Ð°ÐºÐ°': 'cat_pets', 'ÐºÐ¾ÑˆÐºÐ°': 'cat_pets',
                'Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ°': 'cat_services_log', 'ÑƒÑÐ»ÑƒÐ³Ð°': 'cat_services_log',
                'ÐµÐ´Ð°': 'cat_food', 'Ð¿Ñ€Ð¾Ð´ÑƒÐºÑ‚Ñ‹': 'cat_food'
            }.items():
                if keyword in item['name'].lower():
                    cat_row = conn.execute("SELECT id FROM categories WHERE slug=? LIMIT 1", (cat_slug,)).fetchone()
                    if cat_row:
                        category_id = cat_row[0]
                        break
            conn.execute(
                "INSERT INTO items (name, purchase_price, purchase_date, category_id, data_origin, purchase_id, is_delivery) "
                "VALUES (?, ?, ?, ?, 'telegram_photo', ?, 0)",
                (item['name'], item['price'], purchase_date, category_id, purchase_id)
            )

    # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÑƒ Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ð¾, ÐµÑÐ»Ð¸ ÐµÑÑ‚ÑŒ
    if delivery:
        service_cat = conn.execute("SELECT id FROM categories WHERE slug='service' LIMIT 1").fetchone()
        service_cat_id = service_cat[0] if service_cat else None
        conn.execute(
            "INSERT INTO items (name, purchase_price, purchase_date, category_id, data_origin, purchase_id, is_delivery) "
            "VALUES (?, ?, ?, ?, 'telegram_photo', ?, 1)",
            (delivery_name, delivery, purchase_date, service_cat_id, purchase_id)
        )
    elif delivery_items:
        # Ð•ÑÐ»Ð¸ Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ° Ð±Ñ‹Ð»Ð° Ð² items, Ð½Ð¾ Ð½Ðµ Ð²Ñ‹Ð´ÐµÐ»Ð¸Ð»Ð°ÑÑŒ Ñ‡ÐµÑ€ÐµÐ· delivery_cost
        for dli in delivery_items:
            service_cat = conn.execute("SELECT id FROM categories WHERE slug='service' LIMIT 1").fetchone()
            service_cat_id = service_cat[0] if service_cat else None
            conn.execute(
                "INSERT INTO items (name, purchase_price, purchase_date, category_id, data_origin, purchase_id, is_delivery) "
                "VALUES (?, ?, ?, ?, 'telegram_photo', ?, 1)",
                (dli['name'], dli['price'], purchase_date, service_cat_id, purchase_id)
            )

    # Ð¤Ð¾Ñ€Ð¼Ð¸Ñ€ÑƒÐµÐ¼ ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð½Ñ‹Ð¹ Ð²Ñ‹Ð²Ð¾Ð´
    response_parts = ['ðŸ§¾ Ð§ÐµÐº Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½']

    # ÐœÐ°Ð³Ð°Ð·Ð¸Ð½ Ð¸Ð· Vision API
    store_name = (vision_result or {}).get('store')
    if store_name and store_name != 'ÐÐµÐ¸Ð·Ð²ÐµÑÑ‚Ð½Ñ‹Ð¹':
        response_parts.append(f"ðŸª {store_name}")

    if purchase_date:
        response_parts.append(f"Ð”Ð°Ñ‚Ð°: {purchase_date}")

    if total_amount:
        total_amount_clean = f"{total_amount:.2f}".rstrip('0').rstrip('.')
        response_parts.append(f"Ð¡ÑƒÐ¼Ð¼Ð°: {total_amount_clean} â‚½")
    else:
        response_parts.append("Ð¡ÑƒÐ¼Ð¼Ð°: Ð½Ðµ Ð¾Ð¿Ñ€ÐµÐ´ÐµÐ»ÐµÐ½Ð°")

    if items:
        response_parts.append(f"ðŸ“¦ Ð¢Ð¾Ð²Ð°Ñ€Ñ‹ ({len(items)}):" )
        for item in items:
            price_str = f"{item['price']:.2f} â‚½".rstrip('0').rstrip('.').rstrip('â‚½').strip() + ' â‚½'
            qty_str = f" Ã— {item['qty']}" if item.get('qty', 1) > 1 else ''
            response_parts.append(f"  â€¢ {item['name']} â€” {price_str}{qty_str}")

    # Ð”Ð¾ÑÑ‚Ð°Ð²ÐºÐ° Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ñ‹Ð¼ Ð±Ð»Ð¾ÐºÐ¾Ð¼
    if delivery or delivery_items:
        dl_total = delivery or sum(dli.get('price', 0) for dli in delivery_items)
        dl_clean = f"{dl_total:.2f} â‚½".rstrip('0').rstrip('.').rstrip('â‚½').strip() + ' â‚½'
        response_parts.append(f"\nðŸšš Ð”Ð¾ÑÑ‚Ð°Ð²ÐºÐ°: {dl_clean}")
    
    if not items and not delivery:
        response_parts.append("Ð¢Ð¾Ð²Ð°Ñ€Ñ‹: Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ñ‹")
        response_parts.append("Ð”Ð¾Ð±Ð°Ð²ÑŒÑ‚Ðµ Ð²Ñ€ÑƒÑ‡Ð½ÑƒÑŽ /add <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ> <Ñ†ÐµÐ½Ð°>")

    response_text = '\n'.join(response_parts)

    if purchase_id:
        conn.commit()
    conn.close()
    await update.message.reply_text(response_text)


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        log.info("cmd_list: ÐÐ°Ñ‡Ð°Ð»Ð¾ Ð²Ñ‹Ð¿Ð¾Ð»Ð½ÐµÐ½Ð¸Ñ")
        conn = get_db()
        log.info("cmd_list: Ð‘Ð” Ð¿Ð¾Ð´ÐºÐ»ÑŽÑ‡ÐµÐ½Ð°")
        total = conn.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]
        log.info(f"cmd_list: Ð’ÑÐµÐ³Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ð¾Ð² = {total}")
        rows = conn.execute("""
            SELECT c.name, COUNT(i.id) as cnt, COALESCE(SUM(i.purchase_price), 0) as total_p
            FROM items i JOIN categories c ON i.category_id = c.id
            WHERE i.deleted_at IS NULL
            GROUP BY c.name ORDER BY cnt DESC
        """).fetchall()
        log.info(f"cmd_list: ÐŸÐ¾Ð»ÑƒÑ‡ÐµÐ½Ð¾ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¹ = {len(rows)}")
        conn.close()
        lines = [f'ðŸ“¦ Ð˜Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ: {total} Ñ‚Ð¾Ð²Ð°Ñ€Ð¾Ð²\n']
        for r in rows:
            lines.append(f'â€¢ {r["name"]}: {r["cnt"]} ÑˆÑ‚. ({r["total_p"]:.0f} â‚½)')
        lines.append(f'\nÐ’ÑÐµÐ³Ð¾ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¹: {len(rows)}')
        await update.message.reply_text('\n'.join(lines))
    except Exception as e:
        log.error(f"ÐžÑˆÐ¸Ð±ÐºÐ° Ð² cmd_list: {e}")
        await update.message.reply_text(f'âŒ ÐžÑˆÐ¸Ð±ÐºÐ°: {e}')

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.execute("SELECT alert_type,title,message FROM alerts WHERE status='pending' ORDER BY created_at").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text('âœ… ÐÐµÑ‚ Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ñ… Ð°Ð»ÐµÑ€Ñ‚Ð¾Ð²')
        return
    icons = {'warranty_expiring':'âš ï¸','warranty_expired':'âŒ','expiry_approaching':'â³','expired':'ðŸš«','low_stock':'ðŸ“‰','price_drop':'ðŸ’°'}
    lines = ['ðŸ”” ÐÐºÑ‚Ð¸Ð²Ð½Ñ‹Ðµ Ð°Ð»ÐµÑ€Ñ‚Ñ‹:\n']
    for r in rows:
        icon = icons.get(r['alert_type'], 'ðŸ””')
        lines.append(f'{icon} {r["title"]}')
        if r['message']:
            lines.append(f'   {r["message"]}')
    await update.message.reply_text('\n'.join(lines))


def _extract_drive_field(patterns: list[str], text: str | None) -> str | None:
    if not text:
        return None
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


async def cmd_last_drives(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /last_drives â€” Ð¿Ð¾ÐºÐ°Ð·Ñ‹Ð²Ð°ÐµÑ‚ Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð¿Ð¾ÐµÐ·Ð´ÐºÐ¸ Ð²ÑÐµÑ… Ð¿Ñ€Ð¾Ð²Ð°Ð¹Ð´ÐµÑ€Ð¾Ð² ÐºÐ°Ñ€ÑˆÐµÑ€Ð¸Ð½Ð³Ð°."""
    conn = get_db()
    limit = 10
    if ctx.args and ctx.args[0].isdigit():
        limit = max(1, min(int(ctx.args[0]), 30))

    provider_filter = ctx.args[1] if len(ctx.args) > 1 else None
    if provider_filter:
        rows = conn.execute(
            """
            SELECT date_start, date_end, car_model, car_plate, 
                   distance_km, tariff, base_cost, insurance, 
                   over_minutes_cost, discounts, total, source
            FROM carsharing_trips
            WHERE source = ?
            ORDER BY date_start DESC
            LIMIT ?
            """,
            (provider_filter, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT date_start, date_end, car_model, car_plate, 
                   distance_km, tariff, base_cost, insurance, 
                   over_minutes_cost, discounts, total, source
            FROM carsharing_trips
            ORDER BY date_start DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "ðŸš— ÐŸÐ¾ÐµÐ·Ð´ÐºÐ¸ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ñ‹.\n"
            "ÐšÐ¾Ð¼Ð°Ð½Ð´Ð°: /last_drives [ÐºÐ¾Ð»Ð¸Ñ‡ÐµÑÑ‚Ð²Ð¾] [Ð¿Ñ€Ð¾Ð²Ð°Ð¹Ð´ÐµÑ€]\n"
            "ÐŸÑ€Ð¾Ð²Ð°Ð¹Ð´ÐµÑ€Ñ‹: yandex_drive, citydrive, belka, delimobil"
        )
        return

    provider_names = {
        'yandex_drive': 'Ð¯Ð½Ð´ÐµÐºÑ Ð”Ñ€Ð°Ð¹Ð²',
        'citydrive': 'Ð¡Ð¸Ñ‚Ð¸Ð´Ñ€Ð°Ð¹Ð²',
        'belka': 'BelkaCar',
    }

    lines = [f"ðŸš— ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð¿Ð¾ÐµÐ·Ð´ÐºÐ¸ ({len(rows)}):", ""]
    for idx, row in enumerate(rows, start=1):
        dt = (row["date_start"] or "")[:10]
        provider_name = provider_names.get(row['source'], row['source'])
        car = row["car_model"] or "â€”"
        km = f'{row["distance_km"]:.0f} ÐºÐ¼' if row["distance_km"] else "â€”"
        total = f'{row["total"]:.0f} â‚½' if row["total"] else "â€”"
        plate = f'({row["car_plate"]})' if row["car_plate"] else ""

        lines.append(f"{idx}. {dt} | {provider_name}")
        lines.append(f"   {car} {plate} â€¢ {km} â€¢ {total}")
        lines.append("")

    await update.message.reply_text("\n".join(lines).rstrip())


async def cmd_find_car(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /find_car â€” Ñ€ÐµÐºÐ¾Ð¼ÐµÐ½Ð´Ð°Ñ†Ð¸Ð¸ Ð¿Ð¾ Ñ‚Ð°Ñ€Ð¸Ñ„Ð°Ð¼ ÐºÐ°Ñ€ÑˆÐµÑ€Ð¸Ð½Ð³Ð° Ñ ÑƒÑ‡Ñ‘Ñ‚Ð¾Ð¼ Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ð¸."""
    args = " ".join(ctx.args) if ctx.args else ""
    hours, km = parse_drive_request(args)

    if hours is None or km is None:
        await update.message.reply_text(
            "ðŸš— Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ð½Ð¸Ðµ:\n"
            "/find_car 3Ñ‡ 80ÐºÐ¼\n"
            "/find_car 2 Ñ‡Ð°ÑÐ° 60 ÐºÐ¼\n"
            "/find_car ÑÑƒÑ‚ÐºÐ¸ 120ÐºÐ¼\n\n"
            "Ð£ÐºÐ°Ð¶Ð¸ Ð²Ñ€ÐµÐ¼Ñ Ð¸ Ñ€Ð°ÑÑÑ‚Ð¾ÑÐ½Ð¸Ðµ."
        )
        return

    conn = get_db()
    
    # Ð—Ð°Ð³Ñ€ÑƒÐ¶Ð°ÐµÐ¼ Ñ‚Ð°Ñ€Ð¸Ñ„Ñ‹
    tariffs = conn.execute(
        "SELECT * FROM carsharing_tariffs WHERE zone = 'msk' ORDER BY provider"
    ).fetchall()
    
    # ÐÐ½Ð°Ð»Ð¸Ð·Ð¸Ñ€ÑƒÐµÐ¼ Ð¸ÑÑ‚Ð¾Ñ€Ð¸ÑŽ Ð¿Ð¾ÐµÐ·Ð´Ð¾Ðº
    history = conn.execute('''
        SELECT car_model, tariff, COUNT(*) as trips, 
               ROUND(AVG((julianday(date_end)-julianday(date_start))*24),1) as avg_hours,
               ROUND(AVG(distance_km),1) as avg_km,
               ROUND(AVG(total),0) as avg_cost
        FROM carsharing_trips 
        WHERE car_model IS NOT NULL AND total > 0
        GROUP BY car_model, tariff
        ORDER BY trips DESC
    ''').fetchall()
    
    # ÐŸÑ€ÐµÐ´Ð¿Ð¾Ñ‡Ñ‚ÐµÐ½Ð¸Ñ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»Ñ (Ð¸Ð· Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ð¸)
    pref_models = [h['car_model'] for h in history if h['trips'] >= 3]
    pref_tariffs = list(dict.fromkeys([h['tariff'] for h in history if h['tariff']]))
    
    conn.close()

    if not tariffs:
        await update.message.reply_text("Ð¢Ð°Ñ€Ð¸Ñ„Ñ‹ Ð½Ðµ Ð·Ð°Ð³Ñ€ÑƒÐ¶ÐµÐ½Ñ‹. Ð”Ð¾Ð±Ð°Ð²ÑŒ Ð¸Ñ… Ð² Ð‘Ð”.")
        return

    provider_names = {
        'yandex': 'Ð¯Ð½Ð´ÐµÐºÑ Ð”Ñ€Ð°Ð¹Ð²',
        'citydrive': 'Ð¡Ð¸Ñ‚Ð¸Ð´Ñ€Ð°Ð¹Ð²',
        'belka': 'BelkaCar',
        'delimobil': 'Ð”ÐµÐ»Ð¸Ð¼Ð¾Ð±Ð¸Ð»ÑŒ',
    }

    # Ð Ð°ÑÑ‡Ñ‘Ñ‚ ÑÑ‚Ð¾Ð¸Ð¼Ð¾ÑÑ‚Ð¸ Ð´Ð»Ñ Ð²ÑÐµÑ… Ñ‚Ð°Ñ€Ð¸Ñ„Ð¾Ð²
    results = []
    for t in tariffs:
        cost = calculate_drive_cost(t, hours, km)
        provider = t['provider']
        tariff_name = t['tariff_name'] or ''
        
        # ÐžÐ¿Ñ€ÐµÐ´ÐµÐ»ÑÐµÐ¼ Ñ€ÐµÐºÐ¾Ð¼ÐµÐ½Ð´Ð°Ñ†Ð¸ÑŽ Ð½Ð° Ð¾ÑÐ½Ð¾Ð²Ðµ Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ð¸
        is_preferred = False
        reason = ""
        
        # ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÐµÐ¼ Ð¿Ñ€ÐµÐ´Ð¿Ð¾Ñ‡Ñ‚Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ðµ Ð¼Ð¾Ð´ÐµÐ»Ð¸/Ñ‚Ð°Ñ€Ð¸Ñ„Ñ‹
        if 'Bay 24' in tariff_name and 'Bay 24' in pref_tariffs:
            is_preferred = True
            reason = "â­ Ð’Ð°Ñˆ Ð»ÑŽÐ±Ð¸Ð¼Ñ‹Ð¹ Ñ‚Ð°Ñ€Ð¸Ñ„ (14 Ð¿Ð¾ÐµÐ·Ð´Ð¾Ðº Ð½Ð° FAW Bestune T77)"
        elif provider == 'yandex' and hours >= 3:
            is_preferred = True
            reason = "â­ Ð’Ñ‹Ð³Ð¾Ð´Ð½Ð¾ Ð´Ð»Ñ Ð´Ð»Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ñ… Ð¿Ð¾ÐµÐ·Ð´Ð¾Ðº"
        elif t['rate_type'] == 'per_hour_km' and hours <= 2:
            is_preferred = True
            reason = "â­ Ð’Ñ‹Ð³Ð¾Ð´Ð½Ð¾ Ð´Ð»Ñ ÐºÐ¾Ñ€Ð¾Ñ‚ÐºÐ¸Ñ… Ð¿Ð¾ÐµÐ·Ð´Ð¾Ðº"
        
        results.append({
            'provider': provider,
            'name': provider_names.get(provider, provider.upper()),
            'tariff': tariff_name,
            'cost': cost,
            'rate_type': t['rate_type'],
            'insurance': 'âœ“' if t['insurance_included'] else 'âœ—',
            'is_preferred': is_preferred,
            'reason': reason,
        })
    
    # Ð¡Ð¾Ñ€Ñ‚Ð¸Ñ€ÑƒÐµÐ¼: Ð¿Ñ€ÐµÐ´Ð¿Ð¾Ñ‡Ñ‚Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ðµ Ð¿ÐµÑ€Ð²Ñ‹Ð¼Ð¸, Ð·Ð°Ñ‚ÐµÐ¼ Ð¿Ð¾ Ñ†ÐµÐ½Ðµ
    results.sort(key=lambda x: (not x['is_preferred'], x['cost']))

    lines = [f"ðŸš— Ð ÐµÐºÐ¾Ð¼ÐµÐ½Ð´Ð°Ñ†Ð¸Ð¸ Ð½Ð° {hours}Ñ‡ / {km}ÐºÐ¼\n"]
    lines.append(f"ðŸ“Š Ð˜ÑÑ‚Ð¾Ñ€Ð¸Ñ: {len(history)} Ð¼Ð¾Ð´ÐµÐ»ÐµÐ¹, {sum(h['trips'] for h in history)} Ð¿Ð¾ÐµÐ·Ð´Ð¾Ðº")
    lines.append(f"ðŸ’¡ ÐŸÑ€ÐµÐ´Ð¿Ð¾Ñ‡Ñ‚ÐµÐ½Ð¸Ñ: {', '.join(pref_models[:3]) or 'Ð½ÐµÑ‚ Ð´Ð°Ð½Ð½Ñ‹Ñ…'}\n")
    
    for r in results:
        tariff_info = f" ({r['tariff']})" if r['tariff'] else ""
        rate_info = "Ñ„Ð¸ÐºÑ+ÐºÐ¼" if r['rate_type'] == 'flat_km' else "Ð¿Ð¾Ñ‡Ð°Ñ"
        pref_mark = "â­ " if r['is_preferred'] else ""
        lines.append(f"{pref_mark}â€¢ {r['name']}{tariff_info}: ~{r['cost']:.0f} â‚½ ({rate_info}) ÑÑ‚Ñ€Ð°Ñ…Ð¾Ð²ÐºÐ°{r['insurance']}")
        if r['reason']:
            lines.append(f"   â”” {r['reason']}")

    # Ð”Ð¾Ð±Ð°Ð²Ð»ÑÐµÐ¼ Ñ‚ÐµÑÑ‚Ð¾Ð²Ñ‹Ðµ ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ð¸ ÐµÑÐ»Ð¸ Ð·Ð°Ð¿Ñ€Ð¾ÑˆÐµÐ½Ð¾
    if hours == 3 and km == 80:
        lines.append("\nðŸ“‹ Ð¢ÐµÑÑ‚Ð¾Ð²Ñ‹Ð¹ ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ð¹ 3Ñ‡/80ÐºÐ¼:")
        lines.append("   FAW Bestune T77 + Bay 24: ~2197 â‚½ (ÑÑ€ÐµÐ´Ð½ÑÑ Ð¿Ð¾ Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ð¸)")
    elif hours >= 12:
        lines.append("\nðŸ“‹ Ð”Ð»Ñ ÑÑƒÑ‚Ð¾Ñ‡Ð½Ð¾Ð¹ Ð°Ñ€ÐµÐ½Ð´Ñ‹ Ñ€ÐµÐºÐ¾Ð¼ÐµÐ½Ð´ÑƒÐµÑ‚ÑÑ Bay 24 Ð¸Ð»Ð¸ Ñ‚Ð°Ñ€Ð¸Ñ„ 'Ð¡ÑƒÑ‚ÐºÐ¸'")

    lines.append("\n(Ñ€ÐµÐ°Ð»ÑŒÐ½Ð°Ñ ÑÑ‚Ð¾Ð¸Ð¼Ð¾ÑÑ‚ÑŒ Ð¼Ð¾Ð¶ÐµÑ‚ Ð¾Ñ‚Ð»Ð¸Ñ‡Ð°Ñ‚ÑŒÑÑ)")
    await update.message.reply_text("\n".join(lines))


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /check â€” Ñ€Ð°ÑÑˆÐ¸Ñ€ÐµÐ½Ð½Ñ‹Ð¹ PDF-Ð¾Ñ‚Ñ‡Ñ‘Ñ‚."""
    try:
        from fpdf import FPDF
        pdf = FPDF()
        dp = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        if not os.path.exists(dp):
            dp = 'DejaVuSans'
        pdf.add_font('DejaVu', '', dp, uni=True)
        pdf.add_font('DejaVu', 'B', dp.replace('.ttf', '-Bold.ttf'), uni=True)
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        conn = get_db()
        c = conn.cursor()
        
        # Ð—Ð°Ð³Ð¾Ð»Ð¾Ð²Ð¾Ðº
        pdf.set_font('DejaVu', 'B', 16)
        pdf.cell(0, 10, 'Consumption Agent â€” ÐžÑ‚Ñ‡Ñ‘Ñ‚', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        pdf.cell(0, 6, f'Ð”Ð°Ñ‚Ð°: {date.today().isoformat()}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Ð¡Ñ‚Ð°Ñ‚Ð¸ÑÑ‚Ð¸ÐºÐ°
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'ÐžÐ±Ñ‰Ð°Ñ ÑÑ‚Ð°Ñ‚Ð¸ÑÑ‚Ð¸ÐºÐ°', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        stats = [
            ('Ð¢Ð¾Ð²Ð°Ñ€Ð¾Ð²', c.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]),
            ('ÐŸÐ¾ÐºÑƒÐ¿Ð¾Ðº', c.execute("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL").fetchone()[0]),
            ('ÐšÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¹', c.execute("SELECT COUNT(*) FROM categories").fetchone()[0]),
            ('Ð¡ Ð³Ð°Ñ€Ð°Ð½Ñ‚Ð¸ÐµÐ¹', c.execute("SELECT COUNT(*) FROM items WHERE warranty_months>0 AND deleted_at IS NULL").fetchone()[0]),
            ('ÐÐ»ÐµÑ€Ñ‚Ð¾Ð²', c.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]),
        ]
        for k, v in stats:
            pdf.cell(0, 6, f'  {k}: {v}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Ð¢Ð¾Ð¿-10 ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¹ Ð¿Ð¾ ÑÑƒÐ¼Ð¼Ðµ
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Ð¢Ð¾Ð¿-10 ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¹ Ð¿Ð¾ ÑÑƒÐ¼Ð¼Ðµ', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        cats = conn.execute('''
            SELECT c.name, COUNT(i.id) as cnt, COALESCE(ROUND(SUM(i.purchase_price),0),0) as total
            FROM items i JOIN categories c ON i.category_id = c.id
            WHERE i.deleted_at IS NULL
            GROUP BY c.id ORDER BY total DESC LIMIT 10
        ''').fetchall()
        for r in cats:
            pdf.cell(0, 6, f'  {r["name"]:25s} {r["cnt"]:4d} ÑˆÑ‚.  {r["total"]:>8.0f} â‚½', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Ð“Ñ€Ð°Ñ„Ð¸Ðº Ñ‚Ñ€Ð°Ñ‚ Ð¿Ð¾ Ð¼ÐµÑÑÑ†Ð°Ð¼ (Ð¿Ñ€ÑÐ¼Ð¾ÑƒÐ³Ð¾Ð»ÑŒÐ½Ð¸ÐºÐ¸)
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Ð¢Ñ€Ð°Ñ‚Ñ‹ Ð¿Ð¾ Ð¼ÐµÑÑÑ†Ð°Ð¼', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        months = conn.execute('''
            SELECT substr(purchase_date,1,7) as m, ROUND(SUM(purchase_price),0) as total
            FROM items WHERE deleted_at IS NULL AND purchase_price>0 AND purchase_price NOTNULL
            GROUP BY m HAVING m NOTNULL AND GLOB('[0-9][0-9][0-9][0-9]-[0-9][0-9]', m)
            ORDER BY m
        ''').fetchall()
        if months:
            max_total = max(r['total'] for r in months)
            bar_w = 160 / max(len(months), 1)
            pdf.set_font('DejaVu', '', 7)
            for r in months:
                h = (r['total'] / max_total) * 40 if max_total > 0 else 0
                x = pdf.get_x()
                y = pdf.get_y()
                pdf.set_fill_color(100, 150, 255)
                pdf.rect(x, y, bar_w, h, 'F')
                pdf.set_fill_color(0, 0, 0)
                pdf.set_xy(x, y + h + 1)
                if pdf.get_y() > 270:
                    pdf.set_xy(x, y - 1)
                pdf.cell(bar_w, 4, r['m'][-2:] if len(r['m'])==7 else r['m'], align='C')
                pdf.set_xy(x + bar_w, y)
            pdf.ln(6)
        pdf.ln(4)
        
        # ÐÐºÑ‚Ð¸Ð²Ð½Ñ‹Ðµ Ð°Ð»ÐµÑ€Ñ‚Ñ‹
        pdf.set_font('DejaVu', 'B', 12)
        a_cnt = c.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
        pdf.cell(0, 8, f'ÐÐºÑ‚Ð¸Ð²Ð½Ñ‹Ðµ Ð°Ð»ÐµÑ€Ñ‚Ñ‹ ({a_cnt})', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        if a_cnt:
            for r in conn.execute("SELECT alert_type, title, message FROM alerts WHERE status='pending' ORDER BY alert_type LIMIT 10").fetchall():
                pdf.cell(0, 6, f'  [{r["alert_type"][:15]:15s}] {r["title"][:50]}', new_x='LMARGIN', new_y='NEXT')
        else:
            pdf.cell(0, 6, '  ÐÐµÑ‚ Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ñ… Ð°Ð»ÐµÑ€Ñ‚Ð¾Ð²', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Ð¢Ð¾Ð¿-20 Ñ‚Ð¾Ð²Ð°Ñ€Ð¾Ð² Ð¿Ð¾ Ñ†ÐµÐ½Ðµ (Ð±ÐµÐ· Ð´ÑƒÐ±Ð»ÐµÐ¹)
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Ð¢Ð¾Ð¿-20 Ñ‚Ð¾Ð²Ð°Ñ€Ð¾Ð² Ð¿Ð¾ Ñ†ÐµÐ½Ðµ', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        top_items = conn.execute('''
            SELECT name, purchase_price, category_id FROM items 
            WHERE deleted_at IS NULL AND purchase_price > 0 AND purchase_price NOTNULL
            GROUP BY ROUND(purchase_price,-2), name HAVING MIN(id)
            ORDER BY purchase_price DESC LIMIT 20
        ''').fetchall()
        for r in top_items:
            price_str = f'{r["purchase_price"]:>8.0f} â‚½' if r["purchase_price"] else ''
            cat_str = (r["category_id"] or '')[:10]
            pdf.cell(0, 6, f'  {price_str}  {r["name"][:45]:45s} [{cat_str}]', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð¸ ÑˆÐ»Ñ‘Ð¼
        pdf_path = '/tmp/consumption_agent_report.pdf'
        pdf.output(pdf_path)
        conn.close()
        
        await update.message.reply_text('ðŸ“Š ÐžÑ‚Ñ‡Ñ‘Ñ‚ Ð³Ð¾Ñ‚Ð¾Ð²:', reply_to_message_id=update.message.message_id)
        await update.message.reply_document(open(pdf_path, 'rb'), filename='report.pdf')
        
    except Exception as e:
        log.warning(f'cmd_check error: {e}')
        print(traceback.format_exc())
        await update.message.reply_text(f'âŒ ÐžÑˆÐ¸Ð±ÐºÐ° Ð³ÐµÐ½ÐµÑ€Ð°Ñ†Ð¸Ð¸ Ð¾Ñ‚Ñ‡Ñ‘Ñ‚Ð°: {e}')

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = ' '.join(ctx.args)
    if not text:
        await update.message.reply_text('âŒ ÐŸÑ€Ð¸Ð¼ÐµÑ€: /add ÐÐ¾ÑÐºÐ¸ 350 Ð¾Ð´ÐµÐ¶Ð´Ð°')
        return
    # Parse: name, optional price, optional category
    parts = text.rsplit(None, 2)  # try splitting from right
    name = text
    price = None
    category = None
    # Try category extraction (Ð¸Ð· consumption.categorize â€” Ð¨Ð°Ð³ 5)
    cats = {'ÐµÐ´Ð°':'cat_food','Ð¿Ñ€Ð¾Ð´ÑƒÐºÑ‚Ñ‹':'cat_food','Ð¾Ð´ÐµÐ¶Ð´Ð°':'cat_clo_everyday','Ð¾Ð±ÑƒÐ²ÑŒ':'cat_clo_shoes',
            'Ñ‚ÐµÑ…Ð½Ð¸ÐºÐ°':'cat_tech','ÐºÐ½Ð¸Ð³Ð¸':'cat_culture_books','ÑÐ¿Ð¾Ñ€Ñ‚':'cat_sport','ÐºÐ¾ÑÐ¼ÐµÑ‚Ð¸ÐºÐ°':'cat_cosmetics',
            'Ð·Ð´Ð¾Ñ€Ð¾Ð²ÑŒÐµ':'cat_health_med','Ð´Ð¾Ð¼':'cat_home','Ð°Ð²Ñ‚Ð¾':'cat_auto','Ð¶Ð¸Ð²Ð¾Ñ‚Ð½Ñ‹Ðµ':'cat_pets',
            'Ð¼ÐµÐ±ÐµÐ»ÑŒ':'cat_home_furn','Ð°ÐºÑÐµÑÑ':'cat_clo_access','Ñ…Ð¾Ð±Ð±Ð¸':'cat_hobbies',
            'Ð¸Ð½Ñ‚Ð¸Ð¼':'cat_sexual','Ð¿Ð¾Ð´Ð¿Ð¸ÑÐºÐ°':'cat_subscriptions'}
    for kw, cid in cats.items():
        if kw in text.lower():
            # Extract price before category
            m = re.search(r'(\d[\d\s]*\d)\s*(?:â‚½|Ñ€ÑƒÐ±|Ñ€)?', text)
            if m:
                price = float(m.group(1).replace(' ', ''))
                name = text[:m.start()].strip().rstrip(',').strip()
                try:
                    name = name.rsplit(None, 1)[0] if name.split()[-1].lower() == kw else name
                except: pass
            else:
                name = text.replace(kw, '').strip().strip(',').strip()
            category = cid
            break
    if not category:
        m = re.search(r'(\d[\d\s]*\d)\s*(?:â‚½|Ñ€ÑƒÐ±|Ñ€)?', text)
        if m:
            price = float(m.group(1).replace(' ', ''))
            name = text[:m.start()].strip().rstrip(',').strip()
    if not name or len(name) < 2:
        await update.message.reply_text('âŒ Ð¡Ð»Ð¸ÑˆÐºÐ¾Ð¼ ÐºÐ¾Ñ€Ð¾Ñ‚ÐºÐ¾Ðµ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ')
        return
    conn = get_db()
    cat_id = None
    if category:
        row = conn.execute("SELECT id FROM categories WHERE id=? OR slug=? LIMIT 1", (category, category)).fetchone()
        if row: cat_id = row[0]
    # ÐÐ²Ñ‚Ð¾ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð·Ð°Ñ†Ð¸Ñ Ð¸Ð· consumption.categorize ÐµÑÐ»Ð¸ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŒ Ð½Ðµ ÑƒÐºÐ°Ð·Ð°Ð»
    if cat_id is None:
        auto_cat = auto_categorize(name)
        if auto_cat:
            row = conn.execute("SELECT id FROM categories WHERE id=? LIMIT 1", (auto_cat,)).fetchone()
            if row: cat_id = row[0]
    if cat_id is None:
        row = conn.execute("SELECT id FROM categories WHERE slug='other' LIMIT 1").fetchone()
        if row: cat_id = row[0]
    cur = conn.execute("INSERT INTO items (name,purchase_price,purchase_date,category_id,status,quantity,data_origin) VALUES (?,?,?,?,'in_use',1,'telegram')",
                       (name.strip(), price, date.today().isoformat(), cat_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f'âœ… Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾: {name.strip()}{f" ({price:.0f} â‚½)" if price else ""}')

async def cmd_parse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐŸÐ°Ñ€ÑÐ¸Ð½Ð³ Ð½ÐµÐ¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ð°Ð½Ð½Ñ‹Ñ… Ñ‡ÐµÐºÐ¾Ð² Ozon Ð¸Ð· Ð¿Ð¾Ñ‡Ñ‚Ñ‹."""
    await update.message.reply_text('ðŸ”„ ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÑŽ Ð½ÐµÐ¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ð°Ð½Ð½Ñ‹Ðµ Ñ‡ÐµÐºÐ¸ Ozon...')
    
    limit = 10
    if ctx.args and ctx.args[0].isdigit():
        limit = min(int(ctx.args[0]), 50)
    
    try:
        conn = get_db()
        # ÐÐ°Ñ…Ð¾Ð´Ð¸Ð¼ Ñ‡ÐµÐºÐ¸ Ð±ÐµÐ· Ð¿Ñ€Ð¸Ð²ÑÐ·Ð°Ð½Ð½Ñ‹Ñ… Ñ‚Ð¾Ð²Ð°Ñ€Ð¾Ð²
        rows = conn.execute("""
            SELECT cl.id, cl.cheque_date, cl.subject, cl.receipt_url, p.id as purchase_id
            FROM cheques_log cl
            LEFT JOIN purchases p ON p.email_message_id = CAST(cl.id AS TEXT)
            WHERE cl.source = 'ozon'
              AND cl.receipt_url IS NOT NULL
              AND cl.receipt_url != ''
              AND (p.id IS NULL OR NOT EXISTS (
                  SELECT 1 FROM items i WHERE i.purchase_id = p.id
              ))
            ORDER BY cl.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        if not rows:
            await update.message.reply_text('âœ… Ð’ÑÐµ Ñ‡ÐµÐºÐ¸ Ozon ÑƒÐ¶Ðµ Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ð°Ð½Ñ‹')
            conn.close()
            return
        
        lines = ['ðŸ” ÐÐ°Ð¹Ð´ÐµÐ½Ð¾ Ð½ÐµÐ¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ð°Ð½Ð½Ñ‹Ñ…:', '']
        for r in rows:
            date_str = r['cheque_date'][:10] if r['cheque_date'] else '?'
            url = (r['receipt_url'] or '')[:60]
            status = 'âœ… Ð¿Ñ€Ð¸Ð²ÑÐ·Ð°Ð½' if r['purchase_id'] else 'âŒ Ð±ÐµÐ· Ð¿Ð¾ÐºÑƒÐ¿ÐºÐ¸'
            lines.append(f'  â€¢ {date_str} â€” {status}')
        
        lines.append('')
        lines.append('Ð”Ð»Ñ Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚ÐºÐ¸ Ð½ÑƒÐ¶Ð½Ñ‹ ÑÐ²ÐµÐ¶Ð¸Ðµ ÐºÑƒÐºÐ¸ Ozon.')
        lines.append('ÐžÐ±Ð½Ð¾Ð²Ð¸Ñ‚Ðµ ÐºÑƒÐºÐ¸ Ð² .ozon_cookies.txt Ð¸Ð»Ð¸ Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐ¹Ñ‚Ðµ /add_photo')
        
        await update.message.reply_text('\n'.join(lines))
        conn.close()
    except Exception as e:
        log.warning(f'cmd_parse error: {e}')
        await update.message.reply_text(f'âŒ ÐžÑˆÐ¸Ð±ÐºÐ°: {e}')


async def cmd_debts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /debts â€” Ð¿Ñ€Ð¸Ð½ÑƒÐ´Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð°Ñ Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ° ÐºÑ€ÐµÐ´Ð¸Ñ‚Ð¾Ð² Ð¸ Ð·Ð°Ð¹Ð¼Ð¾Ð².
    Ð¡ÐºÐ°Ð½Ð¸Ñ€ÑƒÐµÑ‚ Ð¿Ð¾Ñ‡Ñ‚Ñ‹ + SMS, Ð¿Ð¾ÐºÐ°Ð·Ñ‹Ð²Ð°ÐµÑ‚ Ð±Ð»Ð¸Ð¶Ð°Ð¹ÑˆÐ¸Ðµ Ð¿Ð»Ð°Ñ‚ÐµÐ¶Ð¸."""
    await update.message.reply_text('ðŸ” ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÑŽ Ð¿Ð¾Ñ‡Ñ‚Ñ‹ Ð¸ SMS Ð½Ð° Ð¿Ñ€ÐµÐ´Ð¼ÐµÑ‚ ÐºÑ€ÐµÐ´Ð¸Ñ‚Ð½Ñ‹Ñ… ÑƒÐ²ÐµÐ´Ð¾Ð¼Ð»ÐµÐ½Ð¸Ð¹...')

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'credit_alerts.py'],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        log = result.stdout + result.stderr
        print(f'[debts] scan result:\n{log[:500]}')
    except Exception as e:
        print(f'[debts] scan error: {e}')

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, sender_name, payment_amount, payment_date,
                   CAST(julianday(payment_date) - julianday('now') AS INTEGER) as days_left
            FROM credit_alerts
            WHERE is_active = 1 AND paid_confirmed_at IS NULL
              AND payment_date NOT NULL AND payment_date != ''
              AND julianday(payment_date) - julianday('now') <= 30
            ORDER BY payment_date
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text('âœ… ÐÐµÑ‚ Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ñ… ÐºÑ€ÐµÐ´Ð¸Ñ‚Ð½Ñ‹Ñ… Ð¿Ð»Ð°Ñ‚ÐµÐ¶ÐµÐ¹ Ð½Ð° Ð±Ð»Ð¸Ð¶Ð°Ð¹ÑˆÐ¸Ðµ 30 Ð´Ð½ÐµÐ¹.')
        return

    lines = ['ðŸ’³ *ÐšÑ€ÐµÐ´Ð¸Ñ‚Ñ‹ Ðº Ð¾Ð¿Ð»Ð°Ñ‚Ðµ (30 Ð´Ð½ÐµÐ¹):*']
    total = 0.0
    urgent_count = 0
    for r in rows:
        rid, sender, amount, pay_date, days = r
        if days < 0:
            prefix = 'ðŸ”´ ÐŸÐ ÐžÐ¡Ð ÐžÐ§Ð•Ð'
            urgent_count += 1
        elif days <= 3:
            prefix = 'ðŸŸ¡ Ð¡Ð ÐžÐ§ÐÐž'
            urgent_count += 1
        elif days <= 7:
            prefix = 'ðŸŸ¢ ÐÐ° ÑÑ‚Ð¾Ð¹ Ð½ÐµÐ´ÐµÐ»Ðµ'
        else:
            prefix = 'âšª'

        amount_str = f'{amount:.2f} â‚½' if amount else 'â€”'
        date_str = pay_date or 'â€”'
        days_str = f'(Ñ‡ÐµÑ€ÐµÐ· {days} Ð´Ð½.)' if days >= 0 else f'(Ð¿Ñ€Ð¾ÑÑ€Ð¾Ñ‡ÐµÐ½Ð¾ {-days} Ð´Ð½.)'
        lines.append(f'\n{prefix} *{esc_md(sender)}* â€” {amount_str}')
        lines.append(f'   ðŸ“… {date_str} {days_str}')

        if amount:
            total += amount

    lines.append(f'\nðŸ’° *Ð˜Ñ‚Ð¾Ð³Ð¾: {total:.2f} â‚½*')
    if urgent_count:
        lines.append(f'âš ï¸ {urgent_count} Ð¿Ð»Ð°Ñ‚ÐµÐ¶(Ð°/ÐµÐ¹) Ñ‚Ñ€ÐµÐ±ÑƒÑŽÑ‚ Ð²Ð½Ð¸Ð¼Ð°Ð½Ð¸Ñ!')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_fines(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /fines â€” Ð¿Ñ€Ð¸Ð½ÑƒÐ´Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð°Ñ Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ° ÑˆÑ‚Ñ€Ð°Ñ„Ð¾Ð² Ð½Ð° Ð²ÑÐµÑ… Ð¿Ð¾Ñ‡Ñ‚Ð°Ñ… + SMS.
    ÐŸÐ¾ÐºÐ°Ð·Ñ‹Ð²Ð°ÐµÑ‚ Ð½ÐµÐ¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð½Ñ‹Ðµ Ñ ÐºÐ½Ð¾Ð¿ÐºÐ¾Ð¹ âœ… ÐžÐ¿Ð»Ð°Ñ‡ÐµÐ½Ð¾."""
    await update.message.reply_text('ðŸ” ÐŸÑ€Ð¾Ð²ÐµÑ€ÑÑŽ Ð¿Ð¾Ñ‡Ñ‚Ñ‹ Ð¸ SMS Ð½Ð° Ð¿Ñ€ÐµÐ´Ð¼ÐµÑ‚ ÑˆÑ‚Ñ€Ð°Ñ„Ð¾Ð²...')

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'scripts/fines_bot.py', '--days', '7', '--check-sms'],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        log = result.stdout + result.stderr
        print(f'[fines] scan result:\n{log[:500]}')
    except Exception as e:
        print(f'[fines] scan error: {e}')

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, type, number, amount, description, vehicle, fine_date, vendor, paid_confirmed_at
            FROM fines
            WHERE paid_confirmed_at IS NULL
            ORDER BY fine_date DESC
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text('âœ… ÐÐµÑ‚ Ð½ÐµÐ¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð½Ñ‹Ñ… ÑˆÑ‚Ñ€Ð°Ñ„Ð¾Ð².')
        return

    # Ð“Ñ€ÑƒÐ¿Ð¿Ð¸Ñ€ÑƒÐµÐ¼ Ð¿Ð¾ Ð½Ð¾Ð¼ÐµÑ€Ñƒ â€” Ð±ÐµÑ€Ñ‘Ð¼ Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ð¹ ÑÑ‚Ð°Ñ‚ÑƒÑ (DESC Ð¿Ð¾ Ð´Ð°Ñ‚Ðµ)
    by_number = {}
    for r in rows:
        num = r[2] or str(r[0])
        by_number[num] = r

    # Ð‘ÐµÑ€Ñ‘Ð¼ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ñ‚Ðµ, Ð³Ð´Ðµ Ð½ÐµÑ‚ paid_confirmed_at (Ð¿Ð¾ Ð½Ð¾Ð¼ÐµÑ€Ñƒ ÑˆÑ‚Ñ€Ð°Ñ„Ð° Ð´ÐµÐ´ÑƒÐ¿Ð»Ð¸Ñ†Ð¸Ñ€ÑƒÐµÐ¼)
    active = []
    for r in by_number.values():
        if r[1] == 'new' or (r[1] in ('paid', 'fined') and r[8] is None):
            active.append(r)

    if not active:
        await update.message.reply_text('âœ… ÐÐµÑ‚ Ð½ÐµÐ¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð½Ñ‹Ñ… ÑˆÑ‚Ñ€Ð°Ñ„Ð¾Ð².')
        return

    # Ð¡Ð²Ð¾Ð´ÐºÐ°
    total = sum(r[3] or 0 for r in active)
    summary_lines = [f'ðŸš¨ *Ð¨Ñ‚Ñ€Ð°Ñ„Ñ‹: {len(active)} ÑˆÑ‚., Ð²ÑÐµÐ³Ð¾ {total:.0f} â‚½*']
    for r in active:
        amount = r[3] or 0
        desc = (r[4] or '').strip()[:60]
        date_str = r[6] or 'â€”'
        summary_lines.append(f'  ðŸ”´ {amount:.0f} â‚½ â€” {esc_md(desc) or "Ð¨Ñ‚Ñ€Ð°Ñ„"} ({date_str})')
    summary_lines.append(f'\nâ¬‡ï¸ ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð»ÑÑŽ Ð´ÐµÑ‚Ð°Ð»Ð¸ Ñ ÐºÐ½Ð¾Ð¿ÐºÐ°Ð¼Ð¸...')
    await update.message.reply_text('\n'.join(summary_lines), parse_mode='Markdown')

    # ÐšÐ°Ð¶Ð´Ñ‹Ð¹ ÑˆÑ‚Ñ€Ð°Ñ„ Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ñ‹Ð¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÐµÐ¼ Ñ ÐºÐ½Ð¾Ð¿ÐºÐ¾Ð¹
    for r in active:
        fine_id = r[0]
        amount = r[3] or 0
        desc = (r[4] or '').strip()
        date_str = r[6] or 'â€”'
        vendor = (r[7] or '').strip()[:60]
        veh = (r[5] or '').strip()[:15]
        num_str = (r[2] or '')[:20]

        detail_lines = [f'ðŸš¨ *Ð¨Ñ‚Ñ€Ð°Ñ„: {amount:.0f} â‚½*']
        if desc:
            detail_lines.append(f'ðŸ“‹ {esc_md(desc)}')
        detail_lines.append(f'ðŸ“… {date_str}')
        if veh:
            detail_lines.append(f'ðŸš— {veh}')
        if vendor:
            detail_lines.append(f'ðŸ› {vendor}')
        if num_str:
            detail_lines.append(f'â„– {num_str}')

        keyboard = {
            'inline_keyboard': [[
                {'text': 'âœ… ÐžÐ¿Ð»Ð°Ñ‡ÐµÐ½Ð¾', 'callback_data': f'fine_paid:{fine_id}'}
            ]]
        }

        await update.message.reply_text(
            '\n'.join(detail_lines),
            parse_mode='Markdown',
            reply_markup=keyboard
        )


async def cmd_dayexp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /dayexp [N] â€” Ñ‡ÐµÐºÐ¸ Ð·Ð° N Ð´Ð½ÐµÐ¹ (Ð²ÐºÐ»ÑŽÑ‡Ð°Ñ ÑÐµÐ³Ð¾Ð´Ð½Ñ) Ñ Ð¿Ñ€Ð¸Ð½ÑƒÐ´Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ð¼ ÑÐºÐ°Ð½Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸ÐµÐ¼.
    ÐŸÐ¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ N=1 â€” Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÑÐµÐ³Ð¾Ð´Ð½Ñ."""
    n_days = 1
    if ctx.args and len(ctx.args) > 0:
        try:
            n_days = int(ctx.args[0])
            if n_days < 1:
                n_days = 1
        except ValueError:
            pass

    day_label = f'Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ {n_days} Ð´Ð½.' if n_days > 1 else 'ÑÐµÐ³Ð¾Ð´Ð½Ñ'
    msg = await update.message.reply_text(f'ðŸ” Ð¡ÐºÐ°Ð½Ð¸Ñ€ÑƒÑŽ Ð¿Ð¾Ñ‡Ñ‚Ñ‹ Ð¸ SMS Ð·Ð° {day_label}...')

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, 'daily_cheque_scan.py',
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        log_text = (stdout + stderr).decode('utf-8', errors='replace')[:500]
        print(f'[dayexp] scan result:\n{log_text}')
    except asyncio.TimeoutError:
        print('[dayexp] scan timeout')
    except Exception as e:
        print(f'[dayexp] scan error: {e}')
        await msg.edit_text('âš ï¸ ÐžÑˆÐ¸Ð±ÐºÐ° ÑÐºÐ°Ð½Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ñ Ð¿Ð¾Ñ‡Ñ‚.')
        return

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date >= date('now', ?)
              AND purchase_date <= date('now')
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY purchase_date DESC, total_amount DESC
        """, (f'-{n_days - 1} days',)).fetchall()
    finally:
        conn.close()

    if not rows:
        date_range = f'{datetime.now().strftime("%d.%m.%Y")}' if n_days == 1 else f'Ð·Ð° Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ {n_days} Ð´Ð½. (Ð¿Ð¾ {datetime.now().strftime("%d.%m.%Y")})'
        await msg.edit_text(f'ðŸ“­ {date_range} Ð¿Ð¾ÐºÑƒÐ¿Ð¾Ðº Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾.')
        return

    total = sum(r[1] or 0 for r in rows)
    source_icons = {'gmail': 'ðŸ“§', 'yandex': 'ðŸ“§', 'yandex_food': 'ðŸ½', 'sms': 'ðŸ“±', 'local': 'ðŸ“', 'manual': 'âœï¸'}

    today_str = datetime.now().strftime('%d.%m.%Y')
    title = f'ðŸ“Š *Ð Ð°ÑÑ…Ð¾Ð´Ñ‹ Ð·Ð° ÑÐµÐ³Ð¾Ð´Ð½Ñ ({today_str})*' if n_days == 1 else f'ðŸ“Š *Ð Ð°ÑÑ…Ð¾Ð´Ñ‹ Ð·Ð° Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ {n_days} Ð´Ð½. (Ð¿Ð¾ {today_str})*'
    lines = [title]
    lines.append(f'_{len(rows)} Ð¿Ð¾ÐºÑƒÐ¿Ð¾Ðº, Ð²ÑÐµÐ³Ð¾ {total:,.0f} â‚½_\n'.replace(',', ' '))

    for r in rows:
        date_str, amount, store, source, notes = r
        amt = amount or 0
        src_icon = source_icons.get(source or '', 'ðŸ“§')
        store_clean = store or 'â€”'
        notes_clean = (notes or '').replace('\n', ' ').strip()
        if notes_clean:
            short_note = notes_clean[:80]
            lines.append(f'{src_icon} *{store_clean}* â€” {amt:,.0f} â‚½'.replace(',', ' '))
            lines.append(f'   {short_note}')
        else:
            lines.append(f'{src_icon} *{store_clean}* â€” {amt:,.0f} â‚½'.replace(',', ' '))

    # ÐŸÐ¾ Ð¼Ð°Ð³Ð°Ð·Ð¸Ð½Ð°Ð¼
    by_store = {}
    for r in rows:
        s = r[2] or 'Ð”Ñ€ÑƒÐ³Ð¾Ðµ'
        by_store[s] = by_store.get(s, 0) + (r[1] or 0)
    if len(by_store) > 1:
        lines.append(f'\nðŸ“Œ *ÐŸÐ¾ Ð¼Ð°Ð³Ð°Ð·Ð¸Ð½Ð°Ð¼:*')
        for s, a in sorted(by_store.items(), key=lambda x: -x[1]):
            lines.append(f'  â€¢ {s}: {a:,.0f} â‚½'.replace(',', ' '))

    await msg.edit_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_monthexp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /monthexp â€” Ñ€Ð°ÑÑ…Ð¾Ð´Ñ‹ Ñ Ð½Ð°Ñ‡Ð°Ð»Ð° Ð¼ÐµÑÑÑ†Ð° Ñ Ñ€Ð°ÑÑˆÐ¸Ñ„Ñ€Ð¾Ð²ÐºÐ¾Ð¹ Ð¿Ð¾ Ð´Ð½ÑÐ¼.
    Ð—Ð° Ñ‚ÐµÐºÑƒÑ‰Ð¸Ð¹ Ð´ÐµÐ½ÑŒ â€” Ð¿Ñ€Ð¸Ð½ÑƒÐ´Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð¾Ðµ ÑÐºÐ°Ð½Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ðµ Ð¿Ð¾Ñ‡Ñ‚ + SMS (Ñ„Ð¾Ð½Ð¾Ð²Ð¾)."""
    msg = await update.message.reply_text('ðŸ” Ð¡ÐºÐ°Ð½Ð¸Ñ€ÑƒÑŽ Ð¿Ð¾Ñ‡Ñ‚Ñ‹ Ð¸ SMS â€” ÑÐ¾Ð±Ð¸Ñ€Ð°ÑŽ Ð´Ð°Ð½Ð½Ñ‹Ðµ Ð·Ð° Ð¼ÐµÑÑÑ†...')

    # Ð¤Ð¾Ð½Ð¾Ð²Ð¾Ðµ ÑÐºÐ°Ð½Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ðµ
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, 'daily_cheque_scan.py',
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        log_text = (stdout + stderr).decode('utf-8', errors='replace')[:500]
        print(f'[monthexp] scan result:\n{log_text}')
    except asyncio.TimeoutError:
        print('[monthexp] scan timeout')
    except Exception as e:
        print(f'[monthexp] scan error: {e}')

    today = datetime.now()
    month_start = today.strftime('%Y-%m-01')
    today_str = today.strftime('%Y-%m-%d')
    month_names = {
        1:'Ð¯Ð½Ð²Ð°Ñ€ÑŒ', 2:'Ð¤ÐµÐ²Ñ€Ð°Ð»ÑŒ', 3:'ÐœÐ°Ñ€Ñ‚', 4:'ÐÐ¿Ñ€ÐµÐ»ÑŒ',
        5:'ÐœÐ°Ð¹', 6:'Ð˜ÑŽÐ½ÑŒ', 7:'Ð˜ÑŽÐ»ÑŒ', 8:'ÐÐ²Ð³ÑƒÑÑ‚',
        9:'Ð¡ÐµÐ½Ñ‚ÑÐ±Ñ€ÑŒ', 10:'ÐžÐºÑ‚ÑÐ±Ñ€ÑŒ', 11:'ÐÐ¾ÑÐ±Ñ€ÑŒ', 12:'Ð”ÐµÐºÐ°Ð±Ñ€ÑŒ'
    }
    month_name = f'{month_names.get(today.month, "")} {today.year}'

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date >= ? AND purchase_date <= ?
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY purchase_date, total_amount DESC
        """, (month_start, today_str)).fetchall()
    finally:
        conn.close()

    if not rows:
        await msg.edit_text(f'ðŸ“­ Ð—Ð° {month_name} (Ñ 1 Ð¿Ð¾ {today.day}) Ð¿Ð¾ÐºÑƒÐ¿Ð¾Ðº Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾.')
        return

    grand_total = sum(r[1] or 0 for r in rows)
    source_icons = {'gmail': 'ðŸ“§', 'yandex': 'ðŸ“§', 'yandex_food': 'ðŸ½', 'sms': 'ðŸ“±', 'local': 'ðŸ“', 'manual': 'âœï¸'}

    lines = [f'ðŸ“Š *Ð Ð°ÑÑ…Ð¾Ð´Ñ‹ Ñ 1 {month_name.lower()} Ð¿Ð¾ {today.day} Ñ‡Ð¸ÑÐ»Ð¾*']
    lines.append(f'_{len(rows)} Ð¿Ð¾ÐºÑƒÐ¿Ð¾Ðº, Ð²ÑÐµÐ³Ð¾ {grand_total:,.0f} â‚½_\n'.replace(',', ' '))

    # Ð“Ñ€ÑƒÐ¿Ð¿Ð¸Ñ€Ð¾Ð²ÐºÐ° Ð¿Ð¾ Ð´Ð½ÑÐ¼
    by_day = {}
    for r in rows:
        d = r[0]
        if d not in by_day:
            by_day[d] = []
        by_day[d].append(r)

    for day in sorted(by_day.keys(), reverse=True):
        day_rows = by_day[day]
        day_total = sum(r[1] or 0 for r in day_rows)
        day_label = 'Ð¡ÐµÐ³Ð¾Ð´Ð½Ñ' if day == today_str else day
        lines.append(f'\nðŸ“… *{day_label}* â€” {day_total:,.0f} â‚½ ({len(day_rows)} Ð¿Ð¾ÐºÑƒÐ¿Ð¾Ðº)'.replace(',', ' '))

        for r in day_rows:
            date_str, amount, store, source, notes = r
            amt = amount or 0
            src_icon = source_icons.get(source or '', 'ðŸ“§')
            store_clean = store or 'â€”'
            notes_clean = (notes or '').replace('\n', ' ').strip()[:60]
            if notes_clean:
                lines.append(f'{src_icon} *{store_clean}* â€” {amt:,.0f} â‚½ Â· {notes_clean}'.replace(',', ' '))
            else:
                lines.append(f'{src_icon} *{store_clean}* â€” {amt:,.0f} â‚½'.replace(',', ' '))

    # ÐŸÐ¾ Ð¼Ð°Ð³Ð°Ð·Ð¸Ð½Ð°Ð¼
    by_store = {}
    for r in rows:
        s = r[2] or 'Ð”Ñ€ÑƒÐ³Ð¾Ðµ'
        by_store[s] = by_store.get(s, 0) + (r[1] or 0)
    if len(by_store) > 1:
        lines.append(f'\nðŸ“Œ *Ð’ÑÐµÐ³Ð¾ Ð¿Ð¾ Ð¼Ð°Ð³Ð°Ð·Ð¸Ð½Ð°Ð¼:*')
        for s, a in sorted(by_store.items(), key=lambda x: -x[1]):
            lines.append(f'  â€¢ {s}: {a:,.0f} â‚½'.replace(',', ' '))

    await msg.edit_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_warranties(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /warranties â€” Ð¾Ñ‚Ñ‡Ñ‘Ñ‚ Ð¿Ð¾ Ð³Ð°Ñ€Ð°Ð½Ñ‚Ð¸ÑÐ¼."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from warranty_check import get_warranties_report, update_warranty_until, check_warranties, save_alerts
        conn = get_db()
        # ÐŸÐµÑ€ÐµÑÑ‡Ñ‘Ñ‚ warranty_until
        update_warranty_until(conn)
        # ÐŸÑ€Ð¾Ð²ÐµÑ€ÐºÐ° Ð¸ ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð¸Ðµ Ð°Ð»ÐµÑ€Ñ‚Ð¾Ð²
        alerts = check_warranties(conn)
        if alerts:
            save_alerts(conn, alerts)
        # ÐžÑ‚Ñ‡Ñ‘Ñ‚
        report = get_warranties_report(conn)
        conn.close()
        await update.message.reply_text(report, parse_mode='Markdown')
    except Exception as e:
        log.error(f'cmd_warranties error: {e}')
        await update.message.reply_text(f'âŒ ÐžÑˆÐ¸Ð±ÐºÐ°: {e}')


async def cmd_add_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /add_item â€” Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ð²ÐµÑ‰ÑŒ Ð² Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ Ñ Ñ„Ð¾Ñ‚Ð¾, Ð±Ñ€ÐµÐ½Ð´Ð¾Ð¼ Ð¸ ÑÑ€Ð¾ÐºÐ¾Ð¼ Ð·Ð°Ð¼ÐµÐ½Ñ‹.
    Ð¤Ð¾Ñ€Ð¼Ð°Ñ‚:
      /add_item ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ
      /add_item ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ | Ð±Ñ€ÐµÐ½Ð´ Ð‘Ñ€ÐµÐ½Ð´ | Ð·Ð°Ð¼ÐµÐ½Ð° 60 Ð¼ÐµÑ
      /add_item ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ | Ð±Ñ€ÐµÐ½Ð´ Ð‘Ñ€ÐµÐ½Ð´ | Ð·Ð°Ð¼ÐµÐ½Ð° 5 Ð»ÐµÑ‚
    ÐœÐ¾Ð¶Ð½Ð¾ Ð¿Ñ€Ð¸ÐºÑ€ÐµÐ¿Ð¸Ñ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾ Ðº ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÑŽ."""
    text = ' '.join(ctx.args).strip()
    if not text:
        await update.message.reply_text(
            'âŒ Ð£ÐºÐ°Ð¶Ð¸Ñ‚Ðµ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ Ð²ÐµÑ‰Ð¸\n\n'
            'ÐŸÑ€Ð¸Ð¼ÐµÑ€:\n'
            '/add_item Ð¡Ñ‚Ñ€ÐµÐ¼ÑÐ½ÐºÐ° 5 ÑÑ‚ÑƒÐ¿ÐµÐ½ÐµÐ¹\n'
            '/add_item ÐŸÑ‹Ð»ÐµÑÐ¾Ñ | Ð±Ñ€ÐµÐ½Ð´ Xiaomi | Ð·Ð°Ð¼ÐµÐ½Ð° 60 Ð¼ÐµÑ\n'
            '/add_item ÐÐ¾ÑÐºÐ¸ | Ð±Ñ€ÐµÐ½Ð´ Nike | Ð·Ð°Ð¼ÐµÐ½Ð° 12 Ð¼ÐµÑ\n\n'
            'ÐœÐ¾Ð¶Ð½Ð¾ Ð¿Ñ€Ð¸ÐºÑ€ÐµÐ¿Ð¸Ñ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾ Ðº ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÑŽ.'
        )
        return

    # ÐŸÐ°Ñ€ÑÐ¸Ð¼ Ð¿Ð¾Ð»Ñ Ñ‡ÐµÑ€ÐµÐ· ÑƒÐ½Ð¸Ð²ÐµÑ€ÑÐ°Ð»ÑŒÐ½Ñ‹Ð¹ brand_parser
    from brand_parser import parse_brand_and_name
    bp = parse_brand_and_name(text)
    name = bp['name'] or text
    brand = bp['brand']
    replace_months = bp['replace_months']
    replace_days = bp.get('replace_days')

    # ÐÐ¾Ñ€Ð¼Ð°Ð»Ð¸Ð·ÑƒÐµÐ¼ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ â€” ÑƒÐ±Ð¸Ñ€Ð°ÐµÐ¼ Ð»Ð¸ÑˆÐ½ÐµÐµ
    name = name.strip().strip(',;')
    if not name:
        await update.message.reply_text('âŒ ÐÐ°Ð·Ð²Ð°Ð½Ð¸Ðµ Ð½Ðµ Ð¼Ð¾Ð¶ÐµÑ‚ Ð±Ñ‹Ñ‚ÑŒ Ð¿ÑƒÑÑ‚Ñ‹Ð¼')
        return

    # ÐžÐ¿Ñ€ÐµÐ´ÐµÐ»ÑÐµÐ¼ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸ÑŽ Ð¿Ð¾ ÐºÐ»ÑŽÑ‡ÐµÐ²Ñ‹Ð¼ ÑÐ»Ð¾Ð²Ð°Ð¼ Ð² Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ð¸
    cat_map = {
        'ÑÑ‚Ñ€ÐµÐ¼ÑÐ½': 'cat_home', 'Ð¿Ñ‹Ð»ÐµÑÐ¾Ñ': 'cat_tech', 'ÑƒÑ‚ÑŽÐ³': 'cat_home',
        'ÑƒÑ‚ÑŽÐ¶': 'cat_home', 'Ñ„ÐµÐ½': 'cat_cosmetics', 'Ñ€Ð°ÑÑ‡ÐµÑ': 'cat_cosmetics',
        'Ñ‰Ñ‘Ñ‚Ðº': 'cat_home', 'Ð·ÑƒÐ±Ð½': 'cat_health_med', 'Ð¿Ð¾Ð»Ð¾Ñ‚ÐµÐ½': 'cat_home',
        'Ð¿Ð¾ÑÑ‚ÐµÐ»ÑŒ': 'cat_home', 'Ð¿Ñ€Ð¾ÑÑ‚Ñ‹Ð½': 'cat_home', 'Ð½Ð°Ð²Ð¾Ð»Ð¾Ñ‡Ðº': 'cat_home',
        'Ð¾Ð´ÐµÑÐ»': 'cat_home', 'Ð¿Ð¾Ð´ÑƒÑˆÐº': 'cat_home', 'ÐºÐ¾Ð²Ñ‘Ñ€': 'cat_home_furn',
        'ÐºÐ¾Ð²ÐµÑ€': 'cat_home_furn', 'ÑˆÑ‚Ð¾Ñ€Ð°': 'cat_home_furn', 'ÑÐ²ÐµÑ‚Ð¸Ð»ÑŒ': 'cat_home_furn',
        'Ð»Ð°Ð¼Ð¿Ð°': 'cat_home_furn', 'Ð»ÑŽÑÑ‚Ñ€': 'cat_home_furn', 'Ñ‚Ð¾Ñ€ÑˆÐµÑ€': 'cat_home_furn',
        'ÐºÑ€ÐµÑÐ»': 'cat_home_furn', 'Ð´Ð¸Ð²Ð°Ð½': 'cat_home_furn', 'ÑÑ‚Ð¾Ð»': 'cat_home_furn',
        'ÑÑ‚ÑƒÐ»': 'cat_home_furn', 'ÐºÑ€Ð¾Ð²Ð°Ñ‚': 'cat_home_furn', 'ÐºÐ¾Ð¼Ð¾Ð´': 'cat_home_furn',
        'Ñ‚ÑƒÐ¼Ð±': 'cat_home_furn', 'ÑˆÐºÐ°Ñ„': 'cat_home_furn', 'ÑÑ‚ÐµÐ»Ð»Ð°Ð¶': 'cat_home_furn',
        'ÐºÑƒÑ€Ñ‚Ðº': 'cat_clo_everyday', 'Ð¿Ð°Ð»ÑŒÑ‚': 'cat_clo_everyday', 'Ð¿ÑƒÑ…Ð¾Ð²': 'cat_clo_everyday',
        'Ð¿Ð»Ð°Ñ‰': 'cat_clo_everyday', 'Ð¿Ð¸Ð´Ð¶Ð°Ðº': 'cat_clo_everyday', 'ÐºÐ¾ÑÑ‚ÑŽÐ¼': 'cat_clo_everyday',
        'Ð´Ð¶Ð¸Ð½Ñ': 'cat_clo_everyday', 'Ð±Ñ€ÑŽÐº': 'cat_clo_everyday', 'ÑˆÑ‚Ð°Ð½Ñ‹': 'cat_clo_everyday',
        'Ñ„ÑƒÑ‚Ð±Ð¾Ð»Ðº': 'cat_clo_everyday', 'Ñ€ÑƒÐ±Ð°ÑˆÐº': 'cat_clo_everyday', 'ÑÐ²Ð¸Ñ‚ÐµÑ€': 'cat_clo_everyday',
        'Ð²Ð¾Ð´Ð¾Ð»Ð°Ð·': 'cat_clo_everyday', 'Ñ‚Ð¾Ð»ÑÑ‚Ð¾Ð²': 'cat_clo_everyday', 'Ñ…ÑƒÐ´Ð¸': 'cat_clo_everyday',
        'Ð½Ð¾ÑÐº': 'cat_clo_underwear', 'Ñ‚Ñ€ÑƒÑ': 'cat_clo_underwear', 'Ð¼Ð°Ð¹Ðº': 'cat_clo_underwear',
        'Ð±Ð¾Ñ‚Ð¸Ð½Ðº': 'cat_clo_shoes', 'ÐºÑ€Ð¾ÑÑÐ¾Ð²': 'cat_clo_shoes', 'Ñ‚ÑƒÑ„Ð»': 'cat_clo_shoes',
        'ÑÐ°Ð¿Ð¾Ð³': 'cat_clo_shoes', 'Ñ‚Ð°Ð¿Ðº': 'cat_clo_shoes', 'ÑˆÐ»Ñ‘Ð¿Ð°Ð½': 'cat_clo_shoes',
        'ÑˆÐ°Ñ€Ñ„': 'cat_clo_access', 'ÑˆÐ°Ð¿Ðº': 'cat_clo_access', 'Ñ€ÐµÐ¼ÐµÐ½': 'cat_clo_access',
        'Ð¿ÐµÑ€Ñ‡Ð°Ñ‚': 'cat_clo_access', 'ÑÑƒÐ¼Ðº': 'cat_clo_access', 'Ñ€ÑŽÐºÐ·Ð°Ðº': 'cat_clo_access',
        'Ñ‡Ð°ÑÑ‹': 'cat_clo_access', 'Ð±Ñ€Ð°ÑÐ»ÐµÑ‚': 'cat_clo_access', 'Ð¾Ñ‡Ðº': 'cat_clo_access',
        'Ñ‚ÐµÐ»ÐµÑ„Ð¾Ð½': 'cat_tech', 'Ð½Ð¾ÑƒÑ‚Ð±ÑƒÐº': 'cat_tech', 'Ð¿Ð»Ð°Ð½ÑˆÐµÑ‚': 'cat_tech',
        'Ð½Ð°ÑƒÑˆÐ½Ð¸Ðº': 'cat_tech', 'ÐºÐ¾Ð»Ð¾Ð½Ðº': 'cat_tech', 'Ñ€Ð¾ÑƒÑ‚ÐµÑ€': 'cat_tech',
        'Ð¼Ð¾Ð½Ð¸Ñ‚Ð¾Ñ€': 'cat_tech', 'ÐºÐ»Ð°Ð²Ð¸Ð°Ñ‚ÑƒÑ€': 'cat_tech', 'Ð¼Ñ‹ÑˆÐº': 'cat_tech',
        'ÐºÐ°Ð¼ÐµÑ€': 'cat_tech', 'Ð¿Ñ€Ð¸Ð½Ñ‚ÐµÑ€': 'cat_tech', 'Ð¿Ñ€Ð¾Ð²Ð¾Ð´': 'cat_tech',
        'Ð·Ð°Ñ€ÑÐ´Ðº': 'cat_tech', 'ÐºÐ°Ð±ÐµÐ»ÑŒ': 'cat_tech', 'Ð°Ð´Ð°Ð¿Ñ‚ÐµÑ€': 'cat_tech',
        'Ñ…Ð¾Ð»Ð¾Ð´Ð¸Ð»ÑŒ': 'cat_tech', 'Ð¼Ð¸ÐºÑ€Ð¾Ð²Ð¾Ð»Ð½': 'cat_tech', 'Ñ‚Ð¾ÑÑ‚ÐµÑ€': 'cat_tech',
        'Ð±Ð»ÐµÐ½Ð´ÐµÑ€': 'cat_tech', 'ÐºÐ¾Ñ„ÐµÐ¼Ð¾Ð»Ðº': 'cat_tech', 'Ñ‡Ð°Ð¹Ð½Ð¸Ðº': 'cat_home_kitchen',
        'ÑÐºÐ¾Ð²Ð¾Ñ€Ð¾Ð´': 'cat_home_kitchen', 'ÐºÐ°ÑÑ‚Ñ€ÑŽÐ»': 'cat_home_kitchen', 'Ð½Ð¾Ð¶': 'cat_home_kitchen',
        'Ñ‚Ð°Ñ€ÐµÐ»Ðº': 'cat_home_kitchen', 'ÐºÑ€ÑƒÐ¶Ðº': 'cat_home_kitchen', 'Ñ‡Ð°ÑˆÐº': 'cat_home_kitchen',
        'ÐºÐ¾ÑÐ¼ÐµÑ‚Ð¸Ðº': 'cat_cosmetics', 'ÐºÑ€ÐµÐ¼': 'cat_cosmetics', 'ÑˆÐ°Ð¼Ð¿ÑƒÐ½': 'cat_cosmetics',
        'ÐºÐ¾Ð½Ð´Ð¸Ñ†Ð¸Ð¾Ð½ÐµÑ€': 'cat_cosmetics', 'Ð¼Ñ‹Ð»': 'cat_cosmetics', 'Ð´ÑƒÑ…': 'cat_cosmetics',
        'Ð¸Ð³Ñ€ÑƒÑˆÐº': 'cat_hobbies', 'Ð½Ð°ÑÑ‚Ð¾Ð»ÑŒÐ½': 'cat_hobbies', 'ÐºÐ½Ð¸Ð³': 'cat_culture_books',
        'ÐºÐ¾Ñ€Ð¼': 'cat_pets', 'Ð¸Ð³Ñ€ÑƒÑˆÐº.*Ð¶Ð¸Ð²Ð¾Ñ‚Ð½': 'cat_pets', 'Ð»ÐµÐ¶Ð°Ðº': 'cat_pets',
    }
    category = None
    nl = name.lower()
    for kw, cid in cat_map.items():
        if kw in nl:
            category = cid
            break
    if not category:
        category = 'cat_other'

    # Ð¤Ð¾Ñ€Ð¼Ð¸Ñ€ÑƒÐµÐ¼ notes Ñ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÐµÐ¹ Ð¾ Ð·Ð°Ð¼ÐµÐ½Ðµ
    notes_parts = ['Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ Ñ‡ÐµÑ€ÐµÐ· /add_item']
    replace_days = bp.get('replace_days')
    if replace_days:
        notes_parts.append(f'ÐžÐ¶Ð¸Ð´Ð°ÐµÑ‚ÑÑ Ð·Ð°Ð¼ÐµÐ½Ð° Ñ‡ÐµÑ€ÐµÐ· {replace_days} Ð´Ð½.')
    elif replace_months:
        notes_parts.append(f'ÐžÐ¶Ð¸Ð´Ð°ÐµÑ‚ÑÑ Ð·Ð°Ð¼ÐµÐ½Ð° Ñ‡ÐµÑ€ÐµÐ· {replace_months} Ð¼ÐµÑ.')
    notes = '\n'.join(notes_parts)

    # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð² Ð‘Ð”
    conn = get_db()
    try:
        cur = conn.execute('''
            INSERT INTO items
                (name, brand, category_id, status, replace_after_months, replace_after_days, purchase_date,
                 notes, data_origin)
            VALUES (?, ?, ?, 'in_use', ?, ?, date('now'),
                    ?, 'manual')
        ''', (name, brand, category, replace_months, replace_days, notes))
        conn.commit()
        item_id = cur.lastrowid
    finally:
        conn.close()

    # Ð•ÑÐ»Ð¸ ÐµÑÑ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾ â€” ÑÐ¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ð¸ Ð¾Ð±Ð¾Ð³Ð°Ñ‰Ð°ÐµÐ¼ Ñ‡ÐµÑ€ÐµÐ· Vision API
    has_photo = False
    vision_enriched = {}
    photos = []
    if update.message and update.message.photo:
        photos = update.message.photo
    # Ð•ÑÐ»Ð¸ ÑÑ‚Ð¾ reply Ð½Ð° ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ Ñ Ñ„Ð¾Ñ‚Ð¾ â€” Ð±ÐµÑ€Ñ‘Ð¼ Ñ„Ð¾Ñ‚Ð¾ Ð¸Ð· Ð¾Ñ€Ð¸Ð³Ð¸Ð½Ð°Ð»ÑŒÐ½Ð¾Ð³Ð¾ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ñ
    elif update.message and update.message.reply_to_message and update.message.reply_to_message.photo:
        photos = update.message.reply_to_message.photo
        log.info(f'add_item: using photo from reply_to_message {update.message.reply_to_message.message_id}')

    if photos:
        best = photos[-1]
        try:
            file = await best.get_file()
            file_bytes = await file.download_as_bytearray()

            # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾
            import memory_lane as _ml
            media_dir = os.path.join(os.path.dirname(DB_PATH), 'data', 'media')
            mconn = get_db()
            try:
                asset_id = _ml.save_media(mconn, file_bytes, mime='image/jpeg', base_dir=media_dir)
                if asset_id:
                    mconn.execute(
                        'INSERT OR IGNORE INTO item_photos (item_id, media_asset_id, is_primary) VALUES (?, ?, 1)',
                        (item_id, asset_id))
                    mconn.commit()
                    has_photo = True
                    log.info(f'add_item photo saved: item_id={item_id}, asset_id={asset_id}')
            finally:
                mconn.close()

            # Vision API Ð¾Ð±Ð¾Ð³Ð°Ñ‰ÐµÐ½Ð¸Ðµ: Ð±Ñ€ÐµÐ½Ð´, Ñ†Ð²ÐµÑ‚, Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸Ðµ
            try:
                tmp_path = os.path.join(RECEIPTS_DIR, f'_additem_{update.message.message_id}.jpg')
                with open(tmp_path, 'wb') as fh:
                    fh.write(file_bytes)
                from vision_item import recognize_item
                vision_enriched = recognize_item(tmp_path)
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                if vision_enriched and 'error' not in vision_enriched:
                    # Ð‘Ñ€ÐµÐ½Ð´ Ð¸Ð· Ñ‚ÐµÐºÑÑ‚Ð° Ð¿Ñ€Ð¸Ð¾Ñ€Ð¸Ñ‚ÐµÑ‚Ð½ÐµÐµ, Vision ÐºÐ°Ðº fallback
                    if not brand and vision_enriched.get('brand'):
                        brand = vision_enriched['brand']
                    # ÐžÐ±Ð½Ð¾Ð²Ð»ÑÐµÐ¼ Ð·Ð°Ð¿Ð¸ÑÑŒ Ð² Ð‘Ð”: attributes + notes
                    uconn = get_db()
                    attrs = json.dumps({
                        'color': vision_enriched.get('color'),
                        'description': vision_enriched.get('description'),
                        'style_tags': vision_enriched.get('style_tags', []),
                        'material': vision_enriched.get('material'),
                        'estimated_price_rub': vision_enriched.get('estimated_price_rub'),
                    }, ensure_ascii=False)
                    # Ð”Ð¾Ð¿Ð¾Ð»Ð½ÑÐµÐ¼ notes Ð´Ð°Ð½Ð½Ñ‹Ð¼Ð¸ Ð¾Ñ‚ Vision
                    vision_notes = []
                    if vision_enriched.get('color'):
                        vision_notes.append(f"Ð¦Ð²ÐµÑ‚: {vision_enriched['color']}")
                    if vision_enriched.get('material'):
                        vision_notes.append(f"ÐœÐ°Ñ‚ÐµÑ€Ð¸Ð°Ð»: {vision_enriched['material']}")
                    if vision_enriched.get('description'):
                        vision_notes.append(f"ÐžÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ: {vision_enriched['description']}")
                    if vision_enriched.get('estimated_price_rub'):
                        vision_notes.append(f"ÐžÑ†ÐµÐ½Ð¾Ñ‡Ð½Ð°Ñ Ñ†ÐµÐ½Ð°: ~{vision_enriched['estimated_price_rub']} â‚½")
                    if vision_notes:
                        new_notes = notes + '\n' + '\n'.join(vision_notes)
                        uconn.execute(
                            'UPDATE items SET brand=COALESCE(?, brand), attributes=?, notes=? WHERE id=?',
                            (brand, attrs, new_notes, item_id))
                    else:
                        uconn.execute(
                            'UPDATE items SET brand=COALESCE(?, brand), attributes=? WHERE id=?',
                            (brand, attrs, item_id))
                    uconn.commit()
                    uconn.close()
                    log.info(f'Vision enriched add_item #{item_id}: brand={brand}, fields={list(vision_enriched.keys())}')
            except Exception as e:
                log.warning(f'Vision enrich for add_item failed: {e}')
        except Exception as e:
            log.warning(f'add_item photo save failed: {e}')

    # Ð¤Ð¾Ñ€Ð¼Ð¸Ñ€ÑƒÐµÐ¼ Ð¾Ñ‚Ð²ÐµÑ‚
    lines = [f'âœ… Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾: *{esc_md(name)}*']
    if brand:
        lines.append(f'ðŸ· Ð‘Ñ€ÐµÐ½Ð´: {esc_md(brand)}')
    lines.append(f'ðŸ“‚ ÐšÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ: {esc_md(category)}')
    if replace_days:
        lines.append(f'ðŸ”„ Ð—Ð°Ð¼ÐµÐ½Ð°: Ñ‡ÐµÑ€ÐµÐ· {replace_days} Ð´Ð½.')
    elif replace_months:
        lines.append(f'ðŸ”„ Ð—Ð°Ð¼ÐµÐ½Ð°: Ñ‡ÐµÑ€ÐµÐ· {replace_months} Ð¼ÐµÑ.')
    if vision_enriched and 'error' not in vision_enriched:
        if vision_enriched.get('color'):
            lines.append(f'ðŸŽ¨ Ð¦Ð²ÐµÑ‚: {vision_enriched["color"]}')
        if vision_enriched.get('description'):
            lines.append(f'ðŸ“ {vision_enriched["description"]}')
        if vision_enriched.get('style_tags'):
            lines.append(f'ðŸ·ï¸ Ð¢ÐµÐ³Ð¸: {", ".join(vision_enriched["style_tags"])}')
        if vision_enriched.get('estimated_price_rub'):
            lines.append(f'ðŸ’° ÐžÑ†ÐµÐ½ÐºÐ°: ~{vision_enriched["estimated_price_rub"]} â‚½')
    if has_photo:
        lines.append('ðŸ“¸ Ð¤Ð¾Ñ‚Ð¾ ÑÐ¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð¾')
    lines.append(f'\nID: {item_id}')

    # ÐšÐ½Ð¾Ð¿ÐºÐ° ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ñ
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton('ðŸ—‘ Ð£Ð´Ð°Ð»Ð¸Ñ‚ÑŒ', callback_data=f'item_delete:{item_id}')
    ]])

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown', reply_markup=kb)


async def cmd_items(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /items â€” ÑÐ¿Ð¸ÑÐ¾Ðº Ð²ÐµÑ‰ÐµÐ¹ Ñ Ð³Ñ€ÑƒÐ¿Ð¿Ð¸Ñ€Ð¾Ð²ÐºÐ¾Ð¹ Ð¸ ÑÑ€Ð¾ÐºÐ°Ð¼Ð¸ Ð·Ð°Ð¼ÐµÐ½Ñ‹.
    /items â€” Ð²ÑÐµ Ð²ÐµÑ‰Ð¸, ÐºÐ¾Ñ‚Ð¾Ñ€Ñ‹Ð¼ ÑÐºÐ¾Ñ€Ð¾ Ð½ÑƒÐ¶Ð½Ð° Ð·Ð°Ð¼ÐµÐ½Ð°
    /items all â€” Ð²ÑÐµ Ð²ÐµÑ‰Ð¸
    /items <ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ> â€” Ð²ÐµÑ‰Ð¸ Ð¿Ð¾ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¸"""
    conn = get_db()
    try:
        all_items = conn.execute('''
            SELECT i.id, i.name, i.brand, i.category_id, i.lifespan_months,
                   i.purchase_date, i.status, i.replace_after_months, i.replace_after_days, i.notes,
                   i.attributes,
                   COALESCE(c.name, i.category_id) AS category_name
            FROM items i
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL AND i.is_delivery = 0
              AND i.data_origin IN ('manual', 'local', 'telegram_photo', 'vision_photo', 'telegram_tag')
            ORDER BY i.category_id, i.name
        ''').fetchall()
    finally:
        conn.close()

    if not all_items:
        await update.message.reply_text('ðŸ“­ Ð’ Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€Ðµ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ‚ Ð²ÐµÑ‰ÐµÐ¹. Ð”Ð¾Ð±Ð°Ð²ÑŒÑ‚Ðµ Ñ‡ÐµÑ€ÐµÐ· /add_item')
        return

    today = datetime.now().date()

    # Ð¤Ð¸Ð»ÑŒÑ‚Ñ€
    args = ' '.join(ctx.args).lower() if ctx.args else ''
    if args and args != 'all':
        # ÐŸÐ¾Ð¸ÑÐº Ð¿Ð¾ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸ÑŽ, Ð±Ñ€ÐµÐ½Ð´Ñƒ, ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¸, Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸ÑŽ, Ñ‚ÐµÐ³Ð°Ð¼
        filtered = []
        for r in all_items:
            name = (r[1] or '').lower()
            brand = (r[2] or '').lower()
            cat = (r[11] or r[3] or '').lower()  # category_name, fallback category_id
            notes = (r[9] or '').lower()
            attrs = {}
            try:
                attrs = json.loads(r[10] or '{}')  # attributes
            except (json.JSONDecodeError, IndexError):
                pass
            desc = (attrs.get('description') or '').lower()
            tags = ' '.join(attrs.get('style_tags', [])).lower()
            color = (attrs.get('color') or '').lower()
            material = (attrs.get('material') or '').lower()
            
            search_text = f'{name} {brand} {cat} {notes} {desc} {tags} {color} {material}'
            if args in search_text:
                filtered.append(r)
    elif args == 'all':
        filtered = all_items
    else:
        # ÐŸÐ¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ: Ñ‚Ðµ, Ñƒ ÐºÐ¾Ð³Ð¾ ÐµÑÑ‚ÑŒ replace_after_months/days Ð¸Ð»Ð¸ lifespan_months, Ð¸ Ð¾Ð½Ð¸ Ð¸ÑÑ‚ÐµÐºÐ°ÑŽÑ‚
        filtered = []
        for r in all_items:
            rep_days = r[8]  # replace_after_days (Ñ‚Ð¾Ñ‡Ð½Ð¾Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ðµ)
            rep_months = r[7] or r[4]  # replace_after_months, Ð¿Ð¾Ñ‚Ð¾Ð¼ lifespan_months
            if not rep_months and not rep_days:
                continue
            purchase = r[5]
            if purchase:
                try:
                    pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                    if rep_days:
                        replace_date = pd + timedelta(days=rep_days)
                    else:
                        replace_date = add_months_safe(pd, rep_months)
                    days_left = (replace_date - today).days
                    if days_left <= 90:  # Ð±Ð»Ð¸Ð¶Ð°Ð¹ÑˆÐ¸Ðµ 3 Ð¼ÐµÑÑÑ†Ð°
                        filtered.append((days_left, r))
                except (TypeError, ValueError) as e:
                    log.warning('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð²Ñ‹Ñ‡Ð¸ÑÐ»Ð¸Ñ‚ÑŒ ÑÑ€Ð¾Ðº Ð·Ð°Ð¼ÐµÐ½Ñ‹ Ð´Ð»Ñ item_id=%s: %s', r[0], e)
        filtered.sort(key=lambda x: x[0])
        filtered = [r[1] for r in filtered]
        if not filtered:
            # Ð•ÑÐ»Ð¸ Ð½ÐµÑ‚ Ð²ÐµÑ‰ÐµÐ¹ Ðº Ð·Ð°Ð¼ÐµÐ½Ðµ, Ð¿Ð¾ÐºÐ°Ð·Ñ‹Ð²Ð°ÐµÐ¼ Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð½Ñ‹Ðµ
            filtered = all_items[-10:]

    if not filtered:
        await update.message.reply_text('ðŸ“­ ÐÐ¸Ñ‡ÐµÐ³Ð¾ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾ Ð¿Ð¾ Ð²Ð°ÑˆÐµÐ¼Ñƒ Ð·Ð°Ð¿Ñ€Ð¾ÑÑƒ.')
        return

    # Ð“Ñ€ÑƒÐ¿Ð¿Ð¸Ñ€ÑƒÐµÐ¼ Ð¿Ð¾ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸ÑÐ¼
    by_cat = {}
    for r in filtered:
        cat = r[3] or 'cat_other'
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(r)

    # Ð§ÐµÐ»Ð¾Ð²ÐµÑ‡ÐµÑÐºÐ¸Ðµ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ñ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¹
    cat_names = {
        'cat_clo_everyday': 'ðŸ‘• ÐŸÐ¾Ð²ÑÐµÐ´Ð½ÐµÐ²Ð½Ð°Ñ Ð¾Ð´ÐµÐ¶Ð´Ð°',
        'cat_clo_underwear': 'ðŸ‘™ ÐÐ¸Ð¶Ð½ÐµÐµ Ð±ÐµÐ»ÑŒÑ‘ / Ð½Ð¾ÑÐºÐ¸',
        'cat_clo_shoes': 'ðŸ‘Ÿ ÐžÐ±ÑƒÐ²ÑŒ',
        'cat_clo_access': 'ðŸ§£ ÐÐºÑÐµÑÑÑƒÐ°Ñ€Ñ‹',
        'cat_tech': 'ðŸ’» Ð¢ÐµÑ…Ð½Ð¸ÐºÐ°',
        'cat_home': 'ðŸ  Ð¥Ð¾Ð·Ñ‚Ð¾Ð²Ð°Ñ€Ñ‹',
        'cat_home_furn': 'ðŸª‘ ÐœÐµÐ±ÐµÐ»ÑŒ',
        'cat_home_kitchen': 'ðŸ³ ÐšÑƒÑ…Ð½Ñ',
        'cat_cosmetics': 'ðŸ§´ ÐšÐ¾ÑÐ¼ÐµÑ‚Ð¸ÐºÐ°',
        'cat_health_med': 'ðŸ’Š Ð—Ð´Ð¾Ñ€Ð¾Ð²ÑŒÐµ',
        'cat_culture_books': 'ðŸ“š ÐšÐ½Ð¸Ð³Ð¸',
        'cat_hobbies': 'ðŸŽ® Ð¥Ð¾Ð±Ð±Ð¸',
        'cat_pets': 'ðŸ¾ Ð–Ð¸Ð²Ð¾Ñ‚Ð½Ñ‹Ðµ',
        'cat_sport': 'ðŸ‹ï¸ Ð¡Ð¿Ð¾Ñ€Ñ‚',
        'cat_auto': 'ðŸš— ÐÐ²Ñ‚Ð¾',
        'cat_food': 'ðŸŽ ÐŸÑ€Ð¾Ð´ÑƒÐºÑ‚Ñ‹',
        'cat_other': 'ðŸ“¦ ÐŸÑ€Ð¾Ñ‡ÐµÐµ',
    }

    lines = ['ðŸ“‹ *Ð˜Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ:*']
    for cat, items in sorted(by_cat.items()):
        cat_label = cat_names.get(cat, f'ðŸ“ {cat}')
        lines.append(f'\n*{cat_label}:*')
        for r in items:
            name = r[1]
            brand = r[2]
            rep = r[7] or r[4]
            purchase = r[5]

            name_str = esc_md(name)
            if brand:
                name_str += f' ({esc_md(brand)})'

            # Ð¡Ñ€Ð¾Ðº Ð·Ð°Ð¼ÐµÐ½Ñ‹
            if rep and purchase:
                try:
                    pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                    replace_date = add_months_safe(pd, rep)
                    days = (replace_date - today).days
                    if days <= 0:
                        suffix = ' ðŸ”´ ÐŸÐ¾Ñ€Ð° Ð¼ÐµÐ½ÑÑ‚ÑŒ!'
                    elif days <= 30:
                        suffix = f' ðŸŸ¡ Ð§ÐµÑ€ÐµÐ· {days} Ð´Ð½.'
                    else:
                        suffix = ''
                except (TypeError, ValueError) as e:
                    log.warning('ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¿Ð¾ÐºÐ°Ð·Ð°Ñ‚ÑŒ ÑÑ€Ð¾Ðº Ð·Ð°Ð¼ÐµÐ½Ñ‹ Ð´Ð»Ñ item_id=%s: %s', r[0], e)
                    suffix = ''
            else:
                suffix = ''

            lines.append(f'  â€¢ {name_str}{suffix}')

    lines.append(f'\nÐ’ÑÐµÐ³Ð¾: {len(filtered)} Ð²ÐµÑ‰ÐµÐ¹')
    if not args or args == 'all':
        lines.append('\n/items all â€” Ð¿Ð¾ÐºÐ°Ð·Ð°Ñ‚ÑŒ Ð²ÑÑ‘')
        lines.append('/items <ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ> â€” Ñ„Ð¸Ð»ÑŒÑ‚Ñ€')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_items_full(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐšÐ¾Ð¼Ð°Ð½Ð´Ð° /items_full â€” Ð¿Ð¾Ð»Ð½Ñ‹Ð¹ Ð²Ñ‹Ð²Ð¾Ð´ Ñ Ñ„Ð¾Ñ‚Ð¾ Ð¸ Ð²ÑÐµÐ¼Ð¸ Ð´Ð°Ð½Ð½Ñ‹Ð¼Ð¸.
    /items_full all â€” Ð²ÑÐµ Ð²ÐµÑ‰Ð¸ Ñ Ð¿Ð¾Ð»Ð½Ð¾Ð¹ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÐµÐ¹
    /items_full â€” Ð²ÐµÑ‰Ð¸ Ñ Ð·Ð°Ð¼ÐµÐ½Ð¾Ð¹ <30 Ð´Ð½ÐµÐ¹ (Ñ ðŸ”´)"""
    log.info(f'cmd_items_full called by chat_id={update.effective_chat.id if update.effective_chat else None}, args={ctx.args}')
    conn = get_db()
    try:
        all_items = conn.execute('''
            SELECT i.id, i.name, i.brand, i.category_id, i.lifespan_months,
                   i.purchase_date, i.status, i.replace_after_months, i.replace_after_days, i.notes,
                   i.attributes,
                   COALESCE(c.name, i.category_id) AS category_name
            FROM items i
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL AND i.is_delivery = 0
              AND i.data_origin IN ('manual', 'local', 'vision_photo', 'telegram_photo', 'telegram_tag')
            ORDER BY i.category_id, i.name
        ''').fetchall()
        # Ð—Ð°Ð³Ñ€ÑƒÐ¶Ð°ÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ (file_path Ð¸Ð· media_assets)
        photos = {}
        for row in conn.execute('''
            SELECT ip.item_id, ma.file_path 
            FROM item_photos ip
            JOIN media_assets ma ON ip.media_asset_id = ma.id
            WHERE ip.item_id IN (SELECT id FROM items WHERE deleted_at IS NULL)
        '''):
            photos[row[0]] = row[1]
    finally:
        conn.close()

    if not all_items:
        await update.message.reply_text('ðŸ“­ Ð’ Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€Ðµ Ð¿Ð¾ÐºÐ° Ð½ÐµÑ‚ Ð²ÐµÑ‰ÐµÐ¹.')
        return

    today = datetime.now().date()
    args = ' '.join(ctx.args).lower() if ctx.args else ''

    if args == 'all':
        filtered = all_items
    elif args:
        # Ð¤Ð¸Ð»ÑŒÑ‚Ñ€ Ð¿Ð¾ Ð½Ð°Ð·Ð²Ð°Ð½Ð¸ÑŽ, Ð±Ñ€ÐµÐ½Ð´Ñƒ, Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸ÑŽ, ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ð¸
        filtered = []
        for r in all_items:
            name = (r[1] or '').lower()
            brand = (r[2] or '').lower()
            cat = (r[11] or r[3] or '').lower()  # category_name, fallback category_id
            notes = (r[9] or '').lower()
            attrs = {}
            try:
                attrs = json.loads(r[10] or '{}')  # attributes
            except json.JSONDecodeError:
                pass
            desc = (attrs.get('description') or '').lower()
            tags = ' '.join(attrs.get('style_tags', [])).lower()
            color = (attrs.get('color') or '').lower()
            material = (attrs.get('material') or '').lower()
            
            # Ð˜Ñ‰ÐµÐ¼ Ð²Ð¾ Ð²ÑÐµÑ… Ð¿Ð¾Ð»ÑÑ…
            search_text = f'{name} {brand} {cat} {notes} {desc} {tags} {color} {material}'
            if args in search_text:
                filtered.append(r)
    else:
        # ÐŸÐ¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ: Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ñ Ð·Ð°Ð¼ÐµÐ½Ð¾Ð¹ <30 Ð´Ð½ÐµÐ¹ (ðŸ”´)
        filtered = []
        for r in all_items:
            rep_days = r[8]  # replace_after_days
            rep_months = r[7] or r[4]
            if not rep_months and not rep_days:
                continue
            purchase = r[5]
            if purchase:
                try:
                    pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                    if rep_days:
                        replace_date = pd + timedelta(days=rep_days)
                    else:
                        replace_date = add_months_safe(pd, rep_months)
                    days_left = (replace_date - today).days
                    if days_left <= 30:
                        filtered.append(r)
                except (TypeError, ValueError):
                    continue

    if not filtered:
        await update.message.reply_text('ðŸ“­ ÐÐ¸Ñ‡ÐµÐ³Ð¾ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾. Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐ¹ /items_full all Ð¸Ð»Ð¸ /items_full <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ>')
        return

    cat_names = {
        'cat_clo_everyday': 'ðŸ‘• ÐŸÐ¾Ð²ÑÐµÐ´Ð½ÐµÐ²Ð½Ð°Ñ Ð¾Ð´ÐµÐ¶Ð´Ð°',
        'cat_clo_underwear': 'ðŸ‘™ ÐÐ¸Ð¶Ð½ÐµÐµ Ð±ÐµÐ»ÑŒÑ‘ / Ð½Ð¾ÑÐºÐ¸',
        'cat_clo_shoes': 'ðŸ‘Ÿ ÐžÐ±ÑƒÐ²ÑŒ',
        'cat_clo_access': 'ðŸ§£ ÐÐºÑÐµÑÑÑƒÐ°Ñ€Ñ‹',
        'cat_tech': 'ðŸ’» Ð¢ÐµÑ…Ð½Ð¸ÐºÐ°',
        'cat_home': 'ðŸ  Ð¥Ð¾Ð·Ñ‚Ð¾Ð²Ð°Ñ€Ñ‹',
        'cat_home_furn': 'ðŸª‘ ÐœÐµÐ±ÐµÐ»ÑŒ',
        'cat_home_kitchen': 'ðŸ³ ÐšÑƒÑ…Ð½Ñ',
        'cat_cosmetics': 'ðŸ§´ ÐšÐ¾ÑÐ¼ÐµÑ‚Ð¸ÐºÐ°',
        'cat_health_med': 'ðŸ’Š Ð—Ð´Ð¾Ñ€Ð¾Ð²ÑŒÐµ',
        'cat_culture_books': 'ðŸ“š ÐšÐ½Ð¸Ð³Ð¸',
        'cat_hobbies': 'ðŸŽ® Ð¥Ð¾Ð±Ð±Ð¸',
        'cat_pets': 'ðŸ¾ Ð–Ð¸Ð²Ð¾Ñ‚Ð½Ñ‹Ðµ',
        'cat_sport': 'ðŸ‹ï¸ Ð¡Ð¿Ð¾Ñ€Ñ‚',
        'cat_auto': 'ðŸš— ÐÐ²Ñ‚Ð¾',
        'cat_food': 'ðŸŽ ÐŸÑ€Ð¾Ð´ÑƒÐºÑ‚Ñ‹',
        'cat_other': 'ðŸ“¦ ÐŸÑ€Ð¾Ñ‡ÐµÐµ',
    }

    # ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ ÐºÐ°Ð¶Ð´Ñ‹Ð¹ item Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ñ‹Ð¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÐµÐ¼ (Ñ Ñ„Ð¾Ñ‚Ð¾ ÐµÑÐ»Ð¸ ÐµÑÑ‚ÑŒ)
    import asyncio
    for idx, r in enumerate(filtered):
        item_id = r[0]
        # Ð—Ð°Ð´ÐµÑ€Ð¶ÐºÐ° Ð¼ÐµÐ¶Ð´Ñƒ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÑÐ¼Ð¸ Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ð¸Ð·Ð±ÐµÐ¶Ð°Ñ‚ÑŒ rate limit
        if idx > 0:
            await asyncio.sleep(0.5)
        name = r[1]
        brand = r[2]
        cat = r[3] or 'cat_other'
        rep_months = r[7] or r[4]
        rep_days = r[8]
        purchase = r[5]
        notes = r[9] or ''
        attrs = {}
        try:
            attrs = json.loads(r[10] or '{}')
        except json.JSONDecodeError:
            pass

        # Ð—Ð°Ð³Ð¾Ð»Ð¾Ð²Ð¾Ðº
        header = f'*{esc_md(name)}*'
        if brand:
            header += f' ({esc_md(brand)})'

        # Ð¡Ñ‚Ð°Ñ‚ÑƒÑ Ð·Ð°Ð¼ÐµÐ½Ñ‹
        status_line = ''
        rep_days = r[8]
        rep_months = r[7] or r[4]
        if (rep_months or rep_days) and purchase:
            try:
                pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                if rep_days:
                    replace_date = pd + timedelta(days=rep_days)
                else:
                    replace_date = add_months_safe(pd, rep_months)
                days = (replace_date - today).days
                if days <= 0:
                    status_line = 'ðŸ”´ *ÐŸÐžÐ Ð ÐœÐ•ÐÐ¯Ð¢Ð¬!*'
                elif days <= 30:
                    status_line = f'ðŸŸ¡ Ð—Ð°Ð¼ÐµÐ½Ð° Ñ‡ÐµÑ€ÐµÐ· *{days} Ð´Ð½.*'
                else:
                    status_line = f'ðŸŸ¢ Ð—Ð°Ð¼ÐµÐ½Ð° Ñ‡ÐµÑ€ÐµÐ· {days} Ð´Ð½.'
            except (TypeError, ValueError):
                pass

        # Ð”ÐµÑ‚Ð°Ð»Ð¸
        details = []
        cat_label = cat_names.get(cat, cat)
        details.append(f'ðŸ“‚ {cat_label}')
        if attrs.get('color'):
            details.append(f'ðŸŽ¨ Ð¦Ð²ÐµÑ‚: {attrs["color"]}')
        if attrs.get('material'):
            details.append(f'ðŸ§µ ÐœÐ°Ñ‚ÐµÑ€Ð¸Ð°Ð»: {attrs["material"]}')
        if attrs.get('description'):
            details.append(f'ðŸ“ {attrs["description"]}')
        if attrs.get('style_tags'):
            details.append(f'ðŸ·ï¸ Ð¢ÐµÐ³Ð¸: {", ".join(attrs["style_tags"])}')
        if attrs.get('estimated_price_rub'):
            details.append(f'ðŸ’° ÐžÑ†ÐµÐ½ÐºÐ°: ~{attrs["estimated_price_rub"]} â‚½')
        if notes:
            # Ð£Ð±Ð¸Ñ€Ð°ÐµÐ¼ ÑÐ»ÑƒÐ¶ÐµÐ±Ð½Ñ‹Ðµ ÑÑ‚Ñ€Ð¾ÐºÐ¸ Ð¸ Vision-Ð´Ð°Ð½Ð½Ñ‹Ðµ (ÑƒÐ¶Ðµ Ð¿Ð¾ÐºÐ°Ð·Ð°Ð½Ñ‹ Ð² attributes)
            clean_notes = notes.replace('Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ Ñ‡ÐµÑ€ÐµÐ· /add_item', '').strip()
            # Ð£Ð±Ð¸Ñ€Ð°ÐµÐ¼ ÑÑ‚Ñ€Ð¾ÐºÐ¸ Ñ Ñ†Ð²ÐµÑ‚Ð¾Ð¼, Ð¼Ð°Ñ‚ÐµÑ€Ð¸Ð°Ð»Ð¾Ð¼, Ð¾Ð¿Ð¸ÑÐ°Ð½Ð¸ÐµÐ¼, Ñ†ÐµÐ½Ð¾Ð¹ (Ð´ÑƒÐ±Ð»Ð¸ Ð¸Ð· Vision)
            for prefix in ['Ð¦Ð²ÐµÑ‚:', 'ÐœÐ°Ñ‚ÐµÑ€Ð¸Ð°Ð»:', 'ÐžÐ¿Ð¸ÑÐ°Ð½Ð¸Ðµ:', 'ÐžÑ†ÐµÐ½Ð¾Ñ‡Ð½Ð°Ñ Ñ†ÐµÐ½Ð°:']:
                clean_notes = '\n'.join(
                    line for line in clean_notes.split('\n') 
                    if not line.strip().startswith(prefix)
                ).strip()
            if clean_notes:
                details.append(f'ðŸ“‹ {clean_notes[:200]}')

        text = f'{header}\n'
        if status_line:
            text += f'{status_line}\n'
        text += '\n'.join(details)
        text += f'\n\nID: `{item_id}`'

        # Ð¤Ð¾Ñ€Ð¼Ð¸Ñ€ÑƒÐµÐ¼ ÐºÐ½Ð¾Ð¿ÐºÐ¸
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = []
        
        # ÐšÐ½Ð¾Ð¿ÐºÐ° Ñ„Ð¾Ñ‚Ð¾ ÐµÑÐ»Ð¸ ÐµÑÑ‚ÑŒ
        photo_path = photos.get(item_id)
        has_photo = photo_path and os.path.exists(photo_path)
        
        if has_photo:
            buttons.append(InlineKeyboardButton('ðŸ“· Ð¤Ð¾Ñ‚Ð¾', callback_data=f'item_photo:{item_id}'))
        
        # ÐšÐ½Ð¾Ð¿ÐºÐ° ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ñ ÐµÑÐ»Ð¸ Ð·Ð°Ð¼ÐµÐ½Ð° <30 Ð´Ð½ÐµÐ¹
        if status_line and ('ðŸ”´' in status_line or 'ðŸŸ¡' in status_line):
            buttons.append(InlineKeyboardButton('ðŸ—‘ Ð£Ð´Ð°Ð»Ð¸Ñ‚ÑŒ', callback_data=f'item_delete:{item_id}'))
        
        kb = InlineKeyboardMarkup([buttons]) if buttons else None

        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'ðŸ›’ Consumption Agent\n\n'
        'ÐšÐ¾Ð¼Ð°Ð½Ð´Ñ‹:\n'
        '/list â€” Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ Ð¿Ð¾ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸ÑÐ¼\n'
        '/alerts â€” Ð°Ð»ÐµÑ€Ñ‚Ñ‹ (Ð³Ð°Ñ€Ð°Ð½Ñ‚Ð¸Ð¸, ÑÑ€Ð¾ÐºÐ¸)\n'
        '/find_car 3Ñ‡ 80ÐºÐ¼ â€” Ð¿Ð¾Ð´Ð±Ð¾Ñ€ Ñ‚Ð°Ñ€Ð¸Ñ„Ð° ÐºÐ°Ñ€ÑˆÐµÑ€Ð¸Ð½Ð³Ð°\n'
        '/last_drives â€” Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð¿Ð¾ÐµÐ·Ð´ÐºÐ¸ ÐºÐ°Ñ€ÑˆÐµÑ€Ð¸Ð½Ð³Ð° (Ð²ÑÐµ Ð¿Ñ€Ð¾Ð²Ð°Ð¹Ð´ÐµÑ€Ñ‹)\n'
        '/debts â€” Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ° ÐºÑ€ÐµÐ´Ð¸Ñ‚Ð¾Ð² Ð¸ Ð·Ð°Ð¹Ð¼Ð¾Ð² Ð¿Ð¾ Ð¿Ð¾Ñ‡Ñ‚Ð°Ð¼ + SMS\n'
        '/fines â€” Ð½ÐµÐ¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð½Ñ‹Ðµ ÑˆÑ‚Ñ€Ð°Ñ„Ñ‹\n'
        '/dayexp â€” Ñ€Ð°ÑÑ…Ð¾Ð´Ñ‹ Ð·Ð° ÑÐµÐ³Ð¾Ð´Ð½Ñ Ñ Ñ€Ð°ÑÑˆÐ¸Ñ„Ñ€Ð¾Ð²ÐºÐ¾Ð¹\n'
        '/monthexp â€” Ñ€Ð°ÑÑ…Ð¾Ð´Ñ‹ Ð·Ð° Ð¼ÐµÑÑÑ† Ñ Ñ€Ð°ÑÑˆÐ¸Ñ„Ñ€Ð¾Ð²ÐºÐ¾Ð¹ Ð¿Ð¾ Ð´Ð½ÑÐ¼\n'
        '/warranties â€” Ð¾Ñ‚Ñ‡Ñ‘Ñ‚ Ð¿Ð¾ Ð³Ð°Ñ€Ð°Ð½Ñ‚Ð¸ÑÐ¼\n'
        '/add <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ> [<Ñ†ÐµÐ½Ð°>] [<ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ>] â€” Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ñ‚Ð¾Ð²Ð°Ñ€\n'
        '/add_photo â€” Ð·Ð°Ð³Ñ€ÑƒÐ·Ð¸Ñ‚ÑŒ Ñ„Ð¾Ñ‚Ð¾ Ñ‡ÐµÐºÐ° (OCR)\n'
        '/check â€” ÑÑ‚Ð°Ñ‚Ð¸ÑÑ‚Ð¸ÐºÐ°\n'
        '/add_item <Ð½Ð°Ð·Ð²Ð°Ð½Ð¸Ðµ> [| Ð±Ñ€ÐµÐ½Ð´ X] [| Ð·Ð°Ð¼ÐµÐ½Ð° N Ð¼ÐµÑ] â€” Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ð²ÐµÑ‰ÑŒ Ð² Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ\n'
        '/items [all|ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸Ñ] â€” Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ Ð²ÐµÑ‰ÐµÐ¹ Ð¿Ð¾ ÐºÐ°Ñ‚ÐµÐ³Ð¾Ñ€Ð¸ÑÐ¼\n'
        '/ml_last [N] â€” Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð·Ð°Ð¿Ð¸ÑÐ¸ Memory Lane\n'
        '/topic_set <ÑÐ»Ð¾Ð²Ð¾> <Ñ‚ÐµÐ¼Ð°> â€” Ð·Ð°Ð´Ð°Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ Ð´Ð»Ñ ÑÐ»Ð¾Ð²Ð°\n'
        '/topic_list [Ñ‚ÐµÐ¼Ð°] â€” Ð¿Ð¾ÐºÐ°Ð·Ð°Ñ‚ÑŒ Ð²ÑÐµ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð° Ñ‚ÐµÐ¼\n'
        '/help â€” ÑÑ‚Ð¾ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ'
    )


async def cmd_topic_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ð£ÑÑ‚Ð°Ð½Ð¾Ð²Ð¸Ñ‚ÑŒ Ñ‚ÐµÐ¼Ñƒ Ð´Ð»Ñ ÐºÐ»ÑŽÑ‡ÐµÐ²Ð¾Ð³Ð¾ ÑÐ»Ð¾Ð²Ð°: /topic_set <ÑÐ»Ð¾Ð²Ð¾> <Ñ‚ÐµÐ¼Ð°>"""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane Ð¼Ð¾Ð´ÑƒÐ»ÑŒ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½.')
        return

    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text('Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ð½Ð¸Ðµ: /topic_set <ÑÐ»Ð¾Ð²Ð¾> <Ñ‚ÐµÐ¼Ð°>\nÐÐ°Ð¿Ñ€Ð¸Ð¼ÐµÑ€: /topic_set ÐºÐ¾Ñ„ÐµÐ¼Ð¾Ð»ÐºÐ° Ð±Ñ‹Ñ‚Ð¾Ð²Ð°Ñ Ñ‚ÐµÑ…Ð½Ð¸ÐºÐ°')
        return

    keyword = ctx.args[0].lower()
    topic = ' '.join(ctx.args[1:]).lower()

    conn = get_db()
    try:
        is_new = _ml.set_topic_rule(conn, keyword, topic)
        conn.commit()
    except Exception as e:
        await update.message.reply_text(f'\u274c ÐžÑˆÐ¸Ð±ÐºÐ°: {e}')
        return
    finally:
        conn.close()

    if is_new:
        await update.message.reply_text(f'\u2705 Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð¾: Â«{keyword}Â» \u2192 Â«{topic}Â»')
    else:
        await update.message.reply_text(f'\u2705 ÐžÐ±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¾ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð¾: Â«{keyword}Â» \u2192 Â«{topic}Â»')


async def cmd_topic_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐŸÐ¾ÐºÐ°Ð·Ð°Ñ‚ÑŒ Ð²ÑÐµ Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð° Ñ‚ÐµÐ¼: /topic_list [Ñ‚ÐµÐ¼Ð°]"""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane Ð¼Ð¾Ð´ÑƒÐ»ÑŒ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½.')
        return

    topic_filter = ' '.join(ctx.args).lower() if ctx.args else None

    conn = get_db()
    try:
        rules = _ml.list_topic_rules(conn, topic_filter)
    finally:
        conn.close()

    if not rules:
        if topic_filter:
            await update.message.reply_text(f'ÐŸÑ€Ð°Ð²Ð¸Ð» Ð´Ð»Ñ Ñ‚ÐµÐ¼Ñ‹ Â«{topic_filter}Â» Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾.')
        else:
            await update.message.reply_text('ÐŸÑ€Ð°Ð²Ð¸Ð» Ð¿Ð¾ÐºÐ° Ð½ÐµÑ‚. Ð”Ð¾Ð±Ð°Ð²ÑŒÑ‚Ðµ /topic_set <ÑÐ»Ð¾Ð²Ð¾> <Ñ‚ÐµÐ¼Ð°>')
        return

    # Ð“Ñ€ÑƒÐ¿Ð¿Ð¸Ñ€ÑƒÐµÐ¼ Ð¿Ð¾ Ñ‚ÐµÐ¼Ð°Ð¼
    groups = {}
    for r in rules:
        t = r['topic']
        if t not in groups:
            groups[t] = []
        icon = '\U0001f3f7' if r['source'] == 'user' else ''
        groups[t].append(f"{icon}{r['keyword']} ({r['usage_count']})")

    lines = [f'\U0001f9f9 ÐŸÑ€Ð°Ð²Ð¸Ð»Ð° Ñ‚ÐµÐ¼ ({len(rules)}):']
    for topic in sorted(groups.keys()):
        kws = ', '.join(groups[topic])
        lines.append(f'\n\U0001f539 {topic}: {kws}')

    # Ð Ð°Ð·Ð±Ð¸Ð²Ð°ÐµÐ¼ Ð½Ð° Ñ‡Ð°ÑÑ‚Ð¸ ÐµÑÐ»Ð¸ Ð´Ð»Ð¸Ð½Ð½Ð¾
    full = '\n'.join(lines)
    if len(full) > 4000:
        for chunk in [full[i:i+4000] for i in range(0, len(full), 4000)]:
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(full)


async def cmd_ml_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐŸÐ¾ÐºÐ°Ð·Ð°Ñ‚ÑŒ Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ N Ð·Ð°Ð¿Ð¸ÑÐµÐ¹ Memory Lane (Ð¿Ð¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ 10)."""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane Ð¼Ð¾Ð´ÑƒÐ»ÑŒ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½.')
        return

    n = 10
    if ctx.args:
        try:
            n = max(1, min(50, int(ctx.args[0])))
        except ValueError:
            await update.message.reply_text('Usage: /ml_last [N=10]')
            return

    conn = get_db()
    try:
        rows = _ml.list_recent(conn, n=n)
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text(
            'Memory Lane Ð¿ÑƒÑÑ‚. ÐžÑ‚Ð¿Ñ€Ð°Ð²ÑŒ Ñ„Ð¾Ñ‚Ð¾ Ñ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑŒÑŽ Â«Ð½Ñ€Ð°Ð²Ð¸Ñ‚ÑÑÂ» Ð¸Ð»Ð¸ #Ñ…ÑÑˆÑ‚ÐµÐ³Ð¾Ð¼, '
            'Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ð´Ð¾Ð±Ð°Ð²Ð¸Ñ‚ÑŒ Ð·Ð°Ð¿Ð¸ÑÑŒ.'
        )
        return

    lines = [f'ðŸ§  ÐŸÐ¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ {len(rows)} Ð·Ð°Ð¿Ð¸ÑÐµÐ¹:']
    for r in rows:
        cap = (r['caption'] or '').strip().replace('\n', ' ')
        if len(cap) > 60:
            cap = cap[:57] + 'â€¦'
        try:
            tags = json.loads(r['style_tags'] or '[]')
        except (TypeError, ValueError):
            tags = []
        tag_str = ' '.join(f'#{t}' for t in tags) if tags else ''
        topic = r['topic'] or 'â€”'
        date = (r['created_at'] or '')[:10]
        has_photo = 'ðŸ“·' if r['media_asset_id'] else ''
        name = r['name'] or ''
        desc = (r['description'] or '')[:40] if r['description'] else ''
        name_part = f' {name}' if name else ''
        desc_part = f' â€” {desc}â€¦' if desc else ''
        lines.append(f'#{r["id"]:>3}{has_photo} {date} [{topic}]{name_part}{desc_part}'.rstrip())
    await update.message.reply_text('\n'.join(lines))

    # ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ Ð´Ð»Ñ Ð·Ð°Ð¿Ð¸ÑÐµÐ¹, Ñƒ ÐºÐ¾Ñ‚Ð¾Ñ€Ñ‹Ñ… ÐµÑÑ‚ÑŒ media_asset_id
    conn2 = get_db()
    for r in rows:
        media_asset_id = r['media_asset_id']
        if not media_asset_id:
            continue
        try:
            row = conn2.execute(
                'SELECT file_path FROM media_assets WHERE id = ?', (media_asset_id,)
            ).fetchone()
            if not row or not os.path.exists(row[0]):
                continue
            caption_lines = [f'ðŸ“Œ {r["name"]}' if r['name'] else f'#{r["id"]}']
            if r['name']:
                caption_lines[0] = f'ðŸ“Œ {r["name"]}'
            else:
                caption_lines[0] = f'#{r["id"]}'
            if r['description']:
                caption_lines.append(r['description'])
            if r['caption']:
                cap = r['caption'].strip()
                if cap != (r['name'] or '') and not cap.startswith('#'):
                    caption_lines.append(cap)
            if r['topic']:
                caption_lines.append(f'ðŸ“‚ {r["topic"]}')
            caption_lines.append(f'ðŸ•’ {str(r["created_at"])[:10]}')
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton('ðŸ—‘ Ð£Ð´Ð°Ð»Ð¸Ñ‚ÑŒ', callback_data=f'ml_delete:{r["id"]}')
            ]])
            with open(row[0], 'rb') as fh:
                await update.message.reply_photo(
                    photo=fh.read(),
                    caption='\n'.join(caption_lines),
                    reply_markup=kb
                )
        except Exception as e:
            log.warning(f'ml_last: failed to send photo for ml_id={r["id"]}: {e}')
    conn2.close()


async def cmd_set_warranty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) != 2:
        await update.message.reply_text("Usage: /set_warranty <item_id> <months>")
        return
    try:
        item_id = int(ctx.args[0])
        months = int(ctx.args[1])
        if item_id <= 0 or months <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Usage: /set_warranty <item_id> <months> (positive integers)")
        return

    conn = get_db()
    try:
        cur = conn.execute("UPDATE items SET warranty_months=? WHERE id=? AND deleted_at IS NULL", (months, item_id))
        if cur.rowcount == 0:
            await update.message.reply_text(f"âŒ Item {item_id} not found")
            return
        # Reset warranty_until so update_warranty_until() recomputes it even
        # if it was already set (the helper skips non-NULL rows).
        conn.execute("UPDATE items SET warranty_until=NULL WHERE id=?", (item_id,))
        from warranty_check import update_warranty_until
        update_warranty_until(conn)
        row = conn.execute("SELECT warranty_until FROM items WHERE id=?", (item_id,)).fetchone()
        conn.commit()
        warranty_until = row["warranty_until"] if row and row["warranty_until"] else "N/A"
        await update.message.reply_text(
            f"OK: warranty_months={months}, warranty_until={warranty_until}"
        )
    finally:
        conn.close()


async def credit_paid_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('credit_paid:'):
        return

    try:
        alert_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('âš ï¸ ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ alert id', show_alert=True)
        return

    row = get_credit_alert(alert_id)
    if not row:
        await query.answer('âš ï¸ ÐÐ»ÐµÑ€Ñ‚ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½', show_alert=True)
        return

    if row['paid_confirmed_at']:
        await query.answer('âœ… Ð£Ð¶Ðµ Ð¾Ñ‚Ð¼ÐµÑ‡ÐµÐ½Ð¾ ÐºÐ°Ðº Ð¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð¾')
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    changed = confirm_credit_alert_paid(alert_id)
    if not changed:
        await query.answer('âš ï¸ ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¾Ð±Ð½Ð¾Ð²Ð¸Ñ‚ÑŒ ÑÑ‚Ð°Ñ‚ÑƒÑ', show_alert=True)
        return

    paid_note = '\n\nâœ… <b>ÐžÑ‚Ð¼ÐµÑ‡ÐµÐ½Ð¾ ÐºÐ°Ðº Ð¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð¾ Ð²Ñ€ÑƒÑ‡Ð½ÑƒÑŽ</b>'
    base_text = html.escape((query.message.text or '').rstrip())
    new_text = base_text + paid_note
    try:
        await query.edit_message_text(
            new_text,
            parse_mode='HTML',
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await query.answer('âœ… ÐŸÐ»Ð°Ñ‚Ñ‘Ð¶ Ð¾Ñ‚Ð¼ÐµÑ‡ÐµÐ½ ÐºÐ°Ðº Ð¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð½Ñ‹Ð¹')


async def fine_paid_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº ÐºÐ½Ð¾Ð¿ÐºÐ¸ 'âœ… ÐžÐ¿Ð»Ð°Ñ‡ÐµÐ½Ð¾' Ð´Ð»Ñ ÑˆÑ‚Ñ€Ð°Ñ„Ð¾Ð²."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('fine_paid:'):
        return

    try:
        fine_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('âš ï¸ ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ id', show_alert=True)
        return

    row = get_fine(fine_id)
    if not row:
        await query.answer('âš ï¸ Ð¨Ñ‚Ñ€Ð°Ñ„ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½', show_alert=True)
        return

    if row['paid_confirmed_at']:
        await query.answer('âœ… Ð£Ð¶Ðµ Ð¾Ñ‚Ð¼ÐµÑ‡ÐµÐ½Ð¾ ÐºÐ°Ðº Ð¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð¾')
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    changed = confirm_fine_paid(fine_id)
    if not changed:
        await query.answer('âš ï¸ ÐÐµ ÑƒÐ´Ð°Ð»Ð¾ÑÑŒ Ð¾Ð±Ð½Ð¾Ð²Ð¸Ñ‚ÑŒ ÑÑ‚Ð°Ñ‚ÑƒÑ', show_alert=True)
        return

    paid_note = '\n\nâœ… <b>ÐžÑ‚Ð¼ÐµÑ‡ÐµÐ½Ð¾ ÐºÐ°Ðº Ð¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð¾</b>'
    base_text = html.escape((query.message.text or '').rstrip())
    new_text = base_text + paid_note
    try:
        await query.edit_message_text(
            new_text,
            parse_mode='HTML',
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await query.answer('âœ… Ð¨Ñ‚Ñ€Ð°Ñ„ Ð¾Ñ‚Ð¼ÐµÑ‡ÐµÐ½ ÐºÐ°Ðº Ð¾Ð¿Ð»Ð°Ñ‡ÐµÐ½Ð½Ñ‹Ð¹')


async def item_replaced_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº ÐºÐ½Ð¾Ð¿ÐºÐ¸ 'âœ… Ð—Ð°Ð¼ÐµÐ½ÐµÐ½Ð¾' Ð´Ð»Ñ Ð½Ð°Ð¿Ð¾Ð¼Ð¸Ð½Ð°Ð½Ð¸Ð¹ Ð¾ Ð·Ð°Ð¼ÐµÐ½Ðµ Ð²ÐµÑ‰ÐµÐ¹."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_replaced:'):
        return

    try:
        alert_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('âš ï¸ ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ alert id', show_alert=True)
        return

    conn = get_db()
    try:
        # ÐŸÐ¾Ð»ÑƒÑ‡Ð°ÐµÐ¼ alert Ð¸ ÑÐ²ÑÐ·Ð°Ð½Ð½Ñ‹Ð¹ item
        alert = conn.execute(
            'SELECT item_id FROM alerts WHERE id = ? AND alert_type = ?',
            (alert_id, 'replace_reminder')
        ).fetchone()
        if not alert:
            await query.answer('âš ï¸ ÐÐ»ÐµÑ€Ñ‚ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½', show_alert=True)
            return

        item_id = alert['item_id']

        # ÐŸÐ¾Ð¼ÐµÑ‡Ð°ÐµÐ¼ item ÐºÐ°Ðº Ð·Ð°Ð¼ÐµÐ½Ñ‘Ð½Ð½Ñ‹Ð¹
        conn.execute(
            "UPDATE items SET status = 'replaced', updated_at = datetime('now') WHERE id = ?",
            (item_id,)
        )
        # Ð—Ð°ÐºÑ€Ñ‹Ð²Ð°ÐµÐ¼ Ð°Ð»ÐµÑ€Ñ‚
        conn.execute(
            "UPDATE alerts SET status = 'actioned' WHERE id = ?",
            (alert_id,)
        )
        conn.commit()

        # ÐžÐ±Ð½Ð¾Ð²Ð»ÑÐµÐ¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ
        replaced_note = '\n\nâœ… <b>Ð—Ð°Ð¼ÐµÐ½ÐµÐ½Ð¾</b>'
        base_text = html.escape((query.message.text or '').rstrip())
        new_text = base_text + replaced_note
        try:
            await query.edit_message_text(
                new_text,
                parse_mode='HTML',
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        await query.answer('âœ… ÐžÑ‚Ð¼ÐµÑ‡ÐµÐ½Ð¾ ÐºÐ°Ðº Ð·Ð°Ð¼ÐµÐ½Ñ‘Ð½Ð½Ð¾Ðµ')
    except Exception as e:
        log.warning(f'item_replaced_callback failed: {e}')
        await query.answer('âš ï¸ ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ñ€Ð¸ Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ð¸', show_alert=True)
    finally:
        conn.close()


async def item_delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº ÐºÐ½Ð¾Ð¿ÐºÐ¸ 'ðŸ—‘ Ð£Ð´Ð°Ð»Ð¸Ñ‚ÑŒ' Ð´Ð»Ñ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ñ item Ð¸Ð· Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€Ñ."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_delete:'):
        return

    try:
        item_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('âš ï¸ ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ item id', show_alert=True)
        return

    conn = get_db()
    try:
        # Soft delete â€” Ð¿Ð¾Ð¼ÐµÑ‡Ð°ÐµÐ¼ deleted_at
        conn.execute(
            "UPDATE items SET deleted_at = datetime('now'), status = 'disposed' WHERE id = ?",
            (item_id,)
        )
        conn.commit()

        # ÐžÐ±Ð½Ð¾Ð²Ð»ÑÐµÐ¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ
        deleted_note = '\n\nðŸ—‘ <b>Ð£Ð´Ð°Ð»ÐµÐ½Ð¾ Ð¸Ð· Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€Ñ</b>'
        base_text = html.escape((query.message.text or '').rstrip())
        new_text = base_text + deleted_note
        try:
            await query.edit_message_text(
                new_text,
                parse_mode='HTML',
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        await query.answer('ðŸ—‘ Ð£Ð´Ð°Ð»ÐµÐ½Ð¾')
    except Exception as e:
        log.warning(f'item_delete_callback failed: {e}')
        await query.answer('âš ï¸ ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ñ€Ð¸ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ð¸', show_alert=True)
    finally:
        conn.close()


async def ml_delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº ÐºÐ½Ð¾Ð¿ÐºÐ¸ 'ðŸ—‘ Ð£Ð´Ð°Ð»Ð¸Ñ‚ÑŒ' Ð´Ð»Ñ Memory Lane Ð·Ð°Ð¿Ð¸ÑÐµÐ¹ Ð² /ml_last."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('ml_delete:'):
        return

    try:
        ml_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('âš ï¸ ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ id', show_alert=True)
        return

    conn = get_db()
    try:
        # ÐŸÐ¾Ð»ÑƒÑ‡Ð°ÐµÐ¼ media_asset_id Ð´Ð»Ñ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ñ
        row = conn.execute(
            'SELECT media_asset_id FROM memory_lane_items WHERE id = ?', (ml_id,)
        ).fetchone()
        if not row:
            await query.answer('âš ï¸ Ð—Ð°Ð¿Ð¸ÑÑŒ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð°', show_alert=True)
            return

        media_asset_id = row[0]

        # Ð£Ð´Ð°Ð»ÑÐµÐ¼ Ð·Ð°Ð¿Ð¸ÑÑŒ Ð¸Ð· memory_lane_items
        conn.execute('DELETE FROM memory_lane_items WHERE id = ?', (ml_id,))

        # Ð£Ð´Ð°Ð»ÑÐµÐ¼ ÑÐ²ÑÐ·Ð°Ð½Ð½Ñ‹Ð¹ media_asset (Ñ„Ð°Ð¹Ð» + Ð·Ð°Ð¿Ð¸ÑÑŒ Ð² Ð‘Ð”)
        if media_asset_id:
            ma_row = conn.execute(
                'SELECT file_path FROM media_assets WHERE id = ?', (media_asset_id,)
            ).fetchone()
            conn.execute('DELETE FROM media_assets WHERE id = ?', (media_asset_id,))
            if ma_row and os.path.exists(ma_row[0]):
                try:
                    os.remove(ma_row[0])
                except Exception:
                    pass

        conn.commit()

        # ÐžÐ±Ð½Ð¾Ð²Ð»ÑÐµÐ¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ (ÑƒÐ±Ð¸Ñ€Ð°ÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾, Ð¼ÐµÐ½ÑÐµÐ¼ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑŒ)
        try:
            await query.edit_message_caption(
                caption=f'ðŸ—‘ Ð—Ð°Ð¿Ð¸ÑÑŒ #{ml_id} ÑƒÐ´Ð°Ð»ÐµÐ½Ð°',
                reply_markup=None
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

        await query.answer('ðŸ—‘ Ð—Ð°Ð¿Ð¸ÑÑŒ ÑƒÐ´Ð°Ð»ÐµÐ½Ð°')
    except Exception as e:
        log.warning(f'ml_delete_callback failed: {e}')
        await query.answer('âš ï¸ ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ñ€Ð¸ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ð¸', show_alert=True)
    finally:
        conn.close()


async def vision_confirm_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº ÐºÐ½Ð¾Ð¿ÐºÐ¸ 'âœ… ÐŸÐ¾Ð´Ñ‚Ð²ÐµÑ€Ð´Ð¸Ñ‚ÑŒ' â€” Ñ‚Ð¾Ð²Ð°Ñ€ ÑƒÐ¶Ðµ Ð² Ð‘Ð”, Ð¿Ñ€Ð¾ÑÐ¸Ð¼ Ð´Ð¾Ð¿. Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    pending = ctx.user_data.get('vision_pending')
    if not pending:
        await query.answer('âš ï¸ Ð”Ð°Ð½Ð½Ñ‹Ðµ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ñ‹', show_alert=True)
        return

    # Ð£Ð±Ð¸Ñ€Ð°ÐµÐ¼ ÐºÐ½Ð¾Ð¿ÐºÐ¸ Ð¸ Ð¾Ð±Ð½Ð¾Ð²Ð»ÑÐµÐ¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await query.edit_message_text(
        query.message.text + '\n\nâœ… Ð”Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¾ Ð² Ð¸Ð½Ð²ÐµÐ½Ñ‚Ð°Ñ€ÑŒ',
        reply_markup=None
    )
    await query.answer('âœ… Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÐµÐ½Ð¾')

    # Ð—Ð°Ð¿Ñ€Ð°ÑˆÐ¸Ð²Ð°ÐµÐ¼ Ð´Ð¾Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒÐ½ÑƒÑŽ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ Ñ‡ÐµÑ€ÐµÐ· ForceReply
    from telegram import ForceReply
    await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text='ðŸ“ Ð’Ð²ÐµÐ´Ð¸Ñ‚Ðµ Ð´Ð¾Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒÐ½ÑƒÑŽ Ð¸Ð½Ñ„Ð¾Ñ€Ð¼Ð°Ñ†Ð¸ÑŽ Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ðµ (Ð±Ñ€ÐµÐ½Ð´, Ñ€Ð°Ð·Ð¼ÐµÑ€, Ð¼Ð°Ñ‚ÐµÑ€Ð¸Ð°Ð»):',
        reply_markup=ForceReply(selective=True),
        reply_to_message_id=query.message.message_id
    )
    # Ð¡Ð¾Ñ…Ñ€Ð°Ð½ÑÐµÐ¼ item_id Ð´Ð»Ñ Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚ÐºÐ¸ Ð¾Ñ‚Ð²ÐµÑ‚Ð°
    ctx.user_data['vision_awaiting_notes'] = pending.get('item_id')


async def vision_reject_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº ÐºÐ½Ð¾Ð¿ÐºÐ¸ 'âŒ ÐžÑ‚ÐºÐ»Ð¾Ð½Ð¸Ñ‚ÑŒ' â€” ÑƒÐ´Ð°Ð»ÑÐµÑ‚ Ñ‚Ð¾Ð²Ð°Ñ€ Ð¸Ð· Ð‘Ð” Ð¸ Ñ„Ð¾Ñ‚Ð¾."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    pending = ctx.user_data.pop('vision_pending', None)
    if not pending:
        await query.answer('âš ï¸ Ð”Ð°Ð½Ð½Ñ‹Ðµ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ñ‹', show_alert=True)
        return

    item_id = pending.get('item_id')
    asset_id = pending.get('asset_id')
    receipt_path = pending.get('receipt_path')

    conn = get_db()
    try:
        # Ð£Ð´Ð°Ð»ÑÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ Ð¸Ð· media_assets
        if asset_id:
            ma_row = conn.execute('SELECT file_path FROM media_assets WHERE id = ?', (asset_id,)).fetchone()
            conn.execute('DELETE FROM media_assets WHERE id = ?', (asset_id,))
            if ma_row and os.path.exists(ma_row[0]):
                try:
                    os.remove(ma_row[0])
                except Exception:
                    pass

        # Ð£Ð´Ð°Ð»ÑÐµÐ¼ ÑÐ²ÑÐ·ÑŒ item_photos
        if item_id:
            conn.execute('DELETE FROM item_photos WHERE item_id = ?', (item_id,))
            # Soft delete item
            conn.execute(
                "UPDATE items SET deleted_at = datetime('now'), status = 'disposed' WHERE id = ?",
                (item_id,)
            )

        conn.commit()

        # Ð£Ð´Ð°Ð»ÑÐµÐ¼ Ð²Ñ€ÐµÐ¼ÐµÐ½Ð½Ñ‹Ð¹ Ñ„Ð°Ð¹Ð»
        if receipt_path and os.path.exists(receipt_path):
            try:
                os.remove(receipt_path)
            except Exception:
                pass

        # Ð£Ð±Ð¸Ñ€Ð°ÐµÐ¼ ÐºÐ½Ð¾Ð¿ÐºÐ¸ Ð¸ Ð¾Ð±Ð½Ð¾Ð²Ð»ÑÐµÐ¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        await query.edit_message_text(
            query.message.text + '\n\nâŒ ÐžÑ‚ÐºÐ»Ð¾Ð½ÐµÐ½Ð¾ Ð¸ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¾',
            reply_markup=None
        )
        await query.answer('âŒ Ð£Ð´Ð°Ð»ÐµÐ½Ð¾')
    except Exception as e:
        log.warning(f'vision_reject_callback failed: {e}')
        await query.answer('âš ï¸ ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ñ€Ð¸ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ð¸', show_alert=True)
    finally:
        conn.close()


async def item_photo_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ÐžÐ±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸Ðº ÐºÐ½Ð¾Ð¿ÐºÐ¸ 'ðŸ“· Ð¤Ð¾Ñ‚Ð¾' â€” Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÑ‚ Ñ„Ð¾Ñ‚Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ð°."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_photo:'):
        return

    try:
        item_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('âš ï¸ ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ item id', show_alert=True)
        return

    # Ð—Ð°Ð³Ñ€ÑƒÐ¶Ð°ÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ Ð¸Ð· Ð‘Ð”
    conn = get_db()
    try:
        row = conn.execute('''
            SELECT ma.file_path 
            FROM item_photos ip
            JOIN media_assets ma ON ip.media_asset_id = ma.id
            WHERE ip.item_id = ? LIMIT 1
        ''', (item_id,)).fetchone()
        if not row:
            await query.answer('ðŸ“­ Ð¤Ð¾Ñ‚Ð¾ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾', show_alert=True)
            return

        photo_path = row[0]
        if not os.path.exists(photo_path):
            await query.answer('ðŸ“­ Ð¤Ð¾Ñ‚Ð¾ Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾', show_alert=True)
            return

        # ÐžÑ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ Ñ„Ð¾Ñ‚Ð¾ Ð¾Ñ‚Ð²ÐµÑ‚Ð¾Ð¼ Ð½Ð° ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ
        with open(photo_path, 'rb') as f:
            await ctx.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=f.read(),
                caption=f'ðŸ“· Ð¤Ð¾Ñ‚Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ð° ID: {item_id}'
            )
        await query.answer('ðŸ“· Ð¤Ð¾Ñ‚Ð¾ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð»ÐµÐ½Ð¾')
    except Exception as e:
        log.warning(f'item_photo_callback failed: {e}')
        await query.answer('âš ï¸ ÐžÑˆÐ¸Ð±ÐºÐ° Ð¿Ñ€Ð¸ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²ÐºÐµ Ñ„Ð¾Ñ‚Ð¾', show_alert=True)
    finally:
        conn.close()


def add_authorized_handler(app: Application, handler):
    """ÐžÐ±Ð¾Ñ€Ð°Ñ‡Ð¸Ð²Ð°ÐµÑ‚ handler Ð¿Ñ€Ð¾Ð²ÐµÑ€ÐºÐ¾Ð¹ Ð´Ð¾ÑÑ‚ÑƒÐ¿Ð° Ð¿ÐµÑ€ÐµÐ´ Ð´Ð¾Ð±Ð°Ð²Ð»ÐµÐ½Ð¸ÐµÐ¼ Ð² Ð¿Ñ€Ð¸Ð»Ð¾Ð¶ÐµÐ½Ð¸Ðµ."""
    original_callback = handler.callback

    async def deny_access(update: Update):
        chat = update.effective_chat if update else None
        chat_id = chat.id if chat else None
        log.warning('Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½ Ð´Ð»Ñ chat_id=%s', chat_id)

        if getattr(update, 'callback_query', None):
            await update.callback_query.answer('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½', show_alert=True)
            return

        message = getattr(update, 'effective_message', None)
        if message is not None:
            await message.reply_text('âŒ Ð”Ð¾ÑÑ‚ÑƒÐ¿ Ð·Ð°Ð¿Ñ€ÐµÑ‰Ñ‘Ð½.')

    async def wrapped_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat if update else None
        if chat is None or chat.id not in ALLOWED_CHAT_IDS:
            await deny_access(update)
            return
        return await original_callback(update, ctx)

    handler.callback = wrapped_callback
    app.add_handler(handler)

def main():
    # Ð˜Ð½Ð¸Ñ†Ð¸Ð°Ð»Ð¸Ð·Ð¸Ñ€ÑƒÐµÐ¼ schema memory_lane (ÑÐ¾Ð·Ð´Ð°Ñ‘Ð¼ Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ñ‹ topic_rules Ð¸ Ð´Ñ€., ÐµÑÐ»Ð¸ ÐµÑ‰Ñ‘ Ð½ÐµÑ‚)
    try:
        import memory_lane as _ml
        conn = get_db()
        _ml.ensure_memory_lane_schema(conn)
        conn.close()
    except Exception as e:
        log.warning(f'memory_lane schema init failed: {e}')

    if not TOKEN:
        print('âŒ Ð£ÐºÐ°Ð¶Ð¸Ñ‚Ðµ CONSUMPTION_BOT_TOKEN')
        print('   export CONSUMPTION_BOT_TOKEN=...')
        sys.exit(1)
    app = Application.builder().token(TOKEN).build()
    add_authorized_handler(app, CommandHandler('start', start))
    add_authorized_handler(app, CommandHandler('list', cmd_list))
    add_authorized_handler(app, CommandHandler('alerts', cmd_alerts))
    add_authorized_handler(app, CommandHandler('parse', cmd_parse))
    add_authorized_handler(app, CommandHandler('check', cmd_check))
    add_authorized_handler(app, CommandHandler('last_drives', cmd_last_drives))
    add_authorized_handler(app, CommandHandler('find_car', cmd_find_car))
    add_authorized_handler(app, CommandHandler('add', cmd_add))
    add_authorized_handler(app, CommandHandler('add_item', cmd_add_item))
    add_authorized_handler(app, CommandHandler('items', cmd_items))
    add_authorized_handler(app, CommandHandler('items_full', cmd_items_full))
    add_authorized_handler(app, CommandHandler('add_photo', add_photo))
    add_authorized_handler(app, CommandHandler('debts', cmd_debts))
    add_authorized_handler(app, CommandHandler('fines', cmd_fines))
    add_authorized_handler(app, CommandHandler('dayexp', cmd_dayexp))
    add_authorized_handler(app, CommandHandler('monthexp', cmd_monthexp))
    add_authorized_handler(app, CommandHandler('warranties', cmd_warranties))
    add_authorized_handler(app, CommandHandler('set_warranty', cmd_set_warranty))
    add_authorized_handler(app, CommandHandler('ml_last', cmd_ml_last))
    add_authorized_handler(app, CommandHandler('topic_set', cmd_topic_set))
    add_authorized_handler(app, CommandHandler('topic_list', cmd_topic_list))
    add_authorized_handler(app, CommandHandler('help', cmd_help))
    add_authorized_handler(app, MessageHandler(filters.PHOTO, photo_handler))
    add_authorized_handler(app, MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    add_authorized_handler(app, CallbackQueryHandler(credit_paid_callback, pattern=r'^credit_paid:\d+$'))
    add_authorized_handler(app, CallbackQueryHandler(fine_paid_callback, pattern=r'^fine_paid:\d+$'))
    add_authorized_handler(app, CallbackQueryHandler(item_replaced_callback, pattern=r'^item_replaced:\d+$'))
    add_authorized_handler(app, CallbackQueryHandler(item_delete_callback, pattern=r'^item_delete:\d+$'))
    add_authorized_handler(app, CallbackQueryHandler(item_photo_callback, pattern=r'^item_photo:\d+$'))
    add_authorized_handler(app, CallbackQueryHandler(ml_delete_callback, pattern=r'^ml_delete:\d+$'))
    add_authorized_handler(app, CallbackQueryHandler(vision_confirm_callback, pattern=r'^vision_confirm$'))
    add_authorized_handler(app, CallbackQueryHandler(vision_reject_callback, pattern=r'^vision_reject$'))

    # Generate alerts once at startup
    gen = generate_alerts()

    # Schedule daily checks at 09:00 local server time when JobQueue is available
    from datetime import time as dt_time
    if app.job_queue is not None:
        app.job_queue.run_daily(
            run_daily_alert_job,
            time=dt_time(hour=9, minute=0, second=0),
            name='daily_alert_checks'
        )
    else:
        log.warning('JobQueue is unavailable; skipping in-process daily schedule')

    log.info(f'Bot started, polling... (alerts at startup: {gen})')
    app.run_polling()

if __name__ == '__main__':
    main()
