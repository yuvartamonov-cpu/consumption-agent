from __future__ import annotations

import logging
import re
import subprocess

log = logging.getLogger(__name__)


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


def _write_text_file(path: str, content: str) -> None:
    """Write text content to a file (utility for tests and exports)."""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


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
