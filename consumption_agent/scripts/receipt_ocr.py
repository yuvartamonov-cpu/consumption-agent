#!/usr/bin/env python3
"""
receipt_ocr.py — качественное распознавание чеков.

Пайплайн:
  1. Предобработка изображения (PIL): градации серого, повышение контраста,
     бинаризация, увеличение DPI, удаление шума, выравнивание перспективы.
  2. Tesseract OCR: несколько вариантов + PSM, выбор лучшего.
  3. Парсинг: Ozon (1×цена), Яндекс.Маркет, Самокат, обычные чеки.
  4. Матчинг товаров с категориями.

Использование:
    from receipt_ocr import process_receipt
    result = process_receipt('/path/to/photo.jpg')
    # result = {'shop': 'Ozon', 'items': [...], 'total': 851.0, 'date': '2026-05-11'}
"""

import logging, os, re, subprocess, json, tempfile
from datetime import date
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ReceiptItem:
    name: str
    price: float
    qty: int = 1
    total: float = 0.0

    def __post_init__(self):
        if self.total == 0.0:
            self.total = self.price * self.qty


_DELIVERY_KEYWORDS = ['курьерская доставка', 'доставка', 'доставк', 'курьер',
                       'shipping', 'delivery', 'почт', 'postage']


@dataclass
class ReceiptResult:
    shop: str = ''
    date: str = ''
    total: Optional[float] = None
    items: list = field(default_factory=list)
    delivery_cost: float = 0.0
    raw_text: str = ''
    ocr_score: int = 0


# ─────────────────────────────────────────────────────────────
# Garbage / noise filters
# ─────────────────────────────────────────────────────────────

_NOISE_PATTERNS = [
    r'^[A-Za-z]{2,5}\s+[A-Za-z]\s+[A-Za-z]',
    r'[A-Z]{3,}',                     # 3+ заглавных латинских подряд
]

_GARBAGE_CHARS = 'eEиcSChHaAкКВuUаa'

_KNOWN_JUNK = [
    'ae eee', 'aes ae', 'ee сини', 'ee ee ee',
    'чинно чинно', 'синие синие', 'сииине', 'бииине',
    'ee ee', 'eee', 'ae', 'aes', 'a cS', 'Ce A',
]

_NOT_NAME_KEYWORDS = [
    'ИТОГ', 'вт.ч', 'НДС', 'HOC', 'расчет', 'Зачет',
    'ФН:', 'PH ККТ', 'ФД:', 'ФПД', 'Сайт', 'ИНН',
    'Код маркировки',
    'Полный', 'Кассовый чек', 'ПИ', 'Приход',
    'Вид налогообложения', 'Интернет Решения',
    'Безналичными', 'Наличными',
    'продавца',
    'маркировки',
    'проверки',
]

_RECEIPT_HEADER_MARKERS = (
    'гостевой счет',
    'кассовый чек',
    'товарный чек',
    'счет',
    'наименование',
    'итого',
)


def _is_junk_line(line: str) -> bool:
    """Проверяет, является ли строка мусором OCR."""
    line = line.strip()
    if not line or len(line) < 3:
        return True

    # Цифры и символы — не мусор, но не название
    if re.match(r'^[\d,.#\s₽€$£/=\-]+$', line):
        return True

    # Служебные строки
    for kw in _NOT_NAME_KEYWORDS:
        if kw in line:
            return True

    # Повторяющиеся символы
    if max(line.count(c) for c in set(line)) > len(line) * 0.5:
        return True

    # Мало уникальных символов
    if len(line) > 10 and len(set(line)) < 6:
        return True

    # Латинский шум
    if re.match(r'^[A-Za-z]{2,5}\s+[A-Za-z]', line) and len(line) > 15:
        return True
    if re.search(r'[A-Z]{3,}', line):
        return True

    # Символы шума (>60%)
    nonsense = sum(line.count(c) for c in _GARBAGE_CHARS)
    if nonsense > len(line) * 0.6:
        return True

    # Известный мусор
    for junk in _KNOWN_JUNK:
        if junk in line:
            return True

    # Начинается с ИНН (типично: после названия товара идёт 'ИНН продавца: ...')
    if line.startswith('ИНН'):
        return True
    # Строки вида "Кальянова Валентина Борисовна, ИП" — продавец, не товар
    if ', ИП' in line:
        return True
    if 'ООО' in line and re.search(r'[А-Я]{4,}', line) and len(line) < 40:
        # "ООО ТОЧНО В СРОК" — юрлицо, не товар (если короткая строка)
        return True

    return False


def _clean_shop_header_line(line: str) -> str:
    line = re.sub(r'[*#=_]{2,}', ' ', line or '')
    line = re.sub(r'[-]{2,}', ' ', line)
    line = re.sub(r'\s+', ' ', line)
    return line.strip(" -_*#\t")


