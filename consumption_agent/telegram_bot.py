#!/usr/bin/env python3
"""
Consumption Agent Telegram Bot
Команды:
  /start — приветствие
  /list  — инвентарь по категориям
  /alerts — активные алерты
  /add <название> [<цена>] [<категория>] — добавить товар
  /check — статистика
  /help — справка

Запуск: CONSUMPTION_BOT_TOKEN=xxx python3 telegram_bot.py
"""

import logging, os, sys, re, sqlite3, json, subprocess, tempfile, time, html, traceback, random
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import urlopen, Request

def get_db_with_retry(max_retries=3, backoff_base=0.5):
    """Подключение к БД с retry при блокировке (database is locked)."""
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
    """Выполнение запроса с retry при блокировке."""
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
    """Добавляет месяцы к дате без падения на 29/30/31 числе."""
    months = int(months)
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def parse_drive_request(text: str):
    """Парсит '3ч 80км' или '2 часа 60 км'"""
    hours = None
    km = None
    t = text.lower()
    h_match = re.search(r'(\d+)[\s]*(?:ч|час|часа|часов|h)', t)
    k_match = re.search(r'(\d+)[\s]*(?:км|km)', t)
    if h_match:
        hours = float(h_match.group(1))
    if k_match:
        km = float(k_match.group(1))
    return hours, km


def calculate_drive_cost(tariff, hours, km):
    """Расчёт стоимости поездки по тарифу провайдера."""
    km_rate = tariff['km_rate'] or 0
    rate_type = tariff['rate_type']

    if rate_type == 'flat_km':
        # Фиксированный тариф (сутки/часы) + стоимость за км
        base = (tariff['hourly_rate'] or 0) + km * km_rate
    else:
        # Поминутный/почасовой тариф + стоимость за км
        h_rate = tariff['hourly_rate'] or 0
        base = h_rate * hours + km * km_rate

    return max(round(base, -1), 500)  # округляем до 10₽, минимум 500₽

from telegram import Update, PhotoSize, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Новый модуль категоризации (Шаг 5 рефакторинга)
try:
    from consumption.categorize import categorize as auto_categorize, slug_to_cat_id
except ImportError:
    auto_categorize = lambda n: None
    slug_to_cat_id = lambda s: None

DB_PATH = os.path.join(SCRIPT_DIR, 'consumption.db')
RECEIPTS_DIR = os.path.join(SCRIPT_DIR, 'receipts')
Path(RECEIPTS_DIR).mkdir(exist_ok=True)
TOKEN = os.environ.get('CONSUMPTION_BOT_TOKEN', '')
OWNER_CHAT_ID = int(os.environ.get('OWNER_CHAT_ID', '1477860192'))


def get_credit_alert(alert_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT id, sender_name, payment_date, payment_amount, paid_confirmed_at FROM credit_alerts WHERE id = ?',
        (alert_id,)
    ).fetchone()
    conn.close()
    return row


def get_fine(fine_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT id, type, number, amount, description, vehicle, fine_date, vendor, paid_confirmed_at FROM fines WHERE id = ?',
        (fine_id,)
    ).fetchone()
    conn.close()
    return row


