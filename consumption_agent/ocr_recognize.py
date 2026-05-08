#!/usr/bin/env python3
"""
OCR for Consumption Agent — распознаёт текст с изображений (чеки, скриншоты Ozon).
Принимает: путь к изображению
Возвращает: список потенциальных товаров
"""
import sys
import os
import re
from PIL import Image, ImageEnhance
import pytesseract

# Подключаем БД consumption для записи результатов
DB_PATH = os.path.join(os.path.dirname(__file__), 'consumption.db')

NOISE_PATTERNS = [
    r'сообщени[ея]\s+продав', r'перейти\s+в\s+ча[тч]', r'выберите\s+товар',
    r'экспресс\s+достав', r'просмотреть\s+чек', r'разрешить\s+торг',
    r'\bип\b', r'\bооо\b', r'издательств', r'\bstore\b', r'\bgroup\b',
    r'январ', r'феврал', r'март', r'апрел', r'мая', r'июн', r'июл', r'август',
    r'сентябр', r'октябр', r'ноябр', r'декабр',
    r'http', r'www\.', r'@', r'\.ru\b', r'\.su\b', r'openclaw', r'laptop-',
    r'python3', r'curl', r'netsh', r'adb\b', r'syntaxerror', r'windows\\system32',
    r'fon\.bet', r'stoloto', r'nic\.ru', r'домен', r'ломен', r'эскроу', r'btc', r'usdt',
    r'цена\s+по\s+запросу', r'регистратор', r'блокиров', r'претенз', r'бизнес',
    r'потоки\s+данных', r'диагностик', r'мир\s+электроники',
    r'инн', r'кассов', r'налог', r'без\s*ндс', r'фн\b', r'фд\b', r'фпд\b'
]

PRODUCT_KEYWORDS = [
    'корм', 'собак', 'кошк', 'симпар', 'грандорф', 'мяч', 'валик', 'коврик',
    'йог', 'пилатес', 'диск', 'блок', 'контейнер', 'столик', 'комод', 'шкаф',
    'костюм', 'плать', 'бель', 'балаклав', 'картридж', 'книга', 'тетрад',
    'хоттабыч', 'успенск', 'сартр', 'лодка', 'камертон', 'клей', 'батарей', 'гирлянд',
    'кубик', 'респиратор', 'грунтован', 'холст', 'нильса', 'крокодил', 'королевств',
    'тюбинг', 'вибратор', 'страпон', 'эрот', 'кольцо', 'турка', 'кофевар',
    'бумаг', 'пакет', 'держател', 'средство', 'стабилиз', 'елк', 'ёлк',
    'подписк', 'premium', 'hp', 'принтер', 'стол', 'кровать'
]


def normalize_text(text):
    text = text.replace('ё', 'е').replace('Ё', 'Е')
    text = re.sub(r'[|`~<>_=]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(" .,:;!?-–—_()[]{}\"'“”«»")


def has_product_keyword(text):
    lowered = text.lower()
    return any(keyword in lowered for keyword in PRODUCT_KEYWORDS)


def is_noise_line(text):
    if not text:
        return True
    lowered = text.lower()
    if len(text) < 6:
        return True
    if sum(ch.isalpha() for ch in text) < 4:
        return True
    if any(re.search(pattern, lowered, re.I) for pattern in NOISE_PATTERNS):
        return True
    if not has_product_keyword(text):
        words = [w for w in re.split(r'\s+', text) if len(w) > 1]
        if len(words) < 2:
            return True
        if sum(ch.isdigit() for ch in text) >= 6:
            return True
    return False


def preprocess_image(img_path, output_path=None):
    """
    Улучшаем изображение для OCR: масштаб, серый, контраст, бинаризация.
    """
    img = Image.open(img_path)
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)

    if img.mode != 'L':
        img = img.convert('L')

    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.point(lambda x: 0 if x < 160 else 255, '1')

    if output_path:
        img.save(output_path)

    return img


def ocr_image(img_path, lang='rus+eng'):
    """
    Распознаёт текст с изображения.
    Возвращает сырой текст, цены и очищенный список товаров.
    """
    try:
        processed = preprocess_image(img_path)
        raw_text = pytesseract.image_to_string(processed, lang=lang)
        if len(raw_text.strip()) < 20:
            raw_text = pytesseract.image_to_string(Image.open(img_path), lang=lang)

        prices = re.findall(r'(\d[\d\s]*[.,]\d{2})', raw_text)
        lines = [normalize_text(line) for line in raw_text.split('\n')]

        products = []
        seen = set()
        for line in lines:
            line = normalize_text(line)
            if not line:
                continue
            if is_noise_line(line):
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            products.append(line)

        return raw_text, prices, products

    except Exception as e:
        print(f'Ошибка OCR: {e}', file=sys.stderr)
        return '', [], []


