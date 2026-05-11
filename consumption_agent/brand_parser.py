"""
brand_parser.py — Извлечение бренда, названия и срока замены из текста.

Форматы:
  "нравится джемпер hemington"
  "пиджак circolo замена 24 мес"
  "пиджак Circolo 1901"
  "кроссовки Nike Air Max замена 12 мес"
  "носки | бренд Nike | замена 6 мес"
  "стремянка 5 ступеней"  (нет бренда)
  "турка электрическая Gorenje"

Алгоритм определения бренда:
1. Явный маркер: "бренд X" / "brand X" → X это бренд
2. Разделитель |: "название | бренд X | замена Y мес"
3. Латиница после кириллицы: "джемпер Hemington" → бренд=Hemington
4. Известный бренд из списка
5. CamelCase / заглавное слово после строчного названия
6. Числа в конце бренда допустимы: "Circolo 1901", "Nike Air Max 90"
"""

import re
from typing import Optional

# Расширенный список известных брендов (lowercase для поиска)
KNOWN_BRANDS = {
    # Fashion
    'nike', 'adidas', 'puma', 'reebok', 'new balance', 'asics',
    'gucci', 'prada', 'armani', 'boss', 'hugo', 'zara', 'hm', 'h&m',
    'massimo dutti', 'lacoste', 'tommy hilfiger', 'ralph lauren', 'polo',
    'burberry', 'dior', 'valentino', 'dolce', 'gabbana', 'louis vuitton',
    'brioni', 'canali', 'etro', 'zegna', 'corneliani', 'kiton',
    'brunello cucinelli', 'loro piana', 'bottega veneta', 'fendi',
    'versace', 'givenchy', 'balenciaga', 'saint laurent', 'celine',
    'loewe', 'moncler', 'stone island', 'cp company',
    'uniqlo', 'gap', 'levis', 'levi\'s', 'wrangler', 'diesel',
    'calvin klein', 'michael kors', 'coach', 'kate spade',
    'hemington', 'circolo', 'circolo 1901', 'hackett', 'gant',
    'fred perry', 'ben sherman', 'barbour', 'belstaff',
    'columbia', 'the north face', 'patagonia', 'arc\'teryx',
    'salomon', 'merrell', 'timberland', 'clarks', 'ecco',
    'dr martens', 'converse', 'vans', 'skechers', 'crocs',
    # Tech
    'apple', 'samsung', 'xiaomi', 'huawei', 'sony', 'lg', 'philips',
    'bosch', 'siemens', 'miele', 'dyson', 'electrolux', 'gorenje',
    'tefal', 'moulinex', 'braun', 'panasonic', 'jbl', 'bose',
    'dell', 'hp', 'lenovo', 'asus', 'acer', 'msi',
    'logitech', 'razer', 'steelseries', 'corsair',
    'kitchenaid', 'smeg', 'delonghi', 'nespresso', 'kenwood',
    # Home
    'ikea', 'hoff', 'askona',
    # Sports
    'decathlon', 'under armour', 'fila', 'umbro', 'kappa',
    'mizuno', 'yonex', 'head', 'wilson', 'babolat',
}

# Слова-триггеры, после которых идёт НЕ бренд (исключения)
NOT_BRAND_WORDS = {
    'замена', 'на', 'мес', 'месяц', 'месяца', 'месяцев', 'лет', 'год', 'года',
    'ступен', 'штук', 'шт', 'литр', 'кг', 'см', 'мм', 'метр',
    'белый', 'чёрный', 'черный', 'серый', 'синий', 'красный', 'зелёный',
    'большой', 'маленький', 'средний', 'новый', 'старый',
    'нравится', 'нравиться', 'круто', 'классно', 'хочу', 'запомни',
    'не', 'очень', 'для', 'или', 'из', 'по', 'электрическ', 'электро',
}

