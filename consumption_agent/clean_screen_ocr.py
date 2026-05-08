#!/usr/bin/env python3
"""Clean noisy screen_ocr items and backfill missing OCR categories.

Default: dry-run.
Use --apply to soft-delete noisy items and insert missing categories.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'consumption.db'

MISSING_CATEGORIES = [
    ('cat_home_kitchen', 'cat_home', 'Кухня и хранение', 'home_kitchen', 3),
    ('cat_sport', 'cat_sports', 'Спортивные товары', 'sport_goods', 1),
    ('cat_culture_books', 'cat_hobbies', 'Книги и культура', 'books', 1),
    ('cat_sexual', 'cat_hobbies', 'Интимные товары', 'sexual', 2),
    ('cat_other', 'cat_hobbies', 'Прочее', 'other', 99),
]

NOISE_PATTERNS = [
    r'сообщени[ея]\s+продав', r'перейти\s+в\s+ча[тч]', r'выберите\s+товар',
    r'экспресс\s+достав', r'просмотреть\s+чек', r'разрешить\s+торг',
    r'\bип\b', r'\bооо\b', r'издательств', r'\bstore\b', r'\bgroup\b', r'\bshop\b',
    r'январ', r'феврал', r'март', r'апрел', r'мая', r'июн', r'июл', r'август',
    r'сентябр', r'октябр', r'ноябр', r'декабр',
    r'http', r'www\.', r'@', r'\.ru\b', r'\.su\b', r'openclaw', r'laptop-',
    r'python3', r'curl', r'netsh', r'adb\b', r'syntaxerror', r'windows\\system32',
    r'fon\.bet', r'stoloto', r'nic\.ru', r'домен', r'ломен', r'эскроу', r'btc', r'usdt',
    r'цена\s+по\s+запросу', r'регистратор', r'блокиров', r'претенз', r'бизнес',
    r'потоки\s+данных', r'диагностик', r'мир\s+электроники',
    r'инн', r'кассов', r'налог', r'без\s*ндс', r'фн\b', r'фд\b', r'фпд\b',
    r'chrome\s+браузером', r'dashboard', r'health\b', r'approval', r'requestid'
]

PRODUCT_KEYWORDS = [
    'корм', 'собак', 'кошк', 'симпар', 'грандорф', 'мяч', 'валик', 'коврик', 'йог',
    'пилатес', 'диск', 'блок', 'контейнер', 'столик', 'комод', 'шкаф', 'костюм',
    'плать', 'бель', 'балаклав', 'картридж', 'книга', 'тетрад', 'хоттабыч', 'успенск',
    'сартр', 'лодка', 'камертон', 'клей', 'батарей', 'тюбинг', 'вибратор', 'страпон',
    'гирлянд', 'кубик', 'респиратор', 'грунтован', 'холст',
    'эрот', 'кольцо', 'турка', 'кофевар', 'бумаг', 'пакет', 'держател', 'средство',
    'стабилиз', 'елк', 'ёлк', 'подписк', 'premium', 'hp', 'принтер', 'стол', 'кровать',
    'крокодил', 'королевство', 'нильса', 'путешествие', 'подводная лодка', 'рабочая тетрадь',
]


def normalize_text(text: str) -> str:
    text = text.replace('ё', 'е').replace('Ё', 'Е')
    text = re.sub(r'[|`~<>_=]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(" .,:;!?-–—_()[]{}\"'“”«»")


def has_product_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in PRODUCT_KEYWORDS)


def guess_category(name: str) -> str:
    lowered = name.lower()
    if any(w in lowered for w in ['корм', 'симпар', 'грандорф', 'собак', 'кошк']):
        return 'cat_pets'
    if any(w in lowered for w in ['вибратор', 'эрот', 'страпон', 'кольцо на член']):
        return 'cat_sexual'
    if any(w in lowered for w in ['книга', 'тетрад', 'хоттабыч', 'успенск', 'сартр', 'нильса', 'крокодил', 'королевств']):
        return 'cat_culture_books'
    if any(w in lowered for w in ['мяч', 'валик', 'коврик', 'йог', 'пилатес', 'теннис', 'хоккей', 'тюбинг', 'лодка']):
        return 'cat_sport'
    if any(w in lowered for w in ['столик', 'комод', 'шкаф', 'кровать']):
        return 'cat_home_furn'
    if any(w in lowered for w in ['контейнер', 'бумаг', 'пакет', 'держател', 'турка']):
        return 'cat_home_kitchen'
    if any(w in lowered for w in ['костюм', 'плать', 'бель', 'балаклав']):
        return 'cat_clo_everyday'
    if any(w in lowered for w in ['картридж', 'hp', 'принтер']):
        return 'cat_tech'
    return 'cat_other'


def is_noise_item(name: str) -> bool:
    text = normalize_text(name)
    lowered = text.lower()
    if len(text) < 6:
        return True
    if sum(ch.isalpha() for ch in text) < 4:
        return True
    if any(re.search(pattern, lowered, re.I) for pattern in NOISE_PATTERNS):
        return True
    if has_product_keyword(text):
        return False
    words = [w for w in re.split(r'\s+', text) if len(w) > 1]
    if len(words) < 2:
        return True
    if sum(ch.isdigit() for ch in text) >= 6:
        return True
    if sum(ch in '@#$%^*<>' for ch in text) >= 1:
        return True
    latin_words = [word for word in words if re.fullmatch(r'[A-Za-z][A-Za-z\-]+', word)]
    if len(words) <= 2 and len(latin_words) == len(words):
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if letters:
        uppercase_ratio = sum(ch.isupper() for ch in letters) / len(letters)
        if uppercase_ratio > 0.75 and not has_product_keyword(text):
            return True
    return False


def ensure_categories(conn: sqlite3.Connection) -> int:
    inserted = 0
    for row in MISSING_CATEGORIES:
        exists = conn.execute('SELECT 1 FROM categories WHERE id = ?', (row[0],)).fetchone()
        if exists:
            continue
        conn.execute(
            'INSERT INTO categories (id, parent_id, name, slug, sort_order) VALUES (?, ?, ?, ?, ?)',
            row,
        )
        inserted += 1
    return inserted


def collect_noise(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT id, name, category_id FROM items WHERE data_origin = 'screen_ocr' AND deleted_at IS NULL ORDER BY id"
    ).fetchall()
    noisy = []
    for row in rows:
        if is_noise_item(row[1]):
            noisy.append(row)
    return rows, noisy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--show', type=int, default=30)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        total_rows, noisy = collect_noise(conn)
        print(f'screen_ocr active items: {len(total_rows)}')
        print(f'noise candidates: {len(noisy)}')
        for row in noisy[:args.show]:
            print(f'  #{row[0]} [{row[2]}] {row[1]}')

        inserted_categories = ensure_categories(conn)
        print(f'missing categories inserted: {inserted_categories}')

        if not args.apply:
            conn.rollback()
            print('dry-run only; no DB changes applied')
            return

        if noisy:
            ids = [row[0] for row in noisy]
            conn.executemany(
                "UPDATE items SET deleted_at = datetime('now'), notes = COALESCE(notes, '') || CASE WHEN notes IS NULL OR notes = '' THEN '' ELSE '\n' END || 'auto-cleaned noisy screen_ocr' WHERE id = ?",
                [(item_id,) for item_id in ids],
            )

        recategorized = 0
        active_rows = conn.execute(
            "SELECT id, name, category_id FROM items WHERE data_origin='screen_ocr' AND deleted_at IS NULL"
        ).fetchall()
        for item_id, name, category_id in active_rows:
            new_category = guess_category(normalize_text(name))
            if new_category != category_id and new_category != 'cat_other':
                conn.execute("UPDATE items SET category_id = ? WHERE id = ?", (new_category, item_id))
                recategorized += 1

        conn.commit()

        remaining = conn.execute(
            "SELECT COUNT(*) FROM items WHERE data_origin = 'screen_ocr' AND deleted_at IS NULL"
        ).fetchone()[0]
        print(f'cleaned items: {len(noisy)}')
        print(f'recategorized items: {recategorized}')
        print(f'remaining active screen_ocr items: {remaining}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