def _extract_shop_from_header(text: str) -> str:
    lower = text.lower()
    if not any(marker in lower for marker in _RECEIPT_HEADER_MARKERS):
        return ''

    header_lines = []
    for raw_line in text.splitlines()[:8]:
        line = _clean_shop_header_line(raw_line)
        if not line:
            continue
        line_lower = line.lower()
        if any(marker in line_lower for marker in _RECEIPT_HEADER_MARKERS[1:]):
            break
        header_lines.append(line)

    for line in header_lines:
        line_lower = line.lower()
        if len(line) < 3 or len(line) > 40:
            continue
        if any(ch.isdigit() for ch in line):
            continue
        if ':' in line or 'qr' in line_lower or 'чаевые' in line_lower:
            continue
        if _is_junk_line(line):
            continue
        return line

    return ''


# ─────────────────────────────────────────────────────────────
# Image preprocessing
# ─────────────────────────────────────────────────────────────

def preprocess_image(image_path: str) -> list[str]:
    """Создаёт несколько вариантов изображения для OCR."""
    variants = [image_path]
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        img = Image.open(image_path)
        gray = ImageOps.grayscale(img)

        # 1. Просто серый
        gray_path = image_path.rsplit('.', 1)[0] + '_gray.png'
        gray.save(gray_path)
        variants.append(gray_path)

        # 2. Увеличенный + контраст
        up = gray.resize((gray.width * 3, gray.height * 3))
        up = ImageOps.autocontrast(up)
        up = ImageEnhance.Contrast(up).enhance(2.4)
        up_path = image_path.rsplit('.', 1)[0] + '_up.png'
        up.save(up_path)
        variants.append(up_path)

        # 3. Бинаризация
        bw = up.point(lambda x: 0 if x < 170 else 255)
        bw = bw.filter(ImageFilter.SHARPEN)
        bw_path = image_path.rsplit('.', 1)[0] + '_bw.png'
        bw.save(bw_path)
        variants.append(bw_path)

        # 4. Сильное увеличение (5x) для мелких чеков
        huge = gray.resize((gray.width * 5, gray.height * 5))
        huge = ImageOps.autocontrast(huge)
        huge = ImageEnhance.Contrast(huge).enhance(3.0)
        huge_path = image_path.rsplit('.', 1)[0] + '_huge.png'
        huge.save(huge_path)
        variants.append(huge_path)

    except Exception as e:
        log.warning(f"Image preprocessing failed: {e}")

    return variants


def _score_ocr_text(text: str) -> int:
    """Оценивает качество OCR-текста: чем больше, тем лучше."""
    if not text:
        return 0
    lower = text.lower()
    score = 0

    # Количество цифр, латинских и русских слов
    score += len(re.findall(r'\d', text))
    score += len(re.findall(r'\b[A-Za-z]{3,}\b', text)) * 3
    score += len(re.findall(r'\b[А-Яа-яЁё]{3,}\b', text)) * 2

    # Маркеры чека
    score += len(re.findall(r'[₽€$£]|\b(?:EUR|USD|RUB)\b', text, flags=re.I)) * 8

    # Сильные сигналы чека
    for kw in ['кассовый чек', 'фискальный', 'фн', 'фп', 'итог', 'безналичными',
               'приход', 'итого', 'наличными']:
        if kw in lower:
            score += 120

    # Номер чека, ИНН, ФН
    if re.search(r'\b\d{4,6}[/ -]\d{6,10}\b', text):
        score += 120
    if re.search(r'\b\d{12,14}\b', text):
        score += 60

    # Формат Ozon: "1 x ..."
    if re.search(r'\d+\s*[x×]\s*\d+[.,]\d{2}', text):
        score += 80

    # Есть русские слова
    if re.search(r'[А-Яа-яЁё]{2,}', text):
        score += 30

    return score


# ─────────────────────────────────────────────────────────────
# OCR engine
# ─────────────────────────────────────────────────────────────