# Русские слова-предметы (часть речи: существительное)
ITEM_WORDS = {
    'джемпер', 'свитер', 'пиджак', 'куртка', 'пальто', 'рубашка',
    'футболка', 'поло', 'брюки', 'джинсы', 'шорты', 'юбка', 'платье',
    'кроссовки', 'ботинки', 'туфли', 'сапоги', 'кеды', 'мокасины',
    'носки', 'шарф', 'шапка', 'перчатки', 'ремень', 'галстук',
    'сумка', 'рюкзак', 'кошелек', 'портмоне', 'очки', 'часы',
    'стремянка', 'пылесос', 'утюг', 'фен', 'чайник', 'кофемолка',
    'турка', 'миксер', 'блендер', 'тостер', 'мясорубка',
    'кресло', 'диван', 'стол', 'стул', 'кровать', 'шкаф', 'комод',
    'телефон', 'ноутбук', 'планшет', 'наушники', 'колонка', 'монитор',
}

# Слова-реакции Memory Lane (убирать из начала)
REACTION_WORDS = {
    'нравится', 'нравиться', 'классно', 'круто', 'хочу', 'купить',
    'запомни', 'запомнить', 'сохрани', 'сохранить', 'like', 'dislike',
    'не нравится', 'ужас',
    # NOT 'фу' — обрезает 'футболка'
}


def _is_latin(word: str) -> bool:
    """Проверяет, содержит ли слово латинские буквы."""
    return bool(re.search(r'[a-zA-Z]', word))


def _is_cyrillic(word: str) -> bool:
    """Проверяет, содержит ли слово кириллические буквы."""
    return bool(re.search(r'[а-яА-ЯёЁ]', word))


def _is_number_or_size(word: str) -> bool:
    """Проверяет, является ли слово числом или размером."""
    return bool(re.match(r'^\d+[.,]?\d*$', word) or re.match(r'^\d+[xх×]\d+$', word))


def _looks_like_brand_word(word: str) -> bool:
    """Проверяет, похоже ли слово на бренд."""
    wl = word.lower().rstrip('.,;:!')
    # Исключаем служебные слова
    for nw in NOT_BRAND_WORDS:
        if wl.startswith(nw):
            return False
    # Число
    if _is_number_or_size(word):
        return False
    # Латинское слово ≥ 2 букв
    if _is_latin(word) and len(word) >= 2:
        return True
    # Заглавная кириллица (но не обычное русское слово)
    if word[0].isupper() and _is_cyrillic(word) and wl not in ITEM_WORDS and wl not in NOT_BRAND_WORDS:
        return True
    return False