def confirm_fine_paid(fine_id: int, via: str = 'telegram_button') -> bool:
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
        line = re.sub(r'[^\w\s.,%₽€$£/#:-]', ' ', raw_line, flags=re.UNICODE)
        line = re.sub(r'\s+', ' ', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


def _ocr_crop(image_path: str, box_ratio: tuple[float, float, float, float], lang: str = 'eng', psm: str = '6') -> str:
    """OCR helper для отдельных зон бирки."""
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
    cyr_words = len(re.findall(r'\b[А-Яа-яЁё]{3,}\b', text))
    markers = len(re.findall(r'[₽€$£]|\b(?:EUR|USD|RUB|SIZE|TAGLIA|ФН|ФП|ИТОГО)\b', text, flags=re.I))
    score = digits + latin_words * 3 + cyr_words * 2 + markers * 8

    # Очень сильные сигналы настоящего чека/бирки. Это защищает чеки от выбора шумного OCR-варианта.
    for kw in ['кассовый чек', 'фискальный', 'фн', 'фп', 'итог', 'безналичными']:
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
    """Определяет тип фото: 'receipt', 'tag' или 'unknown'"""
    text = (ocr_text or '').lower()
    receipt_score = 0
    tag_score = 0

    if any(kw in text for kw in ['кассовый чек', 'фискальный', 'фн', 'фп', 'итого', 'сдача', 'наличными', 'безналич']):
        receipt_score += 4
    if '₽' in text or 'руб' in text:
        receipt_score += 2
    if len(re.findall(r'\d+[.,]\d{2}\s*₽?', text)) >= 2:
        receipt_score += 2

    if any(kw.lower() in text for kw in ['€', '$', 'eur', 'usd', 'gbp', 'hkd', 'multicol', 'taglia', 'size', 'article', 'camicie', 'camicia']):
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
    """Пытается достать размер из правой части бирки (часто число в рамке)."""
    try:
        from PIL import Image, ImageOps, ImageEnhance
        img = Image.open(image_path)
        w, h = img.size
        # Правая центральная зона — типичное место размера на fashion-бирках.
        boxes = [
            # Узкий crop по рамке размера справа.
            (int(w * 0.63), int(h * 0.42), int(w * 0.81), int(h * 0.58)),
            (int(w * 0.62), int(h * 0.38), int(w * 0.82), int(h * 0.60)),
            (int(w * 0.60), int(h * 0.35), int(w * 0.84), int(h * 0.62)),
            # Более широкие fallback-зоны.
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
    """Парсит строки чека Ozon / любой формат.
    Возвращает список товаров: [{name, price, qty, total}, ...].
    """
    items = []
    lines = (text or '').split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue

        # Формат Ozon: название на 1-2 строках, затем "1 x ЦЕНА" на следующей
        # Ищем строку вида "1 x 123,45" или "1×123,45"
        qty_price_match = re.search(r'^(\d+)\s*[x×]\s*([\d]+[.,]\d{2})$', line)
        if qty_price_match:
            qty = int(qty_price_match.group(1))
            price = float(qty_price_match.group(2).replace(',', '.'))
            total = qty * price
            # Название — предыдущая непустая строка (может быть 1-2 строки)
            # Ищем строки с названием (не цифровые, без 'ИТОГ', 'вт.ч', 'НДС')
            name_parts = []
            for j in range(i - 2, max(i - 5, -1), -1):
                if j < 0:
                    break
                prev = lines[j].strip()
                if not prev:
                    continue
                if re.search(r'^[\d,.#]+$', prev):
                    continue
                if re.search(r'ИТОГ|вт\.ч|НДС|HOC|расчет|Зачет|ФН:|PH ККТ|ФД:|ФПД|Сайт|ИНН|Код маркировки|Результат|Курьер|Доставк|Полный', prev):
                    continue
                if len(prev) < 3:
                    continue
                # Фильтр мусорных строк OCR
                # Фильтр мусорных строк OCR (чеки Ozon с шумами)
                if len(prev) > 5:
                    # Повторяющиеся символы
                    repeated_single = max(prev.count(c) for c in set(prev))
                    if repeated_single > len(prev) * 0.5:
                        continue
                    # Строка состоит в основном из символов шума
                    nonsense = sum(prev.count(c) for c in 'eEиcSChHaAкКВuUаa')
                    if nonsense > len(prev) * 0.6:
                        continue
                    # Уникальных символов мало — это шум
                    if len(prev) > 10 and len(set(prev)) < 6:
                        continue
                    # Строка начинается с мусора OCR (заглавные A C I e и т.д.)
                    if re.match(r'^[A-Za-z]{2,5}\s+[A-Za-z]', prev) and len(prev) > 15:
                        continue
                    # В строке больше 3 латинских заглавных подряд — мусор OCR
                    if re.search(r'[A-Z]{3,}', prev):
                        continue
                name_parts.insert(0, prev)
                # Если строка достаточно длинная — это название, не ищем дальше
                if len(prev) > 15 and prev.count('детал') + prev.count('игруш') + prev.count('набор') + prev.count('совместим') > 0:
                    # Для Ozon: название может быть на 2 строках, берём обе
                    pass
                elif len(prev) > 35 or prev.count(' ') > 3:
                    break

            name = ' '.join(name_parts) if name_parts else f'tовар {len(items) + 1}'
            items.append({'name': name, 'price': price, 'qty': qty, 'total': total})
            continue

        # Стандартный формат: "Название товара" 123.45 ₽
        m = re.search(r'(.{3,60}?)\s+(\d+[.,]\d{2})\s*₽', line)
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
    # Часто regex захватывает хвосты JSON/HTML после &quot;
    url = url.split('&quot;')[0].split('"')[0].split("'")[0].strip()
    return url


def find_product_image_urls(query: str) -> dict:
    """Best-effort: по 1-3 картинкам из Bing, Yandex, Pinterest."""
    result = {}
    q = quote_plus(query)

    # --- Bing: собираем несколько murl, берём первые 2 непохожие ---
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

    # --- Yandex: img_href (оригинал) или avatars thumbnail ---
    try:
        data = _fetch_html(f'https://yandex.ru/images/search?text={q}')
        # Сначала ищем оригиналы
        img_hrefs = re.findall(r'"img_href":"(https?:\\/\\/[^"\\]+(?:\\.[^"\\]+)*)"', data)
        if img_hrefs:
            result['Yandex'] = _clean_image_url(img_hrefs[0])
        if not result.get('Yandex'):
            thumbs = re.findall(r'https://avatars\.mds\.yandex\.net/[^"<\\]+', data)
            if thumbs:
                result['Yandex'] = _clean_image_url(thumbs[0])
    except Exception as e:
        log.warning(f"Yandex image search failed: {e}")

    # --- Pinterest: часто есть прямые URL в og:image ---
    try:
        data = _fetch_html(f'https://www.pinterest.com/search/pins/?q={q}')
        # Pinterest отдаёт JSON в <script> с pin-images
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

    # Google отказался от прямых URL. Вместо него пишем ссылку на поиск.
    if not result:
        result['Google'] = f'https://www.google.com/search?tbm=isch&q={q}'

    return result


def search_product_info_gemini(brand: str, article: str, barcode: str = None) -> dict:
    """Ищет информацию о товаре через Gemini API по данным бирки."""
    try:
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            log.warning('GEMINI_API_KEY not set, skipping Gemini search')
            return {}
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        query_parts = [f'Бренд: {brand}']
        if article:
            query_parts.append(f'Артикул: {article}')
        if barcode:
            query_parts.append(f'Штрихкод: {barcode}')
        
        prompt = f"""Найди информацию о товаре одежды по данным бирки:
{'\n'.join(query_parts)}

Верни результат в формате JSON:
{{
  "name": "название товара",
  "category": "категория (одежда/обувь/аксессуары)",
  "description": "описание",
  "color": "цвет",
  "material": "материал",
  "price_rub": "цена в рублях (число или null)",
  "image_url": "URL фото товара или null",
  "product_url": "URL страницы товара или null"
}}

Если не нашёл информацию, верни пустые значения."""
        
        response = model.generate_content(prompt)
        text = response.text
        
        # Извлекаем JSON из ответа
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
    """Извлекает данные с бирки одежды."""
    text = ocr_text or ''
    if image_path:
        # Дополнительные OCR-зоны: вся наклейка, артикул, цена. Особенно помогает Massimo Dutti.
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

    # Спец-кейс: стилизованный логотип Massimo Dutti OCR часто читает как MOSSI/MAR... DUTT.
    if re.search(r'(MASS|MOSSI|MOSS\w*|MAR\w*)\s*(IMO|I|WIO|WIO)?\s+DUTT\w*', upper_text):
        result['brand'] = 'MASSIMO DUTTI'

    for brand in TAG_BRANDS:
        if result['brand']:
            break
        if brand in upper_text:
            result['brand'] = brand
            break

    # Не угадываем бренд по первому латинскому слову: OCR часто даёт мусор вроде CNI/MU.
    # Бренд ставим только из проверенного словаря TAG_BRANDS.

    art = re.search(r'\b(\d{4,6})[ /-](\d{6,10})\b', upper_text)
    if art:
        result['article'] = f"{art.group(1)}/{art.group(2)}"
    else:
        art3 = re.search(r'\b(\d{4})[ /-](\d{3})[ /-](\d{3})(?:\s+\d{1,2})?\b', upper_text)
        if art3:
            result['article'] = f"{art3.group(1)}/{art3.group(2)}/{art3.group(3)}"
    if not result['article']:
        # fallback на 8-14 цифр, но не если это цена
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
            r'(?:SIZE|TAGLIA|РАЗМЕР)[:\s]*(XXXL|XXL|XL|XS|S|M|L|3[8-9]|4[0-9]|5[0-4])\b',
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
        r'([€$£])\s*(\d+[.,]\d{2})',
        r'(\d+[.,]\d{2})\s*(EUR|USD|GBP|HKD|€|\$|£)\b',
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
                if curr in {'€', 'EUR'}:
                    currency = 'EUR'
                elif curr in {'$', 'USD'}:
                    currency = 'USD'
                elif curr in {'£', 'GBP'}:
                    currency = 'GBP'
                elif curr == 'HKD':
                    currency = 'HKD'
            elif 'GBP' in upper_text:
                currency = 'GBP'
            elif '€' in upper_text or ' EUR' in upper_text:
                currency = 'EUR'
            price_candidates.append((amount, currency))

    if price_candidates:
        # На плохом OCR первая цифра часто теряется (64.90 -> 4.90), поэтому берём максимальную разумную цену.
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
    # 1. Гарантии и сроки годности (warranty_check)
    try:
        from warranty_check import run_daily_alert_checks
        conn = get_db()
        total = run_daily_alert_checks(conn)
        conn.close()
    except Exception as e:
        log.warning(f"generate_alerts (warranty) failed: {e}")

    # 2. Напоминания о замене вещей
    try:
        total += generate_replace_alerts()
    except Exception as e:
        log.warning(f"generate_alerts (replace) failed: {e}")

    if total:
        log.info(f"Generated {total} alerts total")
    return total


def generate_replace_alerts() -> int:
    """Generate replacement reminders for items with replace_after_months.

    Алерт создаётся за 30 дней до даты замены.
    Повторно не создаётся, если уже есть pending/sent алерт за этот item
    за последние 7 дней.
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

            # Алерт только если замена через ≤30 дней или уже просрочена
            if days_left > 30:
                continue

            # Проверяем, нет ли недавнего алерта (pending/sent за 7 дней)
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
                title = f'🔴 Пора менять: {name}'
                msg = f'Срок замены истёк {-days_left} дн. назад ({replace_date})'
            else:
                title = f'🔄 Скоро замена: {name}'
                msg = f'Осталось {days_left} дн. до замены ({replace_date})'
            if brand:
                msg += f'\nБренд: {brand}'

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

            # Для replace_reminder добавляем кнопку "✅ Заменено"
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            if row['alert_type'] == 'replace_reminder':
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton('✅ Заменено', callback_data=f'item_replaced:{row["id"]}')
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
    for i in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and i < max_retries - 1:
                time.sleep(delay * (2 ** i))  # Экспоненциальная задержка
                continue
            raise

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🛒 Привет, это Consumption Agent.\n'
        'Для списка команд: /help'
    )

async def add_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '📸 Отправьте фото чека (одно фото за раз).\n'
        'Я распознаю текст и добавлю товары в инвентарь.'
    )


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений — для доп. информации после vision_confirm."""
    text = (update.message.text or '').strip()
    
    # Проверяем, ждём ли доп. информацию о товаре
    item_id = ctx.user_data.pop('vision_awaiting_notes', None)
    if item_id:
        # Если текст пустой — не сохраняем ничего
        if not text:
            await update.message.reply_text('ℹ️ Дополнительная информация не добавлена')
            return
        
        # Ограничиваем 50 символами
        notes_text = text[:50]
        conn = get_db()
        try:
            # Добавляем к существующим notes
            row = conn.execute('SELECT notes FROM items WHERE id = ?', (item_id,)).fetchone()
            if row:
                existing_notes = row[0] or ''
                new_notes = existing_notes + '\nДоп. информация: ' + notes_text if existing_notes else 'Доп. информация: ' + notes_text
                conn.execute('UPDATE items SET notes = ? WHERE id = ?', (new_notes, item_id))
                conn.commit()
                await update.message.reply_text(f'✅ Дополнительная информация сохранена: {notes_text}')
                return
        except Exception as e:
            log.warning(f'text_handler: failed to save notes: {e}')
        finally:
            conn.close()
    
    # Если не ждём доп. информацию — игнорируем (или можно добавить другую логику)
    # Пока просто не отвечаем на обычные текстовые сообщения


async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text('❌ Это не фото. Пожалуйста, отправьте изображение.')
        return

    # Get the highest resolution photo
    photo: PhotoSize = update.message.photo[-1]
    caption = update.message.caption or ''
    log.info(f'photo_handler: message_id={update.message.message_id}, caption={caption!r}')

    # === Редирект: /add_item + фото ===
    # Если caption начинается с /add_item — перенаправляем в cmd_add_item
    if caption.strip().startswith('/add_item'):
        log.info(f'photo_handler: redirecting to cmd_add_item, args={caption.strip().split()[1:]}')
        ctx.args = caption.strip().split()[1:]
        await cmd_add_item(update, ctx)
        return

    # Если caption выглядит как описание вещи (есть бренд или срок замены)
    # — тоже перенаправляем в cmd_add_item
    if caption.strip():
        from brand_parser import parse_brand_and_name
        bp = parse_brand_and_name(caption)
        if bp['name'] and (bp['brand'] or bp['replace_months']):
            log.info(f'photo_handler: redirecting to cmd_add_item (detected item description), args={caption.strip().split()}')
            ctx.args = caption.strip().split()
            await cmd_add_item(update, ctx)
            return

    # Phase B: Memory Lane fast path — если в caption есть триггер-слова или
    # хэштеги, сохраняем в memory_lane_items + media_assets и завершаем,
    # не попадая в OCR/QR-пайплайн чеков.
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

                # Обогащаем через Vision API — тема, теги, описание
                vision_info = {}
                try:
                    from vision_item import enrich_memory_lane
                    vision_info = enrich_memory_lane(tmp_path, caption)
                    if vision_info and 'error' not in vision_info:
                        # Тема из Vision если caption не дала
                        if not parsed.get('topic') and vision_info.get('topic'):
                            parsed['topic'] = vision_info['topic']
                        # Дополняем style_tags
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

            liked = ', '.join(parsed.get('liked', [])) or '—'
            tags = ', '.join(parsed.get('style_tags', [])) or '—'
            topic = parsed.get('topic') or '—'
            desc = vision_info.get('description', '')
            # Название: из caption (brand_parser) или Vision
            name = parsed.get('item_name') or vision_info.get('name', '')
            # Бренд: из caption (brand_parser) приоритетнее Vision
            brand = parsed.get('brand') or vision_info.get('brand')

            parts = [f'🧠 Memory Lane #{item_id}']
            if name:
                parts.append(f'📌 {name}')
            if brand:
                parts.append(f'🏷️ Бренд: {brand}')
            parts.append(f'Реакция: {liked}')
            parts.append(f'Стиль: {tags}')
            parts.append(f'Тема: {topic}')
            if desc:
                parts.append(f'📝 {desc}')
            if vision_info.get('estimated_price_rub'):
                parts.append(f'💰 Оценка: ~{vision_info["estimated_price_rub"]} ₽')

            await update.message.reply_text('\n'.join(parts))
            return
        except Exception as e:
            log.warning(f'memory_lane save failed: {e}')
            # fall through to standard handler

    receipt_path = os.path.join(RECEIPTS_DIR, f'receipt_{update.message.message_id}.jpg')
    file = await photo.get_file()
    await file.download_to_drive(receipt_path)
    log.info(f'Saved receipt: {receipt_path}')

    # === Быстрая классификация типа фото (Vision API, ~1-2 токена) ===
    # Определяем тип ДО OCR/QR, чтобы не тратить время на чеки для фото предметов
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

    # QR/OCR только для чеков и бирок — для предметов не нужен
    qr_data = None
    total_amount = None
    purchase_date = None
    text = ''
    if image_type in ('receipt', 'tag'):
        # Decode QR code (Ozon format)
        qr_data = decode_qr(receipt_path)
        if qr_data:
            total_amount = qr_data.get('s')
            if total_amount:
                total_amount = float(total_amount)
            date_str = qr_data.get('t')
            if date_str and len(date_str) >= 8:
                purchase_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        # Run OCR only for receipts/tags
        text = ocr_image(receipt_path)
        # Save raw OCR for debugging
        with open(receipt_path.replace('.jpg', '_ocr.txt'), 'w', encoding='utf-8') as f:
            f.write(text or 'NO_OCR_TEXT')

    # Если fast path не сработал (image_type всё ещё 'other'), используем OCR-классификацию как fallback
    if image_type == 'other':
        image_type = classify_image_type(text or '')

    tag_probe = parse_clothing_tag(text or '', receipt_path)
    
    # Проверяем штрихкод через pyzbar (более надёжный метод)
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
    
    # Считаем биркой только если:
    # 1. Есть brand + (article или barcode)
    # 2. ИЛИ есть чёткий штрихкод EAN-13 (через OCR или pyzbar)
    # 3. И текст содержит признаки бирки (размер, состав, страна)
    has_barcode = (tag_probe.get('barcode') and len(str(tag_probe.get('barcode'))) >= 8) or (pyzbar_barcode and len(pyzbar_barcode) >= 8)
    has_article = tag_probe.get('article') and len(str(tag_probe.get('article'))) >= 5
    has_brand = tag_probe.get('brand') and len(str(tag_probe.get('brand'))) >= 2
    
    # Проверяем, есть ли в тексте признаки бирки
    raw_text = (tag_probe.get('raw') or '').upper()
    tag_indicators = ['СОСТАВ', 'СТРАНА', 'РАЗМЕР', 'SIZE', 'MADE IN', 'АРТИКУЛ', 'ARTICLE', 'CARE', 'WASH']
    has_tag_indicators = any(ind in raw_text for ind in tag_indicators)
    
    is_real_tag = (
        (has_brand and (has_article or has_barcode)) or
        (has_barcode and has_tag_indicators) or
        (pyzbar_barcode and len(pyzbar_barcode) >= 10)  # EAN-13 штрихкод = точно бирка
    )
    
    # Если Vision API сказал tech/other, но есть признаки бирки — переопределяем
    if image_type in ('unknown', 'other', 'tech') and is_real_tag and not total_amount:
        image_type = 'tag'
        log.info(f"Тип изображения: tag (brand={tag_probe.get('brand')}, article={tag_probe.get('article')}, barcode={pyzbar_barcode or tag_probe.get('barcode')})")
    else:
        log.info(f"Тип изображения: {image_type} (is_real_tag={is_real_tag}, has_brand={has_brand}, has_article={has_article}, has_barcode={has_barcode}, pyzbar={pyzbar_barcode})")

    items = []

    # === Если это предмет/одежда/еда/интерьер (не чек и не бирка) — распознаём как вещь ===
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
                # Таймаут распознавания — сообщаем пользователю, не сохраняем в БД
                await update.message.reply_text(
                    '❌ Объект не распознан\n\n'
                    'Попробуйте:\n'
                    '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
                    '• Использовать команду /add_item <название>'
                )
                return
            if item_info and 'error' not in item_info and item_info.get('name'):
                item_name = item_info.get('name', 'Предмет')
                item_brand = item_info.get('brand')
                item_cat = item_info.get('category', 'другое')
                item_desc = item_info.get('description', '')
                item_color = item_info.get('color')
                item_price = item_info.get('estimated_price_rub')
                style_tags = item_info.get('style_tags', [])

                # Сохраняем в БД сразу (при отклонении удалим)
                conn = get_db()
                cat_map = {
                    'одежда': 'cat_clo_everyday', 'обувь': 'cat_clo_everyday',
                    'техника': 'cat_electronics', 'мебель': 'cat_furniture',
                    'еда': 'cat_food', 'интерьер': 'cat_furniture',
                    'косметика': 'cat_cosmetics', 'аксессуары': 'cat_accessories',
                    'бытовая техника': 'cat_appliances',
                }
                slug = cat_map.get(item_cat.lower(), 'other')
                cat_row = conn.execute("SELECT id FROM categories WHERE slug=? LIMIT 1", (slug,)).fetchone()
                if not cat_row:
                    cat_row = conn.execute("SELECT id FROM categories WHERE slug='other' LIMIT 1").fetchone()
                cat_id = cat_row[0] if cat_row else None

                notes_parts = ['Добавлено через распознавание фото']
                if item_color:
                    notes_parts.append(f'Цвет: {item_color}')
                if item_info.get('material'):
                    notes_parts.append(f'Материал: {item_info["material"]}')
                if item_desc:
                    notes_parts.append(f'Описание: {item_desc}')
                if item_price:
                    notes_parts.append(f'Оценочная цена: ~{item_price} ₽')
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

                # Сохраняем фото и связываем с item
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

                # Показываем результат с кнопками Подтвердить/Отклонить
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                parts = ['📷 Предмет распознан']
                parts.append(f'📌 {item_name}')
                if item_brand:
                    parts.append(f'🏷️ Бренд: {item_brand}')
                parts.append(f'📂 Категория: {item_cat}')
                if item_color:
                    parts.append(f'🎨 Цвет: {item_color}')
                if item_desc:
                    parts.append(f'📝 {item_desc}')
                if style_tags:
                    parts.append(f'🏷️ Теги: {", ".join(style_tags)}')
                if item_price:
                    parts.append(f'💰 Оценка: ~{item_price} ₽')
                parts.append(f'\nID: {new_item_id}')
                parts.append('Сохранить в инвентарь?')

                # Сохраняем данные для колбэка
                ctx.user_data['vision_pending'] = {
                    'item_id': new_item_id,
                    'asset_id': asset_id,
                    'receipt_path': receipt_path,
                }

                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton('✅ Подтвердить', callback_data='vision_confirm'),
                    InlineKeyboardButton('❌ Отклонить', callback_data='vision_reject')
                ]])
                await update.message.reply_text('\n'.join(parts), reply_markup=kb)
                return
            else:
                # Результат есть, но name не распознан — сообщаем и не идём в чек
                await update.message.reply_text(
                    '❌ Объект не распознан\n\n'
                    'Попробуйте:\n'
                    '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
                    '• Использовать команду /add_item <название>'
                )
                return

        except Exception as e:
            log.warning(f'Vision item recognition failed: {e}')
            await update.message.reply_text(
                '❌ Товар не распознан по фото\n\n'
                'Попробуйте:\n'
                '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
                '• Использовать команду /add_item <название>'
            )
            return

    if image_type == 'tag':
        # === Обработка бирки ===
        log.info(f'photo_handler: processing tag, brand={tag_probe.get("brand")}, article={tag_probe.get("article")}')
        tag = tag_probe
        fx_date = purchase_date or date.today().isoformat()
        rate = get_fx_rate(tag['currency'], fx_date)
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
        
        # Ищем информацию через Gemini
        gemini_info = search_product_info_gemini(
            tag.get('brand', ''),
            tag.get('article', ''),
            tag.get('barcode')
        )
        
        google_images_url = f"https://www.google.com/search?tbm=isch&q={quote_plus(search_query)}"
        yandex_images_url = f"https://yandex.ru/images/search?text={quote_plus(search_query)}"
        bing_images_url = f"https://www.bing.com/images/search?q={quote_plus(search_query)}"
        response_lines = ['🧥 Бирка распознана']
        response_lines.append(f"Бренд: {tag['brand'] if tag.get('brand') else 'не найден'}")
        if tag.get('model'):
            response_lines.append(f"Модель: {tag['model']}")
        if tag.get('article'):
            response_lines.append(f"Артикул: {tag['article']}")
        if tag.get('barcode'):
            response_lines.append(f"Штрихкод: {tag['barcode']}")
        if tag.get('size'):
            response_lines.append(f"Размер: {tag['size']}")
        if tag.get('color'):
            response_lines.append(f"Цвет: {tag['color']}")
        if tag.get('price'):
            if tag.get('currency') == 'RUB':
                response_lines.append(f"Цена: {tag['price']} ₽")
            else:
                response_lines.append(f"Цена: {tag['price']} {tag['currency']} (≈ {price_rub:.0f} ₽)")
        response_lines.append("Пробую прислать фото.")
        # Добавляем информацию от Gemini
        if gemini_info:
            response_lines.append('\n🔍 Найдено через Gemini:')
            if gemini_info.get('name'):
                response_lines.append(f"📌 Название: {gemini_info['name']}")
            if gemini_info.get('category'):
                response_lines.append(f"📂 Категория: {gemini_info['category']}")
            if gemini_info.get('color'):
                response_lines.append(f"🎨 Цвет: {gemini_info['color']}")
            if gemini_info.get('material'):
                response_lines.append(f"🧵 Материал: {gemini_info['material']}")
            if gemini_info.get('price_rub'):
                response_lines.append(f"💰 Цена: ~{gemini_info['price_rub']} ₽")
            if gemini_info.get('product_url'):
                response_lines.append(f"🔗 Ссылка: {gemini_info['product_url']}")
        
        response_lines.append(f"\nСсылки на фото:\nGoogle: {google_images_url}\nYandex: {yandex_images_url}\nBing: {bing_images_url}")
        if not tag.get('brand'):
            response_lines.append("⚠️ Бренд не найден в OCR. Нужна часть бирки с логотипом/названием бренда крупным планом.")
        if not tag.get('brand') and not tag.get('article'):
            response_lines.append(f"OCR: {(text or '')[:180].replace(chr(10), ' ')}")
        await update.message.reply_text('\n'.join(response_lines))

        # Отправляем фото от Gemini если есть
        if gemini_info and gemini_info.get('image_url'):
            try:
                await update.message.reply_photo(
                    photo=gemini_info['image_url'],
                    caption=f"🔍 Gemini: {gemini_info.get('name', search_query)}"
                )
            except Exception as e:
                log.warning(f"Failed to send Gemini image: {e}")
        
        # Отправляем фото из поиска
        image_urls = find_product_image_urls(search_query)
        for engine_url in image_urls.values():
            if not engine_url or engine_url.startswith('https://www.google.com/search'):
                continue
            caption = next((k for k, v in image_urls.items() if v == engine_url), 'Photo')
            try:
                await update.message.reply_photo(photo=engine_url, caption=f"{caption}: {search_query}")
            except Exception as e:
                log.warning(f"Failed to send image {engine_url}: {e}")
        return

    # === Если НЕ бирка — используем Vision API (GPT-4o-mini) ===
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

    # Fallback: старый OCR-пайплайн (если Vision не сработал)
    if not items:
        try:
            from scripts import receipt_ocr
            ocr_result = receipt_ocr.process_receipt(receipt_path)
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
        m = re.search(r'ИТОГ[О]?[^\d]*([\d]+[.,]\d{2})', text or '')
        if m:
            total_amount = float(m.group(1).replace(',', '.'))

    conn = get_db()
    purchase_id = None

    # Убедимся, что колонка is_delivery существует
    try:
        conn.execute("ALTER TABLE items ADD COLUMN is_delivery INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # уже существует

    # Проверяем, есть ли доставка от Vision API или OCR
    delivery = 0
    delivery_name = 'Доставка'
    if vision_result and 'error' not in vision_result:
        vd = vision_result.get('delivery', {})
        delivery = vd.get('price', 0) or 0
        delivery_name = vd.get('name', 'Доставка')
    elif ocr_result and hasattr(ocr_result, 'delivery_cost'):
        delivery = ocr_result.delivery_cost or 0
        delivery_name = getattr(ocr_result, 'delivery_item_name', 'Доставка')

    # Отделяем доставку от товаров: убираем из items, если есть
    real_items = []
    delivery_items = []
    if items:
        for item in items:
            name_lower = item['name'].lower()
            # Ключевые слова доставки
            dl_keywords = ['доставк', 'курьер', 'shipping', 'delivery', 'почт', 'postage', 'транспорт']
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
                'корм': 'cat_pets_food', 'собака': 'cat_pets', 'кошка': 'cat_pets',
                'доставка': 'cat_services_log', 'услуга': 'cat_services_log',
                'еда': 'cat_food', 'продукты': 'cat_food'
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

    # Сохраняем доставку отдельно, если есть
    if delivery:
        service_cat = conn.execute("SELECT id FROM categories WHERE slug='service' LIMIT 1").fetchone()
        service_cat_id = service_cat[0] if service_cat else None
        conn.execute(
            "INSERT INTO items (name, purchase_price, purchase_date, category_id, data_origin, purchase_id, is_delivery) "
            "VALUES (?, ?, ?, ?, 'telegram_photo', ?, 1)",
            (delivery_name, delivery, purchase_date, service_cat_id, purchase_id)
        )
    elif delivery_items:
        # Если доставка была в items, но не выделилась через delivery_cost
        for dli in delivery_items:
            service_cat = conn.execute("SELECT id FROM categories WHERE slug='service' LIMIT 1").fetchone()
            service_cat_id = service_cat[0] if service_cat else None
            conn.execute(
                "INSERT INTO items (name, purchase_price, purchase_date, category_id, data_origin, purchase_id, is_delivery) "
                "VALUES (?, ?, ?, ?, 'telegram_photo', ?, 1)",
                (dli['name'], dli['price'], purchase_date, service_cat_id, purchase_id)
            )

    # Формируем структурированный вывод
    response_parts = ['🧾 Чек распознан']

    # Магазин из Vision API
    store_name = (vision_result or {}).get('store')
    if store_name and store_name != 'Неизвестный':
        response_parts.append(f"🏪 {store_name}")

    if purchase_date:
        response_parts.append(f"Дата: {purchase_date}")

    if total_amount:
        total_amount_clean = f"{total_amount:.2f}".rstrip('0').rstrip('.')
        response_parts.append(f"Сумма: {total_amount_clean} ₽")
    else:
        response_parts.append("Сумма: не определена")

    if items:
        response_parts.append(f"📦 Товары ({len(items)}):" )
        for item in items:
            price_str = f"{item['price']:.2f} ₽".rstrip('0').rstrip('.').rstrip('₽').strip() + ' ₽'
            qty_str = f" × {item['qty']}" if item.get('qty', 1) > 1 else ''
            response_parts.append(f"  • {item['name']} — {price_str}{qty_str}")

    # Доставка отдельным блоком
    if delivery or delivery_items:
        dl_total = delivery or sum(dli.get('price', 0) for dli in delivery_items)
        dl_clean = f"{dl_total:.2f} ₽".rstrip('0').rstrip('.').rstrip('₽').strip() + ' ₽'
        response_parts.append(f"\n🚚 Доставка: {dl_clean}")
    
    if not items and not delivery:
        response_parts.append("Товары: не найдены")
        response_parts.append("Добавьте вручную /add <название> <цена>")

    response_text = '\n'.join(response_parts)

    if purchase_id:
        conn.commit()
    conn.close()
    await update.message.reply_text(response_text)


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        log.info("cmd_list: Начало выполнения")
        conn = get_db()
        log.info("cmd_list: БД подключена")
        total = conn.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]
        log.info(f"cmd_list: Всего товаров = {total}")
        rows = conn.execute("""
            SELECT c.name, COUNT(i.id) as cnt, COALESCE(SUM(i.purchase_price), 0) as total_p
            FROM items i JOIN categories c ON i.category_id = c.id
            WHERE i.deleted_at IS NULL
            GROUP BY c.name ORDER BY cnt DESC
        """).fetchall()
        log.info(f"cmd_list: Получено категорий = {len(rows)}")
        conn.close()
        lines = [f'📦 Инвентарь: {total} товаров\n']
        for r in rows:
            lines.append(f'• {r["name"]}: {r["cnt"]} шт. ({r["total_p"]:.0f} ₽)')
        lines.append(f'\nВсего категорий: {len(rows)}')
        await update.message.reply_text('\n'.join(lines))
    except Exception as e:
        log.error(f"Ошибка в cmd_list: {e}")
        await update.message.reply_text(f'❌ Ошибка: {e}')

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.execute("SELECT alert_type,title,message FROM alerts WHERE status='pending' ORDER BY created_at").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text('✅ Нет активных алертов')
        return
    icons = {'warranty_expiring':'⚠️','warranty_expired':'❌','expiry_approaching':'⏳','expired':'🚫','low_stock':'📉','price_drop':'💰'}
    lines = ['🔔 Активные алерты:\n']
    for r in rows:
        icon = icons.get(r['alert_type'], '🔔')
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
    """Команда /last_drives — показывает последние поездки всех провайдеров каршеринга."""
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
            "🚗 Поездки не найдены.\n"
            "Команда: /last_drives [количество] [провайдер]\n"
            "Провайдеры: yandex_drive, citydrive, belka, delimobil"
        )
        return

    provider_names = {
        'yandex_drive': 'Яндекс Драйв',
        'citydrive': 'Ситидрайв',
        'belka': 'BelkaCar',
    }

    lines = [f"🚗 Последние поездки ({len(rows)}):", ""]
    for idx, row in enumerate(rows, start=1):
        dt = (row["date_start"] or "")[:10]
        provider_name = provider_names.get(row['source'], row['source'])
        car = row["car_model"] or "—"
        km = f'{row["distance_km"]:.0f} км' if row["distance_km"] else "—"
        total = f'{row["total"]:.0f} ₽' if row["total"] else "—"
        plate = f'({row["car_plate"]})' if row["car_plate"] else ""

        lines.append(f"{idx}. {dt} | {provider_name}")
        lines.append(f"   {car} {plate} • {km} • {total}")
        lines.append("")

    await update.message.reply_text("\n".join(lines).rstrip())