def save_to_db(img_path, raw_text, products):
    """Сохраняет результат OCR в recognized_items_log."""
    import sqlite3
    
    try:
        conn = sqlite3.connect(DB_PATH)
        count = 0
        
        for product in products:
            cat_id = guess_category(product)
            conn.execute("""
                INSERT INTO recognized_items_log (source_file, source_type, recognized_product,
                                                   confidence, notes, matched_item_id)
                VALUES (?, 'screen_ocr', ?, 'high', ?, NULL)
            """, (os.path.basename(img_path), product,
                  f'OCR распознано: {raw_text[:100]}'))
            count += 1
        
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        print(f'Ошибка записи в БД: {e}', file=sys.stderr)
        return 0


def guess_category(name):
    """Определяет категорию по названию."""
    name_l = name.lower()
    if any(w in name_l for w in ['корм', 'симпарик', 'ветеринар', 'животн', 'собак', 'кошк',
                                  'грандорф', 'мосенда']):
        return 'cat_pets'
    if any(w in name_l for w in ['лекарств', 'таблетк', 'витамин', 'бад']):
        return 'cat_health_med'
    if any(w in name_l for w in ['премиум', 'подписк']):
        return 'cat_subscriptions'
    if any(w in name_l for w in ['стол', 'стул', 'кроват', 'шкаф', 'мебель', 'стремян',
                                  'насосн', 'камин', 'ёлк', 'елк']):
        return 'cat_home_furn'
    if any(w in name_l for w in ['телефон', 'наушник', 'зарядк']):
        return 'cat_tech'
    if any(w in name_l for w in ['крем', 'шампун', 'мыло', 'лосьон']):
        return 'cat_cosmetics'
    if any(w in name_l for w in ['кросовк', 'обувь', 'ботинк', 'туфл']):
        return 'cat_clo_shoes'
    if any(w in name_l for w in ['плать', 'рубашк', 'футболк', 'джинс', 'бель', 'трус',
                                  'костюм', 'лиф']):
        return 'cat_clo_everyday'
    if any(w in name_l for w in ['книг', 'питер пэн', 'гарри поттер', 'бытие', 'тошнота',
                                  'хоттабыч', 'успенск', 'сартр', 'повесть', 'сказка',
                                  'нильса', 'крокодил', 'королевств']):
        return 'cat_culture_books'
    if any(w in name_l for w in ['мяч', 'коврик', 'валик', 'массажн', 'йог', 'пилатес',
                                  'диск', 'блок для йоги', 'хоккей', 'теннис', 'тюбинг']):
        return 'cat_sport'
    if any(w in name_l for w in ['продукт', 'еда', 'вода', 'напит', 'зефир', 'торт',
                                  'шоколад', 'конфет', 'коркунов']):
        return 'cat_food'
    if any(w in name_l for w in ['пакет', 'туалетн', 'бумаг', 'губк', 'контейн',
                                  'держател']):
        return 'cat_home'
    if any(w in name_l for w in ['предохрани', 'автомоби']):
        return 'cat_auto'
    if any(w in name_l for w in ['брелок', 'брош', 'сумк', 'перчат', 'кольц', 'очк',
                                  'часы']):
        return 'cat_clo_access'
    if any(w in name_l for w in ['турка', 'кофевар', 'контейнер', 'бумаг', 'пакет', 'держател']):
        return 'cat_home_kitchen'
    if any(w in name_l for w in ['вибратор', 'эрот', 'страпон', 'секс', 'кольцо на член']):
        return 'cat_sexual'
    return 'cat_other'


def main():
    if len(sys.argv) < 2:
        print('Использование: python3 ocr_recognize.py <путь_к_изображению>')
        sys.exit(1)
    
    img_path = sys.argv[1]
    
    if not os.path.exists(img_path):
        print(f'Файл не найден: {img_path}')
        sys.exit(1)
    
    print(f'Распознаю: {img_path}')
    
    raw_text, prices, products = ocr_image(img_path)
    
    print(f'\n=== Сырой текст ===')
    print(raw_text[:500])
    
    if prices:
        print(f'\n=== Найденные цены ===')
        for p in prices:
            print(f'  {p} ₽')
    
    if products:
        print(f'\n=== Распознанные товары ({len(products)}) ===')
        for p in products:
            cat = guess_category(p)
            print(f'  [{cat}] {p}')
        
        # Сохраняем в БД
        saved = save_to_db(img_path, raw_text, products)
        print(f'\nСохранено в БД: {saved} записей')
    else:
        print('\nТовары не найдены')


if __name__ == '__main__':
    main()