def parse_brand_and_name(text: str) -> dict:
    """
    Извлекает из текста: name, brand, replace_months.
    
    Возвращает:
        {
            'name': 'джемпер',
            'brand': 'Hemington',
            'replace_months': None,
            'cleaned_text': 'джемпер Hemington'  # без реакций и сроков
        }
    """
    result = {'name': None, 'brand': None, 'replace_months': None, 'cleaned_text': text}
    
    if not text or not text.strip():
        return result
    
    working = text.strip()
    
    # 1. Убираем хэштеги (Memory Lane)
    working = re.sub(r'#[\w\-]+', '', working).strip()
    
    # 2. Убираем реакции из начала (с проверкой границы слова)
    wl = working.lower()
    for reaction in sorted(REACTION_WORDS, key=len, reverse=True):
        if wl.startswith(reaction):
            after = working[len(reaction):]
            # Проверяем границу слова: после реакции должен быть пробел/конец строки
            if not after or after[0] in ' \t\n,;:':
                working = after.strip()
                wl = working.lower()
                break
    
    # 3. Извлекаем срок замены
    duration_match = re.search(
        r'(?:замена|на|replace)\s+(\d+)\s*(мес(?:яц(?:а|ев)?)?|m(?:onths?)?|лет|год(?:а|ов)?|г|y(?:ears?)?)\b',
        working, re.IGNORECASE
    )
    if duration_match:
        val = int(duration_match.group(1))
        unit = duration_match.group(2).lower()
        if unit in ('лет', 'год', 'года', 'годов', 'г', 'y', 'year', 'years'):
            result['replace_months'] = val * 12
        else:
            result['replace_months'] = val
        working = working[:duration_match.start()].strip().rstrip(',;')
    
    # 4. Явный маркер "бренд X" / "brand X"
    brand_marker = re.search(r'(?:бренд|brand)\s+(.+?)(?:\s*[|,;]|$)', working, re.IGNORECASE)
    if brand_marker:
        result['brand'] = brand_marker.group(1).strip()
        working = working[:brand_marker.start()].strip().rstrip(',;|')
        result['name'] = working.strip() or None
        result['cleaned_text'] = f"{result['name'] or ''} {result['brand'] or ''}".strip()
        return result
    
    # 5. Разделитель | 
    if '|' in working:
        parts = [p.strip() for p in working.split('|')]
        result['name'] = parts[0]
        for p in parts[1:]:
            pl = p.lower()
            if pl.startswith('бренд') or pl.startswith('brand'):
                result['brand'] = p.split(None, 1)[1] if ' ' in p else p
            elif re.match(r'замена|replace', pl):
                m = re.search(r'(\d+)', p)
                if m:
                    result['replace_months'] = int(m.group(1))
        result['cleaned_text'] = f"{result['name'] or ''} {result['brand'] or ''}".strip()
        return result
    
    # 6. Умный парсинг без разделителей
    words = working.split()
    if not words:
        return result
    
    # Ищем точку перехода кириллица→латиница
    name_words = []
    brand_words = []
    brand_started = False
    
    for i, word in enumerate(words):
        if brand_started:
            # После начала бренда: латиница, числа, или заглавные слова — часть бренда
            # (например: IKEA Поэнг, Nike Air Max 90)
            if _is_latin(word) or _is_number_or_size(word):
                brand_words.append(word)
            elif word[0].isupper() and _is_cyrillic(word) and word.lower() not in ITEM_WORDS and word.lower() not in NOT_BRAND_WORDS:
                # Кириллица с заглавной буквой — может быть частью бренда (IKEA Поэнг)
                brand_words.append(word)
            else:
                # Обычное слово — не бренд
                name_words.extend(brand_words)
                brand_words = []
                brand_started = False
                name_words.append(word)
        elif _looks_like_brand_word(word) and name_words:
            # Потенциальное начало бренда (после хотя бы одного слова названия)
            brand_started = True
            brand_words.append(word)
        else:
            name_words.append(word)
    
    # Проверяем: если brand_words — это известный бренд, подтверждаем
    if brand_words:
        brand_candidate = ' '.join(brand_words)
        bc_lower = brand_candidate.lower()
        
        # Проверка: точное совпадение с известным брендом
        is_known = bc_lower in KNOWN_BRANDS
        # Или начало известного бренда: "circolo" в "circolo 1901"
        if not is_known:
            is_known = any(bc_lower.startswith(kb) or kb.startswith(bc_lower) for kb in KNOWN_BRANDS)
        # Или латиница (высокая вероятность бренда)
        is_latin_brand = _is_latin(brand_candidate)
        
        if is_known or is_latin_brand:
            result['brand'] = brand_candidate
        else:
            # Неуверены — добавляем обратно в название
            name_words.extend(brand_words)
    
    result['name'] = ' '.join(name_words).strip() or None
    result['cleaned_text'] = f"{result['name'] or ''} {result['brand'] or ''}".strip()
    
    return result


# ===== CLI тесты =====
if __name__ == '__main__':
    tests = [
        "нравится джемпер hemington",
        "пиджак circolo замена 24 мес",
        "пиджак Circolo 1901",
        "кроссовки Nike Air Max замена 12 мес",
        "стремянка 5 ступеней",
        "турка электрическая Gorenje",
        "носки Nike замена 6 мес",
        "джинсы Levi's 501",
        "куртка The North Face замена 36 мес",
        "свитер Brunello Cucinelli",
        "чайник Bosch",
        "классно рубашка Massimo Dutti",
        "хочу купить кроссовки Adidas Ultraboost",
        "#одежда нравится пальто Max Mara",
        "запомни кресло IKEA Поэнг",
        "пылесос Dyson V15",
        "нравится джемпер hemington замена 18 мес",
        "футболка",
        "рубашка белая",
        "ноутбук Lenovo ThinkPad X1 Carbon замена 48 мес",
    ]
    
    for t in tests:
        r = parse_brand_and_name(t)
        brand_str = r['brand'] or '—'
        repl_str = f"{r['replace_months']} мес" if r['replace_months'] else '—'
        print(f"  [{t}]")
        print(f"    → name={r['name']}, brand={brand_str}, replace={repl_str}")
        print()