async def cmd_find_car(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /find_car — рекомендации по тарифам каршеринга с учётом истории."""
    args = " ".join(ctx.args) if ctx.args else ""
    hours, km = parse_drive_request(args)

    if hours is None or km is None:
        await update.message.reply_text(
            "🚗 Использование:\n"
            "/find_car 3ч 80км\n"
            "/find_car 2 часа 60 км\n"
            "/find_car сутки 120км\n\n"
            "Укажи время и расстояние."
        )
        return

    conn = get_db()
    
    # Загружаем тарифы
    tariffs = conn.execute(
        "SELECT * FROM carsharing_tariffs WHERE zone = 'msk' ORDER BY provider"
    ).fetchall()
    
    # Анализируем историю поездок
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
    
    # Предпочтения пользователя (из истории)
    pref_models = [h['car_model'] for h in history if h['trips'] >= 3]
    pref_tariffs = list(dict.fromkeys([h['tariff'] for h in history if h['tariff']]))
    
    conn.close()

    if not tariffs:
        await update.message.reply_text("Тарифы не загружены. Добавь их в БД.")
        return

    provider_names = {
        'yandex': 'Яндекс Драйв',
        'citydrive': 'Ситидрайв',
        'belka': 'BelkaCar',
        'delimobil': 'Делимобиль',
    }

    # Расчёт стоимости для всех тарифов
    results = []
    for t in tariffs:
        cost = calculate_drive_cost(t, hours, km)
        provider = t['provider']
        tariff_name = t['tariff_name'] or ''
        
        # Определяем рекомендацию на основе истории
        is_preferred = False
        reason = ""
        
        # Проверяем предпочтительные модели/тарифы
        if 'Bay 24' in tariff_name and 'Bay 24' in pref_tariffs:
            is_preferred = True
            reason = "⭐ Ваш любимый тариф (14 поездок на FAW Bestune T77)"
        elif provider == 'yandex' and hours >= 3:
            is_preferred = True
            reason = "⭐ Выгодно для длительных поездок"
        elif t['rate_type'] == 'per_hour_km' and hours <= 2:
            is_preferred = True
            reason = "⭐ Выгодно для коротких поездок"
        
        results.append({
            'provider': provider,
            'name': provider_names.get(provider, provider.upper()),
            'tariff': tariff_name,
            'cost': cost,
            'rate_type': t['rate_type'],
            'insurance': '✓' if t['insurance_included'] else '✗',
            'is_preferred': is_preferred,
            'reason': reason,
        })
    
    # Сортируем: предпочтительные первыми, затем по цене
    results.sort(key=lambda x: (not x['is_preferred'], x['cost']))

    lines = [f"🚗 Рекомендации на {hours}ч / {km}км\n"]
    lines.append(f"📊 История: {len(history)} моделей, {sum(h['trips'] for h in history)} поездок")
    lines.append(f"💡 Предпочтения: {', '.join(pref_models[:3]) or 'нет данных'}\n")
    
    for r in results:
        tariff_info = f" ({r['tariff']})" if r['tariff'] else ""
        rate_info = "фикс+км" if r['rate_type'] == 'flat_km' else "почас"
        pref_mark = "⭐ " if r['is_preferred'] else ""
        lines.append(f"{pref_mark}• {r['name']}{tariff_info}: ~{r['cost']:.0f} ₽ ({rate_info}) страховка{r['insurance']}")
        if r['reason']:
            lines.append(f"   └ {r['reason']}")

    # Добавляем тестовые сценарии если запрошено
    if hours == 3 and km == 80:
        lines.append("\n📋 Тестовый сценарий 3ч/80км:")
        lines.append("   FAW Bestune T77 + Bay 24: ~2197 ₽ (средняя по истории)")
    elif hours >= 12:
        lines.append("\n📋 Для суточной аренды рекомендуется Bay 24 или тариф 'Сутки'")

    lines.append("\n(реальная стоимость может отличаться)")
    await update.message.reply_text("\n".join(lines))


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /check — расширенный PDF-отчёт."""
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
        
        # Заголовок
        pdf.set_font('DejaVu', 'B', 16)
        pdf.cell(0, 10, 'Consumption Agent — Отчёт', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        pdf.cell(0, 6, f'Дата: {date.today().isoformat()}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Статистика
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Общая статистика', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        stats = [
            ('Товаров', c.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]),
            ('Покупок', c.execute("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL").fetchone()[0]),
            ('Категорий', c.execute("SELECT COUNT(*) FROM categories").fetchone()[0]),
            ('С гарантией', c.execute("SELECT COUNT(*) FROM items WHERE warranty_months>0 AND deleted_at IS NULL").fetchone()[0]),
            ('Алертов', c.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]),
        ]
        for k, v in stats:
            pdf.cell(0, 6, f'  {k}: {v}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Топ-10 категорий по сумме
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Топ-10 категорий по сумме', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        cats = conn.execute('''
            SELECT c.name, COUNT(i.id) as cnt, COALESCE(ROUND(SUM(i.purchase_price),0),0) as total
            FROM items i JOIN categories c ON i.category_id = c.id
            WHERE i.deleted_at IS NULL
            GROUP BY c.id ORDER BY total DESC LIMIT 10
        ''').fetchall()
        for r in cats:
            pdf.cell(0, 6, f'  {r["name"]:25s} {r["cnt"]:4d} шт.  {r["total"]:>8.0f} ₽', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # График трат по месяцам (прямоугольники)
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Траты по месяцам', new_x='LMARGIN', new_y='NEXT')
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
        
        # Активные алерты
        pdf.set_font('DejaVu', 'B', 12)
        a_cnt = c.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
        pdf.cell(0, 8, f'Активные алерты ({a_cnt})', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        if a_cnt:
            for r in conn.execute("SELECT alert_type, title, message FROM alerts WHERE status='pending' ORDER BY alert_type LIMIT 10").fetchall():
                pdf.cell(0, 6, f'  [{r["alert_type"][:15]:15s}] {r["title"][:50]}', new_x='LMARGIN', new_y='NEXT')
        else:
            pdf.cell(0, 6, '  Нет активных алертов', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Топ-20 товаров по цене (без дублей)
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Топ-20 товаров по цене', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        top_items = conn.execute('''
            SELECT name, purchase_price, category_id FROM items 
            WHERE deleted_at IS NULL AND purchase_price > 0 AND purchase_price NOTNULL
            GROUP BY ROUND(purchase_price,-2), name HAVING MIN(id)
            ORDER BY purchase_price DESC LIMIT 20
        ''').fetchall()
        for r in top_items:
            price_str = f'{r["purchase_price"]:>8.0f} ₽' if r["purchase_price"] else ''
            cat_str = (r["category_id"] or '')[:10]
            pdf.cell(0, 6, f'  {price_str}  {r["name"][:45]:45s} [{cat_str}]', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
        
        # Сохраняем и шлём
        pdf_path = '/tmp/consumption_agent_report.pdf'
        pdf.output(pdf_path)
        conn.close()
        
        await update.message.reply_text('📊 Отчёт готов:', reply_to_message_id=update.message.message_id)
        await update.message.reply_document(open(pdf_path, 'rb'), filename='report.pdf')
        
    except Exception as e:
        log.warning(f'cmd_check error: {e}')
        print(traceback.format_exc())
        await update.message.reply_text(f'❌ Ошибка генерации отчёта: {e}')

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = ' '.join(ctx.args)
    if not text:
        await update.message.reply_text('❌ Пример: /add Носки 350 одежда')
        return
    # Parse: name, optional price, optional category
    parts = text.rsplit(None, 2)  # try splitting from right
    name = text
    price = None
    category = None
    # Try category extraction (из consumption.categorize — Шаг 5)
    cats = {'еда':'cat_food','продукты':'cat_food','одежда':'cat_clo_everyday','обувь':'cat_clo_shoes',
            'техника':'cat_tech','книги':'cat_culture_books','спорт':'cat_sport','косметика':'cat_cosmetics',
            'здоровье':'cat_health_med','дом':'cat_home','авто':'cat_auto','животные':'cat_pets',
            'мебель':'cat_home_furn','аксесс':'cat_clo_access','хобби':'cat_hobbies',
            'интим':'cat_sexual','подписка':'cat_subscriptions'}
    for kw, cid in cats.items():
        if kw in text.lower():
            # Extract price before category
            m = re.search(r'(\d[\d\s]*\d)\s*(?:₽|руб|р)?', text)
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
        m = re.search(r'(\d[\d\s]*\d)\s*(?:₽|руб|р)?', text)
        if m:
            price = float(m.group(1).replace(' ', ''))
            name = text[:m.start()].strip().rstrip(',').strip()
    if not name or len(name) < 2:
        await update.message.reply_text('❌ Слишком короткое название')
        return
    conn = get_db()
    cat_id = None
    if category:
        row = conn.execute("SELECT id FROM categories WHERE id=? OR slug=? LIMIT 1", (category, category)).fetchone()
        if row: cat_id = row[0]
    # Автокатегоризация из consumption.categorize если пользователь не указал
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
    await update.message.reply_text(f'✅ Добавлено: {name.strip()}{f" ({price:.0f} ₽)" if price else ""}')

async def cmd_parse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Парсинг необработанных чеков Ozon из почты."""
    await update.message.reply_text('🔄 Проверяю необработанные чеки Ozon...')
    
    limit = 10
    if ctx.args and ctx.args[0].isdigit():
        limit = min(int(ctx.args[0]), 50)
    
    try:
        conn = get_db()
        # Находим чеки без привязанных товаров
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
            await update.message.reply_text('✅ Все чеки Ozon уже обработаны')
            conn.close()
            return
        
        lines = ['🔍 Найдено необработанных:', '']
        for r in rows:
            date_str = r['cheque_date'][:10] if r['cheque_date'] else '?'
            url = (r['receipt_url'] or '')[:60]
            status = '✅ привязан' if r['purchase_id'] else '❌ без покупки'
            lines.append(f'  • {date_str} — {status}')
        
        lines.append('')
        lines.append('Для обработки нужны свежие куки Ozon.')
        lines.append('Обновите куки в .ozon_cookies.txt или используйте /add_photo')
        
        await update.message.reply_text('\n'.join(lines))
        conn.close()
    except Exception as e:
        log.warning(f'cmd_parse error: {e}')
        await update.message.reply_text(f'❌ Ошибка: {e}')


async def cmd_debts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /debts — принудительная проверка кредитов и займов.
    Сканирует почты + SMS, показывает ближайшие платежи."""
    await update.message.reply_text('🔍 Проверяю почты и SMS на предмет кредитных уведомлений...')

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
        await update.message.reply_text('✅ Нет активных кредитных платежей на ближайшие 30 дней.')
        return

    lines = ['💳 *Кредиты к оплате (30 дней):*']
    total = 0.0
    urgent_count = 0
    for r in rows:
        rid, sender, amount, pay_date, days = r
        if days < 0:
            prefix = '🔴 ПРОСРОЧЕН'
            urgent_count += 1
        elif days <= 3:
            prefix = '🟡 СРОЧНО'
            urgent_count += 1
        elif days <= 7:
            prefix = '🟢 На этой неделе'
        else:
            prefix = '⚪'

        amount_str = f'{amount:.2f} ₽' if amount else '—'
        date_str = pay_date or '—'
        days_str = f'(через {days} дн.)' if days >= 0 else f'(просрочено {-days} дн.)'
        lines.append(f'\n{prefix} *{esc_md(sender)}* — {amount_str}')
        lines.append(f'   📅 {date_str} {days_str}')

        if amount:
            total += amount

    lines.append(f'\n💰 *Итого: {total:.2f} ₽*')
    if urgent_count:
        lines.append(f'⚠️ {urgent_count} платеж(а/ей) требуют внимания!')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_fines(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /fines — принудительная проверка штрафов на всех почтах + SMS.
    Показывает неоплаченные с кнопкой ✅ Оплачено."""
    await update.message.reply_text('🔍 Проверяю почты и SMS на предмет штрафов...')

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
        await update.message.reply_text('✅ Нет неоплаченных штрафов.')
        return

    # Группируем по номеру — берём последний статус (DESC по дате)
    by_number = {}
    for r in rows:
        num = r[2] or str(r[0])
        by_number[num] = r

    # Берём только те, где нет paid_confirmed_at (по номеру штрафа дедуплицируем)
    active = []
    for r in by_number.values():
        if r[1] == 'new' or (r[1] in ('paid', 'fined') and r[8] is None):
            active.append(r)

    if not active:
        await update.message.reply_text('✅ Нет неоплаченных штрафов.')
        return

    # Сводка
    total = sum(r[3] or 0 for r in active)
    summary_lines = [f'🚨 *Штрафы: {len(active)} шт., всего {total:.0f} ₽*']
    for r in active:
        amount = r[3] or 0
        desc = (r[4] or '').strip()[:60]
        date_str = r[6] or '—'
        summary_lines.append(f'  🔴 {amount:.0f} ₽ — {esc_md(desc) or "Штраф"} ({date_str})')
    summary_lines.append(f'\n⬇️ Отправляю детали с кнопками...')
    await update.message.reply_text('\n'.join(summary_lines), parse_mode='Markdown')

    # Каждый штраф отдельным сообщением с кнопкой
    for r in active:
        fine_id = r[0]
        amount = r[3] or 0
        desc = (r[4] or '').strip()
        date_str = r[6] or '—'
        vendor = (r[7] or '').strip()[:60]
        veh = (r[5] or '').strip()[:15]
        num_str = (r[2] or '')[:20]

        detail_lines = [f'🚨 *Штраф: {amount:.0f} ₽*']
        if desc:
            detail_lines.append(f'📋 {esc_md(desc)}')
        detail_lines.append(f'📅 {date_str}')
        if veh:
            detail_lines.append(f'🚗 {veh}')
        if vendor:
            detail_lines.append(f'🏛 {vendor}')
        if num_str:
            detail_lines.append(f'№ {num_str}')

        keyboard = {
            'inline_keyboard': [[
                {'text': '✅ Оплачено', 'callback_data': f'fine_paid:{fine_id}'}
            ]]
        }

        await update.message.reply_text(
            '\n'.join(detail_lines),
            parse_mode='Markdown',
            reply_markup=keyboard
        )


async def cmd_dayexp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /dayexp — чеки за сегодня с принудительным сканированием почт (фоново)."""
    msg = await update.message.reply_text('🔍 Сканирую почты и SMS за сегодня...')

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
        await msg.edit_text('⚠️ Ошибка сканирования почт.')
        return

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date = date('now')
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY total_amount DESC
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        await msg.edit_text(f'📭 За сегодня ({datetime.now().strftime("%d.%m.%Y")}) покупок не найдено.')
        return

    total = sum(r[1] or 0 for r in rows)
    source_icons = {'gmail': '📧', 'yandex': '📧', 'yandex_food': '🍽', 'sms': '📱', 'local': '📝', 'manual': '✏️'}

    lines = [f'📊 *Расходы за {datetime.now().strftime("%d.%m.%Y")}*']
    lines.append(f'_{len(rows)} покупок, всего {total:,.0f} ₽_\n'.replace(',', ' '))

    for r in rows:
        date_str, amount, store, source, notes = r
        amt = amount or 0
        src_icon = source_icons.get(source or '', '📧')
        store_clean = store or '—'
        notes_clean = (notes or '').replace('\n', ' ').strip()
        if notes_clean:
            short_note = notes_clean[:80]
            lines.append(f'{src_icon} *{store_clean}* — {amt:,.0f} ₽'.replace(',', ' '))
            lines.append(f'   {short_note}')
        else:
            lines.append(f'{src_icon} *{store_clean}* — {amt:,.0f} ₽'.replace(',', ' '))

    # По магазинам
    by_store = {}
    for r in rows:
        s = r[2] or 'Другое'
        by_store[s] = by_store.get(s, 0) + (r[1] or 0)
    if len(by_store) > 1:
        lines.append(f'\n📌 *По магазинам:*')
        for s, a in sorted(by_store.items(), key=lambda x: -x[1]):
            lines.append(f'  • {s}: {a:,.0f} ₽'.replace(',', ' '))

    await msg.edit_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_monthexp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /monthexp — расходы с начала месяца с расшифровкой по дням.
    За текущий день — принудительное сканирование почт + SMS (фоново)."""
    msg = await update.message.reply_text('🔍 Сканирую почты и SMS — собираю данные за месяц...')

    # Фоновое сканирование
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
        1:'Январь', 2:'Февраль', 3:'Март', 4:'Апрель',
        5:'Май', 6:'Июнь', 7:'Июль', 8:'Август',
        9:'Сентябрь', 10:'Октябрь', 11:'Ноябрь', 12:'Декабрь'
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
        await msg.edit_text(f'📭 За {month_name} (с 1 по {today.day}) покупок не найдено.')
        return

    grand_total = sum(r[1] or 0 for r in rows)
    source_icons = {'gmail': '📧', 'yandex': '📧', 'yandex_food': '🍽', 'sms': '📱', 'local': '📝', 'manual': '✏️'}

    lines = [f'📊 *Расходы с 1 {month_name.lower()} по {today.day} число*']
    lines.append(f'_{len(rows)} покупок, всего {grand_total:,.0f} ₽_\n'.replace(',', ' '))

    # Группировка по дням
    by_day = {}
    for r in rows:
        d = r[0]
        if d not in by_day:
            by_day[d] = []
        by_day[d].append(r)

    for day in sorted(by_day.keys(), reverse=True):
        day_rows = by_day[day]
        day_total = sum(r[1] or 0 for r in day_rows)
        day_label = 'Сегодня' if day == today_str else day
        lines.append(f'\n📅 *{day_label}* — {day_total:,.0f} ₽ ({len(day_rows)} покупок)'.replace(',', ' '))

        for r in day_rows:
            date_str, amount, store, source, notes = r
            amt = amount or 0
            src_icon = source_icons.get(source or '', '📧')
            store_clean = store or '—'
            notes_clean = (notes or '').replace('\n', ' ').strip()[:60]
            if notes_clean:
                lines.append(f'{src_icon} *{store_clean}* — {amt:,.0f} ₽ · {notes_clean}'.replace(',', ' '))
            else:
                lines.append(f'{src_icon} *{store_clean}* — {amt:,.0f} ₽'.replace(',', ' '))

    # По магазинам
    by_store = {}
    for r in rows:
        s = r[2] or 'Другое'
        by_store[s] = by_store.get(s, 0) + (r[1] or 0)
    if len(by_store) > 1:
        lines.append(f'\n📌 *Всего по магазинам:*')
        for s, a in sorted(by_store.items(), key=lambda x: -x[1]):
            lines.append(f'  • {s}: {a:,.0f} ₽'.replace(',', ' '))

    await msg.edit_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_warranties(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /warranties — отчёт по гарантиям."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from warranty_check import get_warranties_report, update_warranty_until, check_warranties, save_alerts
        conn = get_db()
        # Пересчёт warranty_until
        update_warranty_until(conn)
        # Проверка и сохранение алертов
        alerts = check_warranties(conn)
        if alerts:
            save_alerts(conn, alerts)
        # Отчёт
        report = get_warranties_report(conn)
        conn.close()
        await update.message.reply_text(report, parse_mode='Markdown')
    except Exception as e:
        log.error(f'cmd_warranties error: {e}')
        await update.message.reply_text(f'❌ Ошибка: {e}')


async def cmd_add_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /add_item — добавить вещь в инвентарь с фото, брендом и сроком замены.
    Формат:
      /add_item Название
      /add_item Название | бренд Бренд | замена 60 мес
      /add_item Название | бренд Бренд | замена 5 лет
    Можно прикрепить фото к сообщению."""
    text = ' '.join(ctx.args).strip()
    if not text:
        await update.message.reply_text(
            '❌ Укажите название вещи\n\n'
            'Пример:\n'
            '/add_item Стремянка 5 ступеней\n'
            '/add_item Пылесос | бренд Xiaomi | замена 60 мес\n'
            '/add_item Носки | бренд Nike | замена 12 мес\n\n'
            'Можно прикрепить фото к сообщению.'
        )
        return

    # Парсим поля через универсальный brand_parser
    from brand_parser import parse_brand_and_name
    bp = parse_brand_and_name(text)
    name = bp['name'] or text
    brand = bp['brand']
    replace_months = bp['replace_months']
    replace_days = bp.get('replace_days')

    # Нормализуем название — убираем лишнее
    name = name.strip().strip(',;')
    if not name:
        await update.message.reply_text('❌ Название не может быть пустым')
        return

    # Определяем категорию по ключевым словам в названии
    cat_map = {
        'стремян': 'cat_home', 'пылесос': 'cat_tech', 'утюг': 'cat_home',
        'утюж': 'cat_home', 'фен': 'cat_cosmetics', 'расчес': 'cat_cosmetics',
        'щётк': 'cat_home', 'зубн': 'cat_health_med', 'полотен': 'cat_home',
        'постель': 'cat_home', 'простын': 'cat_home', 'наволочк': 'cat_home',
        'одеял': 'cat_home', 'подушк': 'cat_home', 'ковёр': 'cat_home_furn',
        'ковер': 'cat_home_furn', 'штора': 'cat_home_furn', 'светиль': 'cat_home_furn',
        'лампа': 'cat_home_furn', 'люстр': 'cat_home_furn', 'торшер': 'cat_home_furn',
        'кресл': 'cat_home_furn', 'диван': 'cat_home_furn', 'стол': 'cat_home_furn',
        'стул': 'cat_home_furn', 'кроват': 'cat_home_furn', 'комод': 'cat_home_furn',
        'тумб': 'cat_home_furn', 'шкаф': 'cat_home_furn', 'стеллаж': 'cat_home_furn',
        'куртк': 'cat_clo_everyday', 'пальт': 'cat_clo_everyday', 'пухов': 'cat_clo_everyday',
        'плащ': 'cat_clo_everyday', 'пиджак': 'cat_clo_everyday', 'костюм': 'cat_clo_everyday',
        'джинс': 'cat_clo_everyday', 'брюк': 'cat_clo_everyday', 'штаны': 'cat_clo_everyday',
        'футболк': 'cat_clo_everyday', 'рубашк': 'cat_clo_everyday', 'свитер': 'cat_clo_everyday',
        'водолаз': 'cat_clo_everyday', 'толстов': 'cat_clo_everyday', 'худи': 'cat_clo_everyday',
        'носк': 'cat_clo_underwear', 'трус': 'cat_clo_underwear', 'майк': 'cat_clo_underwear',
        'ботинк': 'cat_clo_shoes', 'кроссов': 'cat_clo_shoes', 'туфл': 'cat_clo_shoes',
        'сапог': 'cat_clo_shoes', 'тапк': 'cat_clo_shoes', 'шлёпан': 'cat_clo_shoes',
        'шарф': 'cat_clo_access', 'шапк': 'cat_clo_access', 'ремен': 'cat_clo_access',
        'перчат': 'cat_clo_access', 'сумк': 'cat_clo_access', 'рюкзак': 'cat_clo_access',
        'часы': 'cat_clo_access', 'браслет': 'cat_clo_access', 'очк': 'cat_clo_access',
        'телефон': 'cat_tech', 'ноутбук': 'cat_tech', 'планшет': 'cat_tech',
        'наушник': 'cat_tech', 'колонк': 'cat_tech', 'роутер': 'cat_tech',
        'монитор': 'cat_tech', 'клавиатур': 'cat_tech', 'мышк': 'cat_tech',
        'камер': 'cat_tech', 'принтер': 'cat_tech', 'провод': 'cat_tech',
        'зарядк': 'cat_tech', 'кабель': 'cat_tech', 'адаптер': 'cat_tech',
        'холодиль': 'cat_tech', 'микроволн': 'cat_tech', 'тостер': 'cat_tech',
        'блендер': 'cat_tech', 'кофемолк': 'cat_tech', 'чайник': 'cat_home_kitchen',
        'сковород': 'cat_home_kitchen', 'кастрюл': 'cat_home_kitchen', 'нож': 'cat_home_kitchen',
        'тарелк': 'cat_home_kitchen', 'кружк': 'cat_home_kitchen', 'чашк': 'cat_home_kitchen',
        'косметик': 'cat_cosmetics', 'крем': 'cat_cosmetics', 'шампун': 'cat_cosmetics',
        'кондиционер': 'cat_cosmetics', 'мыл': 'cat_cosmetics', 'дух': 'cat_cosmetics',
        'игрушк': 'cat_hobbies', 'настольн': 'cat_hobbies', 'книг': 'cat_culture_books',
        'корм': 'cat_pets', 'игрушк.*животн': 'cat_pets', 'лежак': 'cat_pets',
    }
    category = None
    nl = name.lower()
    for kw, cid in cat_map.items():
        if kw in nl:
            category = cid
            break
    if not category:
        category = 'cat_other'

    # Формируем notes с информацией о замене
    notes_parts = ['Добавлено через /add_item']
    replace_days = bp.get('replace_days')
    if replace_days:
        notes_parts.append(f'Ожидается замена через {replace_days} дн.')
    elif replace_months:
        notes_parts.append(f'Ожидается замена через {replace_months} мес.')
    notes = '\n'.join(notes_parts)

    # Сохраняем в БД
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

    # Если есть фото — сохраняем и обогащаем через Vision API
    has_photo = False
    vision_enriched = {}
    photos = []
    if update.message and update.message.photo:
        photos = update.message.photo
    # Если это reply на сообщение с фото — берём фото из оригинального сообщения
    elif update.message and update.message.reply_to_message and update.message.reply_to_message.photo:
        photos = update.message.reply_to_message.photo
        log.info(f'add_item: using photo from reply_to_message {update.message.reply_to_message.message_id}')

    if photos:
        best = photos[-1]
        try:
            file = await best.get_file()
            file_bytes = await file.download_as_bytearray()

            # Сохраняем фото
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

            # Vision API обогащение: бренд, цвет, описание
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
                    # Бренд из текста приоритетнее, Vision как fallback
                    if not brand and vision_enriched.get('brand'):
                        brand = vision_enriched['brand']
                    # Обновляем запись в БД: attributes + notes
                    uconn = get_db()
                    attrs = json.dumps({
                        'color': vision_enriched.get('color'),
                        'description': vision_enriched.get('description'),
                        'style_tags': vision_enriched.get('style_tags', []),
                        'material': vision_enriched.get('material'),
                        'estimated_price_rub': vision_enriched.get('estimated_price_rub'),
                    }, ensure_ascii=False)
                    # Дополняем notes данными от Vision
                    vision_notes = []
                    if vision_enriched.get('color'):
                        vision_notes.append(f"Цвет: {vision_enriched['color']}")
                    if vision_enriched.get('material'):
                        vision_notes.append(f"Материал: {vision_enriched['material']}")
                    if vision_enriched.get('description'):
                        vision_notes.append(f"Описание: {vision_enriched['description']}")
                    if vision_enriched.get('estimated_price_rub'):
                        vision_notes.append(f"Оценочная цена: ~{vision_enriched['estimated_price_rub']} ₽")
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

    # Формируем ответ
    lines = [f'✅ Добавлено: *{esc_md(name)}*']
    if brand:
        lines.append(f'🏷 Бренд: {esc_md(brand)}')
    lines.append(f'📂 Категория: {esc_md(category)}')
    if replace_days:
        lines.append(f'🔄 Замена: через {replace_days} дн.')
    elif replace_months:
        lines.append(f'🔄 Замена: через {replace_months} мес.')
    if vision_enriched and 'error' not in vision_enriched:
        if vision_enriched.get('color'):
            lines.append(f'🎨 Цвет: {vision_enriched["color"]}')
        if vision_enriched.get('description'):
            lines.append(f'📝 {vision_enriched["description"]}')
        if vision_enriched.get('style_tags'):
            lines.append(f'🏷️ Теги: {", ".join(vision_enriched["style_tags"])}')
        if vision_enriched.get('estimated_price_rub'):
            lines.append(f'💰 Оценка: ~{vision_enriched["estimated_price_rub"]} ₽')
    if has_photo:
        lines.append('📸 Фото сохранено')
    lines.append(f'\nID: {item_id}')

    # Кнопка удаления
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton('🗑 Удалить', callback_data=f'item_delete:{item_id}')
    ]])

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown', reply_markup=kb)


async def cmd_items(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /items — список вещей с группировкой и сроками замены.
    /items — все вещи, которым скоро нужна замена
    /items all — все вещи
    /items <категория> — вещи по категории"""
    conn = get_db()
    try:
        all_items = conn.execute('''
            SELECT id, name, brand, category_id, lifespan_months,
                   purchase_date, status, replace_after_months, replace_after_days, notes
            FROM items
            WHERE deleted_at IS NULL AND is_delivery = 0
              AND data_origin IN ('manual', 'local')
            ORDER BY category_id, name
        ''').fetchall()
    finally:
        conn.close()

    if not all_items:
        await update.message.reply_text('📭 В инвентаре пока нет вещей. Добавьте через /add_item')
        return

    today = datetime.now().date()

    # Фильтр
    args = ' '.join(ctx.args).lower() if ctx.args else ''
    if args and args != 'all':
        # Поиск по названию, бренду, категории, описанию, тегам
        filtered = []
        for r in all_items:
            name = (r[1] or '').lower()
            brand = (r[2] or '').lower()
            cat = (r[3] or '').lower()
            notes = (r[9] or '').lower()
            attrs = {}
            try:
                attrs = json.loads(r[10] or '{}')
            except json.JSONDecodeError:
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
        # По умолчанию: те, у кого есть replace_after_months/days или lifespan_months, и они истекают
        filtered = []
        for r in all_items:
            rep_days = r[8]  # replace_after_days (точное значение)
            rep_months = r[7] or r[4]  # replace_after_months, потом lifespan_months
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
                    if days_left <= 90:  # ближайшие 3 месяца
                        filtered.append((days_left, r))
                except (TypeError, ValueError) as e:
                    log.warning('Не удалось вычислить срок замены для item_id=%s: %s', r[0], e)
        filtered.sort(key=lambda x: x[0])
        filtered = [r[1] for r in filtered]
        if not filtered:
            # Если нет вещей к замене, показываем последние добавленные
            filtered = all_items[-10:]

    if not filtered:
        await update.message.reply_text('📭 Ничего не найдено по вашему запросу.')
        return

    # Группируем по категориям
    by_cat = {}
    for r in filtered:
        cat = r[3] or 'cat_other'
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(r)

    # Человеческие названия категорий
    cat_names = {
        'cat_clo_everyday': '👕 Повседневная одежда',
        'cat_clo_underwear': '👙 Нижнее бельё / носки',
        'cat_clo_shoes': '👟 Обувь',
        'cat_clo_access': '🧣 Аксессуары',
        'cat_tech': '💻 Техника',
        'cat_home': '🏠 Хозтовары',
        'cat_home_furn': '🪑 Мебель',
        'cat_home_kitchen': '🍳 Кухня',
        'cat_cosmetics': '🧴 Косметика',
        'cat_health_med': '💊 Здоровье',
        'cat_culture_books': '📚 Книги',
        'cat_hobbies': '🎮 Хобби',
        'cat_pets': '🐾 Животные',
        'cat_sport': '🏋️ Спорт',
        'cat_auto': '🚗 Авто',
        'cat_food': '🍎 Продукты',
        'cat_other': '📦 Прочее',
    }

    lines = ['📋 *Инвентарь:*']
    for cat, items in sorted(by_cat.items()):
        cat_label = cat_names.get(cat, f'📁 {cat}')
        lines.append(f'\n*{cat_label}:*')
        for r in items:
            name = r[1]
            brand = r[2]
            rep = r[7] or r[4]
            purchase = r[5]

            name_str = esc_md(name)
            if brand:
                name_str += f' ({esc_md(brand)})'

            # Срок замены
            if rep and purchase:
                try:
                    pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                    replace_date = add_months_safe(pd, rep)
                    days = (replace_date - today).days
                    if days <= 0:
                        suffix = ' 🔴 Пора менять!'
                    elif days <= 30:
                        suffix = f' 🟡 Через {days} дн.'
                    else:
                        suffix = ''
                except (TypeError, ValueError) as e:
                    log.warning('Не удалось показать срок замены для item_id=%s: %s', r[0], e)
                    suffix = ''
            else:
                suffix = ''

            lines.append(f'  • {name_str}{suffix}')

    lines.append(f'\nВсего: {len(filtered)} вещей')
    if not args or args == 'all':
        lines.append('\n/items all — показать всё')
        lines.append('/items <категория> — фильтр')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_items_full(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /items_full — полный вывод с фото и всеми данными.
    /items_full all — все вещи с полной информацией
    /items_full — вещи с заменой <30 дней (с 🔴)"""
    log.info(f'cmd_items_full called by chat_id={update.effective_chat.id if update.effective_chat else None}, args={ctx.args}')
    conn = get_db()
    try:
        all_items = conn.execute('''
            SELECT id, name, brand, category_id, lifespan_months,
                   purchase_date, status, replace_after_months, replace_after_days, notes, attributes
            FROM items
            WHERE deleted_at IS NULL AND is_delivery = 0
              AND data_origin IN ('manual', 'local', 'vision_photo')
            ORDER BY category_id, name
        ''').fetchall()
        # Загружаем фото (file_path из media_assets)
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
        await update.message.reply_text('📭 В инвентаре пока нет вещей.')
        return

    today = datetime.now().date()
    args = ' '.join(ctx.args).lower() if ctx.args else ''

    if args == 'all':
        filtered = all_items
    elif args:
        # Фильтр по названию, бренду, описанию, категории
        filtered = []
        for r in all_items:
            name = (r[1] or '').lower()
            brand = (r[2] or '').lower()
            cat = (r[3] or '').lower()
            notes = (r[9] or '').lower()
            attrs = {}
            try:
                attrs = json.loads(r[10] or '{}')
            except json.JSONDecodeError:
                pass
            desc = (attrs.get('description') or '').lower()
            tags = ' '.join(attrs.get('style_tags', [])).lower()
            
            # Ищем во всех полях
            search_text = f'{name} {brand} {cat} {notes} {desc} {tags}'
            if args in search_text:
                filtered.append(r)
    else:
        # По умолчанию: только с заменой <30 дней (🔴)
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
        await update.message.reply_text('📭 Ничего не найдено. Используй /items_full all или /items_full <название>')
        return

    cat_names = {
        'cat_clo_everyday': '👕 Повседневная одежда',
        'cat_clo_underwear': '👙 Нижнее бельё / носки',
        'cat_clo_shoes': '👟 Обувь',
        'cat_clo_access': '🧣 Аксессуары',
        'cat_tech': '💻 Техника',
        'cat_home': '🏠 Хозтовары',
        'cat_home_furn': '🪑 Мебель',
        'cat_home_kitchen': '🍳 Кухня',
        'cat_cosmetics': '🧴 Косметика',
        'cat_health_med': '💊 Здоровье',
        'cat_culture_books': '📚 Книги',
        'cat_hobbies': '🎮 Хобби',
        'cat_pets': '🐾 Животные',
        'cat_sport': '🏋️ Спорт',
        'cat_auto': '🚗 Авто',
        'cat_food': '🍎 Продукты',
        'cat_other': '📦 Прочее',
    }

    # Отправляем каждый item отдельным сообщением (с фото если есть)
    import asyncio
    for idx, r in enumerate(filtered):
        item_id = r[0]
        # Задержка между сообщениями чтобы избежать rate limit
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

        # Заголовок
        header = f'*{esc_md(name)}*'
        if brand:
            header += f' ({esc_md(brand)})'

        # Статус замены
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
                    status_line = '🔴 *ПОРА МЕНЯТЬ!*'
                elif days <= 30:
                    status_line = f'🟡 Замена через *{days} дн.*'
                else:
                    status_line = f'🟢 Замена через {days} дн.'
            except (TypeError, ValueError):
                pass

        # Детали
        details = []
        cat_label = cat_names.get(cat, cat)
        details.append(f'📂 {cat_label}')
        if attrs.get('color'):
            details.append(f'🎨 Цвет: {attrs["color"]}')
        if attrs.get('material'):
            details.append(f'🧵 Материал: {attrs["material"]}')
        if attrs.get('description'):
            details.append(f'📝 {attrs["description"]}')
        if attrs.get('style_tags'):
            details.append(f'🏷️ Теги: {", ".join(attrs["style_tags"])}')
        if attrs.get('estimated_price_rub'):
            details.append(f'💰 Оценка: ~{attrs["estimated_price_rub"]} ₽')
        if notes:
            # Убираем служебные строки и Vision-данные (уже показаны в attributes)
            clean_notes = notes.replace('Добавлено через /add_item', '').strip()
            # Убираем строки с цветом, материалом, описанием, ценой (дубли из Vision)
            for prefix in ['Цвет:', 'Материал:', 'Описание:', 'Оценочная цена:']:
                clean_notes = '\n'.join(
                    line for line in clean_notes.split('\n') 
                    if not line.strip().startswith(prefix)
                ).strip()
            if clean_notes:
                details.append(f'📋 {clean_notes[:200]}')

        text = f'{header}\n'
        if status_line:
            text += f'{status_line}\n'
        text += '\n'.join(details)
        text += f'\n\nID: `{item_id}`'

        # Формируем кнопки
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = []
        
        # Кнопка фото если есть
        photo_path = photos.get(item_id)
        has_photo = photo_path and os.path.exists(photo_path)
        
        if has_photo:
            buttons.append(InlineKeyboardButton('📷 Фото', callback_data=f'item_photo:{item_id}'))
        
        # Кнопка удаления если замена <30 дней
        if status_line and ('🔴' in status_line or '🟡' in status_line):
            buttons.append(InlineKeyboardButton('🗑 Удалить', callback_data=f'item_delete:{item_id}'))
        
        kb = InlineKeyboardMarkup([buttons]) if buttons else None

        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🛒 Consumption Agent\n\n'
        'Команды:\n'
        '/list — инвентарь по категориям\n'
        '/alerts — алерты (гарантии, сроки)\n'
        '/find_car 3ч 80км — подбор тарифа каршеринга\n'
        '/last_drives — последние поездки каршеринга (все провайдеры)\n'
        '/debts — проверка кредитов и займов по почтам + SMS\n'
        '/fines — неоплаченные штрафы\n'
        '/dayexp — расходы за сегодня с расшифровкой\n'
        '/monthexp — расходы за месяц с расшифровкой по дням\n'
        '/warranties — отчёт по гарантиям\n'
        '/add <название> [<цена>] [<категория>] — добавить товар\n'
        '/add_photo — загрузить фото чека (OCR)\n'
        '/check — статистика\n'
        '/add_item <название> [| бренд X] [| замена N мес] — добавить вещь в инвентарь\n'
        '/items [all|категория] — инвентарь вещей по категориям\n'
        '/ml_last [N] — последние записи Memory Lane\n'
        '/topic_set <слово> <тема> — задать тему для слова\n'
        '/topic_list [тема] — показать все правила тем\n'
        '/help — это сообщение'
    )


async def cmd_topic_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Установить тему для ключевого слова: /topic_set <слово> <тема>"""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
        return

    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text('Использование: /topic_set <слово> <тема>\nНапример: /topic_set кофемолка бытовая техника')
        return

    keyword = ctx.args[0].lower()
    topic = ' '.join(ctx.args[1:]).lower()

    conn = get_db()
    try:
        is_new = _ml.set_topic_rule(conn, keyword, topic)
        conn.commit()
    except Exception as e:
        await update.message.reply_text(f'\u274c Ошибка: {e}')
        return
    finally:
        conn.close()

    if is_new:
        await update.message.reply_text(f'\u2705 Добавлено правило: «{keyword}» \u2192 «{topic}»')
    else:
        await update.message.reply_text(f'\u2705 Обновлено правило: «{keyword}» \u2192 «{topic}»')


async def cmd_topic_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать все правила тем: /topic_list [тема]"""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
        return

    topic_filter = ' '.join(ctx.args).lower() if ctx.args else None

    conn = get_db()
    try:
        rules = _ml.list_topic_rules(conn, topic_filter)
    finally:
        conn.close()

    if not rules:
        if topic_filter:
            await update.message.reply_text(f'Правил для темы «{topic_filter}» не найдено.')
        else:
            await update.message.reply_text('Правил пока нет. Добавьте /topic_set <слово> <тема>')
        return

    # Группируем по темам
    groups = {}
    for r in rules:
        t = r['topic']
        if t not in groups:
            groups[t] = []
        icon = '\U0001f3f7' if r['source'] == 'user' else ''
        groups[t].append(f"{icon}{r['keyword']} ({r['usage_count']})")

    lines = [f'\U0001f9f9 Правила тем ({len(rules)}):']
    for topic in sorted(groups.keys()):
        kws = ', '.join(groups[topic])
        lines.append(f'\n\U0001f539 {topic}: {kws}')

    # Разбиваем на части если длинно
    full = '\n'.join(lines)
    if len(full) > 4000:
        for chunk in [full[i:i+4000] for i in range(0, len(full), 4000)]:
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(full)


async def cmd_ml_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать последние N записей Memory Lane (по умолчанию 10)."""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
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
            'Memory Lane пуст. Отправь фото с подписью «нравится» или #хэштегом, '
            'чтобы добавить запись.'
        )
        return

    lines = [f'🧠 Последние {len(rows)} записей:']
    for r in rows:
        cap = (r['caption'] or '').strip().replace('\n', ' ')
        if len(cap) > 60:
            cap = cap[:57] + '…'
        try:
            tags = json.loads(r['style_tags'] or '[]')
        except (TypeError, ValueError):
            tags = []
        tag_str = ' '.join(f'#{t}' for t in tags) if tags else ''
        topic = r['topic'] or '—'
        date = (r['created_at'] or '')[:10]
        has_photo = '📷' if r['media_asset_id'] else ''
        name = r['name'] or ''
        desc = (r['description'] or '')[:40] if r['description'] else ''
        name_part = f' {name}' if name else ''
        desc_part = f' — {desc}…' if desc else ''
        lines.append(f'#{r["id"]:>3}{has_photo} {date} [{topic}]{name_part}{desc_part}'.rstrip())
    await update.message.reply_text('\n'.join(lines))

    # Отправляем фото для записей, у которых есть media_asset_id
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
            caption_lines = [f'📌 {r["name"]}' if r['name'] else f'#{r["id"]}']
            if r['name']:
                caption_lines[0] = f'📌 {r["name"]}'
            else:
                caption_lines[0] = f'#{r["id"]}'
            if r['description']:
                caption_lines.append(r['description'])
            if r['caption']:
                cap = r['caption'].strip()
                if cap != (r['name'] or '') and not cap.startswith('#'):
                    caption_lines.append(cap)
            if r['topic']:
                caption_lines.append(f'📂 {r["topic"]}')
            caption_lines.append(f'🕒 {str(r["created_at"])[:10]}')
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton('🗑 Удалить', callback_data=f'ml_delete:{r["id"]}')
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
            await update.message.reply_text(f"❌ Item {item_id} not found")
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

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('credit_paid:'):
        return

    try:
        alert_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный alert id', show_alert=True)
        return

    row = get_credit_alert(alert_id)
    if not row:
        await query.answer('⚠️ Алерт не найден', show_alert=True)
        return

    if row['paid_confirmed_at']:
        await query.answer('✅ Уже отмечено как оплачено')
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    changed = confirm_credit_alert_paid(alert_id)
    if not changed:
        await query.answer('⚠️ Не удалось обновить статус', show_alert=True)
        return

    paid_note = '\n\n✅ <b>Отмечено как оплачено вручную</b>'
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
    await query.answer('✅ Платёж отмечен как оплаченный')


async def fine_paid_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '✅ Оплачено' для штрафов."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('fine_paid:'):
        return

    try:
        fine_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный id', show_alert=True)
        return

    row = get_fine(fine_id)
    if not row:
        await query.answer('⚠️ Штраф не найден', show_alert=True)
        return

    if row['paid_confirmed_at']:
        await query.answer('✅ Уже отмечено как оплачено')
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    changed = confirm_fine_paid(fine_id)
    if not changed:
        await query.answer('⚠️ Не удалось обновить статус', show_alert=True)
        return

    paid_note = '\n\n✅ <b>Отмечено как оплачено</b>'
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
    await query.answer('✅ Штраф отмечен как оплаченный')


async def item_replaced_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '✅ Заменено' для напоминаний о замене вещей."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_replaced:'):
        return

    try:
        alert_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный alert id', show_alert=True)
        return

    conn = get_db()
    try:
        # Получаем alert и связанный item
        alert = conn.execute(
            'SELECT item_id FROM alerts WHERE id = ? AND alert_type = ?',
            (alert_id, 'replace_reminder')
        ).fetchone()
        if not alert:
            await query.answer('⚠️ Алерт не найден', show_alert=True)
            return

        item_id = alert['item_id']

        # Помечаем item как заменённый
        conn.execute(
            "UPDATE items SET status = 'replaced', updated_at = datetime('now') WHERE id = ?",
            (item_id,)
        )
        # Закрываем алерт
        conn.execute(
            "UPDATE alerts SET status = 'actioned' WHERE id = ?",
            (alert_id,)
        )
        conn.commit()

        # Обновляем сообщение
        replaced_note = '\n\n✅ <b>Заменено</b>'
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
        await query.answer('✅ Отмечено как заменённое')
    except Exception as e:
        log.warning(f'item_replaced_callback failed: {e}')
        await query.answer('⚠️ Ошибка при обновлении', show_alert=True)
    finally:
        conn.close()


async def item_delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '🗑 Удалить' для удаления item из инвентаря."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_delete:'):
        return

    try:
        item_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный item id', show_alert=True)
        return

    conn = get_db()
    try:
        # Soft delete — помечаем deleted_at
        conn.execute(
            "UPDATE items SET deleted_at = datetime('now'), status = 'disposed' WHERE id = ?",
            (item_id,)
        )
        conn.commit()

        # Обновляем сообщение
        deleted_note = '\n\n🗑 <b>Удалено из инвентаря</b>'
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
        await query.answer('🗑 Удалено')
    except Exception as e:
        log.warning(f'item_delete_callback failed: {e}')
        await query.answer('⚠️ Ошибка при удалении', show_alert=True)
    finally:
        conn.close()


async def ml_delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '🗑 Удалить' для Memory Lane записей в /ml_last."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('ml_delete:'):
        return

    try:
        ml_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный id', show_alert=True)
        return

    conn = get_db()
    try:
        # Получаем media_asset_id для удаления
        row = conn.execute(
            'SELECT media_asset_id FROM memory_lane_items WHERE id = ?', (ml_id,)
        ).fetchone()
        if not row:
            await query.answer('⚠️ Запись не найдена', show_alert=True)
            return

        media_asset_id = row[0]

        # Удаляем запись из memory_lane_items
        conn.execute('DELETE FROM memory_lane_items WHERE id = ?', (ml_id,))

        # Удаляем связанный media_asset (файл + запись в БД)
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

        # Обновляем сообщение (убираем фото, меняем подпись)
        try:
            await query.edit_message_caption(
                caption=f'🗑 Запись #{ml_id} удалена',
                reply_markup=None
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

        await query.answer('🗑 Запись удалена')
    except Exception as e:
        log.warning(f'ml_delete_callback failed: {e}')
        await query.answer('⚠️ Ошибка при удалении', show_alert=True)
    finally:
        conn.close()


async def vision_confirm_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '✅ Подтвердить' — товар уже в БД, просим доп. информацию."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    pending = ctx.user_data.get('vision_pending')
    if not pending:
        await query.answer('⚠️ Данные не найдены', show_alert=True)
        return

    # Убираем кнопки и обновляем сообщение
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await query.edit_message_text(
        query.message.text + '\n\n✅ Добавлено в инвентарь',
        reply_markup=None
    )
    await query.answer('✅ Сохранено')

    # Запрашиваем дополнительную информацию через ForceReply
    from telegram import ForceReply
    await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text='📝 Введите дополнительную информацию о товаре (бренд, размер, материал):',
        reply_markup=ForceReply(selective=True),
        reply_to_message_id=query.message.message_id
    )
    # Сохраняем item_id для обработки ответа
    ctx.user_data['vision_awaiting_notes'] = pending.get('item_id')


async def vision_reject_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '❌ Отклонить' — удаляет товар из БД и фото."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    pending = ctx.user_data.pop('vision_pending', None)
    if not pending:
        await query.answer('⚠️ Данные не найдены', show_alert=True)
        return

    item_id = pending.get('item_id')
    asset_id = pending.get('asset_id')
    receipt_path = pending.get('receipt_path')

    conn = get_db()
    try:
        # Удаляем фото из media_assets
        if asset_id:
            ma_row = conn.execute('SELECT file_path FROM media_assets WHERE id = ?', (asset_id,)).fetchone()
            conn.execute('DELETE FROM media_assets WHERE id = ?', (asset_id,))
            if ma_row and os.path.exists(ma_row[0]):
                try:
                    os.remove(ma_row[0])
                except Exception:
                    pass

        # Удаляем связь item_photos
        if item_id:
            conn.execute('DELETE FROM item_photos WHERE item_id = ?', (item_id,))
            # Soft delete item
            conn.execute(
                "UPDATE items SET deleted_at = datetime('now'), status = 'disposed' WHERE id = ?",
                (item_id,)
            )

        conn.commit()

        # Удаляем временный файл
        if receipt_path and os.path.exists(receipt_path):
            try:
                os.remove(receipt_path)
            except Exception:
                pass

        # Убираем кнопки и обновляем сообщение
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        await query.edit_message_text(
            query.message.text + '\n\n❌ Отклонено и удалено',
            reply_markup=None
        )
        await query.answer('❌ Удалено')
    except Exception as e:
        log.warning(f'vision_reject_callback failed: {e}')
        await query.answer('⚠️ Ошибка при удалении', show_alert=True)
    finally:
        conn.close()


async def item_photo_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '📷 Фото' — отправляет фото товара."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id != OWNER_CHAT_ID:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_photo:'):
        return

    try:
        item_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный item id', show_alert=True)
        return

    # Загружаем фото из БД
    conn = get_db()
    try:
        row = conn.execute('''
            SELECT ma.file_path 
            FROM item_photos ip
            JOIN media_assets ma ON ip.media_asset_id = ma.id
            WHERE ip.item_id = ? LIMIT 1
        ''', (item_id,)).fetchone()
        if not row:
            await query.answer('📭 Фото не найдено', show_alert=True)
            return

        photo_path = row[0]
        if not os.path.exists(photo_path):
            await query.answer('📭 Фото не найдено', show_alert=True)
            return

        # Отправляем фото ответом на сообщение
        with open(photo_path, 'rb') as f:
            await ctx.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=f.read(),
                caption=f'📷 Фото товара ID: {item_id}'
            )
        await query.answer('📷 Фото отправлено')
    except Exception as e:
        log.warning(f'item_photo_callback failed: {e}')
        await query.answer('⚠️ Ошибка при отправке фото', show_alert=True)
    finally:
        conn.close()


def add_authorized_handler(app: Application, handler):
    """Оборачивает handler проверкой доступа перед добавлением в приложение."""
    original_callback = handler.callback

    async def deny_access(update: Update):
        chat = update.effective_chat if update else None
        chat_id = chat.id if chat else None
        log.warning('Доступ запрещён для chat_id=%s', chat_id)

        if getattr(update, 'callback_query', None):
            await update.callback_query.answer('❌ Доступ запрещён', show_alert=True)
            return

        message = getattr(update, 'effective_message', None)
        if message is not None:
            await message.reply_text('❌ Доступ запрещён.')

    async def wrapped_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat if update else None
        if chat is None or chat.id != OWNER_CHAT_ID:
            await deny_access(update)
            return
        return await original_callback(update, ctx)

    handler.callback = wrapped_callback
    app.add_handler(handler)

def main():
    # Инициализируем schema memory_lane (создаём таблицы topic_rules и др., если ещё нет)
    try:
        import memory_lane as _ml
        conn = get_db()
        _ml.ensure_memory_lane_schema(conn)
        conn.close()
    except Exception as e:
        log.warning(f'memory_lane schema init failed: {e}')

    if not TOKEN:
        print('❌ Укажите CONSUMPTION_BOT_TOKEN')
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