def run_tesseract(image_path: str, lang: str = 'rus+eng', psm: str = '6',
                  timeout: int = 30) -> str:
    """Запускает Tesseract на одном изображении."""
    try:
        result = subprocess.run(
            ['tesseract', image_path, 'stdout', '-l', lang,
             '--oem', '1', '--psm', psm],
            capture_output=True, text=True, check=False, timeout=timeout
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning(f"Tesseract failed ({image_path}, psm={psm}): {e}")
        return ''


def ocr_best(image_path: str) -> tuple[str, int]:
    """Запускает OCR на всех вариантах + PSM, возвращает лучший текст и его оценку."""
    best_text = ''
    best_score = -1

    variants = preprocess_image(image_path)

    for vpath in variants:
        for psm in ('6', '11'):
            text = run_tesseract(vpath, psm=psm)
            if not text:
                continue
            score = _score_ocr_text(text)
            if score > best_score:
                best_text = text
                best_score = score

    if best_score < 0:
        log.error("OCR failed completely")
        return '', 0

    return _clean_text(best_text), best_score


def _clean_text(text: str) -> str:
    """Базовая очистка текста."""
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r'[^\w\s.,%₽€$£/#:=\-×x]', ' ', raw_line, flags=re.UNICODE)
        line = re.sub(r'\s+', ' ', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# Parser: items from text
# ─────────────────────────────────────────────────────────────

def parse_items(text: str) -> tuple[list[ReceiptItem], float]:
    """Парсит товары из текста чека.
    Возвращает (items, delivery_cost).
    """
    lines = text.split('\n')

    # Сначала ищем формат Ozon: "1 x 721,00" с названием на строках выше
    items, delivery_cost = _parse_ozon_format(lines)

    # Если не нашли — пробуем стандартный: "Название 123.45₽"
    if not items:
        items, dc = _parse_standard_format(lines)
        delivery_cost = dc

    return items, delivery_cost or 0.0


def _parse_ozon_format(lines: list[str]) -> tuple[list[ReceiptItem], float]:
    """Формат Ozon/курьеры: '1 x 721,00', название на 1-2 строках выше.
    Возвращает (items, delivery_cost).
    """
    items = []
    delivery_cost = 0.0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        qty_match = re.match(r'^(\d+)\s*[x×]\s*(\d+[.,]\d{2})$', line)
        if not qty_match:
            continue

        qty = int(qty_match.group(1))
        price = float(qty_match.group(2).replace(',', '.'))
        total = qty * price

        # Название: ищем строки выше (до 4 строк назад)
        name_parts = []
        stopped_at_product_info = False
        for j in range(i - 2, max(i - 6, -1), -1):
            if j < 0:
                break
            prev = lines[j].strip()
            if _is_junk_line(prev):
                continue
            if re.match(r'^#?\d+[.,]\d{2}$', prev):  # строка-итог позиции
                continue
            if re.match(r'^[=]\d+[.,]\d{2}', prev):  # "=34,33" (НДС)
                continue
            # Весомость строки (русские слова > 3 символов)
            meaningful = len(re.findall(r'[А-Яа-яЁё]{3,}', prev))
            # Если это первая найденная строка (ближайшая к цене) и она начинается
            # с маленькой буквы или с запятой — это продолжение названия, ищем выше
            is_first = len(name_parts) == 0
            is_continuation = is_first and (prev[0].islower() or prev[0] in ',;:')

            # Добавляем строку в название в любом случае
            name_parts.insert(0, prev)

            # Если строка выглядит как продолжение — не останавливаемся, ищем выше
            if is_continuation and meaningful >= 2:
                continue

            # Строка с 3+ русскими словами и > 35 символов — стоп
            if meaningful >= 3 and len(prev) > 30:
                stopped_at_product_info = True
                break
            if len(prev) > 65:
                stopped_at_product_info = True
                break

        name = ' '.join(name_parts).strip()
        if not name:
            name = f'Товар {len(items) + 1}'

        # Детект доставки — не товар, а расход на доставку
        if _is_delivery(name):
            delivery_cost += total
            continue

        items.append(ReceiptItem(name=name[:120], price=price, qty=qty))

    return items, delivery_cost


def _is_delivery(name: str) -> bool:
    """Проверяет, является ли строка доставкой (Курьерская доставка и т.п.)."""
    lower = name.lower()
    for kw in _DELIVERY_KEYWORDS:
        if kw in lower:
            return True
    return False


def _parse_standard_format(lines: list[str]) -> tuple[list[ReceiptItem], float]:
    """Стандартный формат: 'Название товара 123.45₽'.
    Возвращает (items, delivery_cost).
    """
    items = []
    delivery_cost = 0.0
    for line in lines:
        line = line.strip()
        if _is_junk_line(line):
            continue

        # "Название 123.45₽"
        m = re.search(r'(.{3,60}?)\s+(\d+[.,]\d{2})\s*₽', line)
        if m:
            name, price_str = m.groups()
            name = name.strip()
            if name and not _is_junk_line(name):
                price = float(price_str.replace(',', '.'))
                if _is_delivery(name):
                    delivery_cost += price
                else:
                    items.append(ReceiptItem(name=name[:120], price=price))

    return items, delivery_cost


def parse_total(text: str) -> Optional[float]:
    """Извлекает итоговую сумму из текста."""
    # ИТОГ / ИТОГО
    for pattern in [r'ИТОГ[О]?\s*[:\s]*\s*(\d+[.,]\d{2})',
                    r'ИТОГ[О]?\s*[:\s]*\s*(\d+)']:
        m = re.search(pattern, text)
        if m:
            try:
                val = m.group(1).replace(',', '.')
                return float(val)
            except ValueError:
                pass

    return None


def parse_date(text: str) -> str:
    """Извлекает дату из текста чека.
    Российские чеки: DD.MM.YYYY (11.05.2026).
    ISO: YYYY-MM-DD.
    """
    patterns = [
        # DD.MM.YYYY (российский формат)
        (r'(\d{2})[.](\d{2})[.](\d{4})', 'dmy'),
        # DD/MM/YYYY
        (r'(\d{2})/(\d{2})/(\d{4})', 'dmy'),
        # YYYY-MM-DD (ISO)
        (r'(\d{4})-(\d{2})-(\d{2})', 'ymd'),
        # DD.MM.YY
        (r'(\d{2})[.](\d{2})[.](\d{2})', 'dmy_short'),
    ]
    for pat, fmt in patterns:
        m = re.search(pat, text)
        if m:
            a, b, c = m.groups()
            if fmt == 'dmy':
                return f'{c}-{b}-{a}'
            elif fmt == 'ymd':
                return f'{a}-{b}-{c}'
            elif fmt == 'dmy_short':
                return f'20{c}-{b}-{a}'
    return date.today().isoformat()


# ─────────────────────────────────────────────────────────────
# Main processing
# ─────────────────────────────────────────────────────────────

def process_receipt(image_path: str) -> ReceiptResult:
    """Полный пайплайн обработки фото чека."""
    # 1. OCR
    text, score = ocr_best(image_path)
    if score < 30:
        return ReceiptResult(raw_text=text, ocr_score=score)

    # 2. Парсинг товаров (доставка отдельно)
    items, delivery_cost = parse_items(text)

    # 3. Сумма
    items_total = sum(it.total for it in items)
    total = parse_total(text) or (items_total + delivery_cost if items or delivery_cost else None)

    # 4. Дата
    receipt_date = parse_date(text)

    # 5. Определение магазина
    shop = _detect_shop(text)

    return ReceiptResult(
        shop=shop,
        date=receipt_date,
        total=total,
        items=items,
        delivery_cost=delivery_cost,
        raw_text=text,
        ocr_score=score,
    )


def _detect_shop(text: str) -> str:
    """Определяет магазин по тексту чека."""
    lower = text.lower()
    if 'ozon' in lower:
        return 'Ozon'
    if 'yandex' in lower or 'яндекс' in lower:
        return 'Яндекс'
    if 'samokat' in lower or 'самокат' in lower:
        return 'Самокат'
    if 'wildberries' in lower or 'wildberri' in lower or 'wb' in lower:
        return 'Wildberries'
    if 'megamarket' in lower or 'megam' in lower:
        return 'Megamarket'
    if 'автозапра' in lower or 'азс' in lower or 'nefte' in lower:
        return 'АЗС'
    if 'магнит' in lower:
        return 'Магнит'
    if 'пятёрочка' in lower or 'пятерочка' in lower:
        return 'Пятёрочка'
    if 'перекрёсток' in lower or 'перекресток' in lower:
        return 'Перекрёсток'
    if 'ашан' in lower or 'auchan' in lower:
        return 'Ашан'
    if 'лента' in lower:
        return 'Лента'
    if 'метро' in lower or 'metro' in lower:
        return 'METRO'
    if 'fix price' in lower or 'фикс' in lower:
        return 'Fix Price'
    if 'dns' in lower and 'магазин' in lower:
        return 'DNS'
    return _extract_shop_from_header(text)


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Распознавание чеков')
    parser.add_argument('image', help='Путь к изображению чека')
    parser.add_argument('--debug', action='store_true', help='Показать сырой OCR')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(levelname)s: %(message)s')

    result = process_receipt(args.image)

    print(f"\n{'='*50}")
    print(f"🧾 Чек распознан")
    if result.shop:
        print(f"   Магазин: {result.shop}")
    print(f"   Дата: {result.date}")
    print(f"   Итого: {result.total} ₽" if result.total else "   Итого: не определена")
    print(f"   OCR score: {result.ocr_score}")
    if result.items:
        print(f"\n   Товары ({len(result.items)}):")
        for it in result.items:
            print(f"     • {it.name} — {it.price} ₽ × {it.qty}")
    else:
        print("\n   Товары: не найдены")

    if result.raw_text and args.debug:
        print(f"\n{'='*50}")
        print("Сырой OCR:")
        print(result.raw_text)


if __name__ == '__main__':
    main()
