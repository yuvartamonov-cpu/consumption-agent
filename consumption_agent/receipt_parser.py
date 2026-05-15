#!/usr/bin/env python3
"""
receipt_parser.py — парсинг чеков Ozon (PDF-текст и HTML-уведомления).

Поддерживаемые форматы:
1. Фискальный чек Ozon (ozon_pdf) — текстовый файл с товарами, ценами, итогом
2. HTML-уведомление (ozon) — уведомление о чеке без цен

Структура фискального чека:
  Кассовый чек № NNNN
  DD.MM.YYYY HH:MM
  1. Название товара
     1 x XXX,XX                                                        ≡XXX,XX
  ИТОГ                                                                 ≡XXX,XX

Использование:
  python3 receipt_parser.py --file /path/to/cheque.txt
  python3 receipt_parser.py --cheque-id 22  # по id из cheques_log
  python3 receipt_parser.py --batch          # все непрочитанные
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

from consumption.db import connect as db_connect
from matcher import _build_normalized_index, match_record, normalize

DB_PATH = Path(__file__).parent / "consumption.db"


def parse_fiscal_text(text: str) -> dict | None:
    """Парсинг текстового фискального чека Ozon."""
    result = {}

    # Дата и номер чека: "Кассовый чек № 1371  30.11.2025 09:51"
    m = re.search(
        r'Кассовый чек\s+№\s*(\d+)\s+(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})',
        text
    )
    if m:
        result['cheque_number'] = int(m.group(1))
        result['date'] = f"{m.group(4)}-{m.group(3)}-{m.group(2)}"
        result['time'] = f"{m.group(5)}:{m.group(6)}"

    # Итоговая сумма
    m = re.search(r'ИТОГ\s*.*?(\d[\d\s]*\d)', text)
    if m:
        result['total'] = float(m.group(1).replace(' ', '').replace(',', '.'))

    # Позиции: "1. Название товара\n     1 x XXX,XX  ...  ≡XXX,XX"
    items = []
    # Паттерн: номер. название  количество x цена  ≡ сумма
    for match in re.finditer(
            r'(\d+)\.\s*(.+?)\s+(\d+)\s*x\s*(\d[\d\s,]*\d)\s*≡\s*(\d[\d\s,]*\d)',
            text
    ):
        items.append({
            'num': int(match.group(1)),
            'name': match.group(2).strip(),
            'qty': int(match.group(3)),
            'price': float(match.group(4).replace(' ', '').replace(',', '.')),
            'total': float(match.group(5).replace(' ', '').replace(',', '.')),
        })

    result['items'] = items
    result['item_count'] = len(items)

    if not items and 'total' not in result:
        return None  # не удалось распарсить

    return result


def process_file(filepath: str, cheque_log_id: int = None) -> dict | None:
    """Распарсить файл чека. Возвращает dict с распарсенными данными или None."""
    text = Path(filepath).read_text('utf-8', errors='replace')
    parsed = parse_fiscal_text(text)

    if not parsed:
        return None

    print(f"  Чек №{parsed.get('cheque_number', '?')} | {parsed.get('date', '?')} | "
          f"{parsed.get('total', 0):.0f} ₽ | {parsed.get('item_count', 0)} позиций")

    return parsed


def _get_items(db) -> list:
    """Получить список всех товаров для матчинга."""
    rows = db.execute(
        "SELECT id, name, COALESCE(brand,'') AS brand, COALESCE(sku,'') AS sku "
        "FROM items WHERE deleted_at IS NULL"
    ).fetchall()
    return [{'id': r[0], 'name': r[1], 'brand': r[2], 'sku': r[3]} for r in rows]


def _create_purchase_and_items(db, purchase_id: int, parsed: dict, all_items: list,
                                email_uid: str, subject: str) -> str:
    """Привязать товары из чека к items через matcher."""
    matched_count = 0
    unmatched_items = []
    norm_index = _build_normalized_index(all_items)
    norm_item_cache = {id(item): normalize(item["name"]) for item in all_items}

    for item in parsed.get('items', []):
        rec = {
            'recognized_product': item['name'],
            'confidence': 'high',
            'brand': '',
            'sku': '',
        }
        candidates = match_record(rec, norm_index, all_items, norm_item_cache, 85, 90)

        target_item_id = None
        if candidates and candidates[0]['score'] >= 85:
            target_item_id = candidates[0]['item']['id']
            matched_count += 1
        else:
            cur = db.execute("""
                INSERT INTO items (name, category_id, purchase_id, purchase_date,
                                   purchase_price, purchase_currency, quantity, data_origin, status)
                VALUES (?, (SELECT id FROM categories WHERE slug = 'other' LIMIT 1),
                        ?, ?, ?, 'RUB', ?, 'cheque_parse', 'in_use')
            """, (
                item['name'],
                purchase_id,
                parsed.get('date'),
                item['price'],
                item['qty'],
            ))
            target_item_id = cur.lastrowid
            unmatched_items.append(item['name'])

        if target_item_id:
            db.execute("""
                UPDATE items SET purchase_id = ?, purchase_price = ?,
                                 purchase_date = ?, purchase_currency = 'RUB',
                                 quantity = ?
                WHERE id = ?
            """, (purchase_id, item['price'], parsed.get('date'), item['qty'], target_item_id))

    result = f"purchase_id={purchase_id}"
    if matched_count > 0:
        result += f", matched={matched_count}"
    if unmatched_items:
        result += f", new_items={len(unmatched_items)}: {unmatched_items[:3]}"
    return result


def batch_process():
    """Обработать все чеки из cheques_log с source='ozon_pdf', у которых нет purchase."""
    db = db_connect(DB_PATH)

    cur = db.execute("""
        SELECT cl.id, cl.email_uid, cl.receipt_url, cl.subject
        FROM cheques_log cl
        LEFT JOIN purchases p ON cl.email_uid = p.email_message_id AND p.deleted_at IS NULL
        WHERE cl.source = 'ozon_pdf'
          AND cl.receipt_url IS NOT NULL
          AND p.id IS NULL
        ORDER BY cl.id
    """)
    unprocessed = cur.fetchall()

    print(f"Нераспарсенных чеков: {len(unprocessed)}")

    all_items = _get_items(db)

    for cl_id, email_uid, receipt_url, subject in unprocessed:
        filepath = receipt_url
        if not Path(filepath).exists():
            print(f"  ⚠ Файл не найден: {filepath}")
            continue

        try:
            text = Path(filepath).read_text('utf-8', errors='replace')
            if 'Кассовый чек' not in text:
                print(f"  ⚠ Не похоже на чек: {filepath}")
                continue
        except Exception as e:
            print(f"  ⚠ Ошибка чтения {filepath}: {e}")
            continue

        parsed = parse_fiscal_text(text)
        if not parsed:
            print(f"  ⚠ Не удалось распарсить: {filepath}")
            continue

        print(f"  Чек №{parsed.get('cheque_number', '?')} | {parsed.get('date', '?')} | "
              f"{parsed.get('total', 0):.0f} ₽ | {parsed.get('item_count', 0)} позиций")

        # Проверяем, нет ли уже purchase
        existing = db.execute(
            "SELECT id FROM purchases WHERE email_message_id = ? AND deleted_at IS NULL",
            (email_uid,)
        ).fetchone()
        if existing:
            print(f"  ⏭ Уже есть: purchase_id={existing[0]}")
            continue

        # Создаём purchase (INSERT OR IGNORE для защиты от гонки)
        try:
            cur = db.execute("""
                INSERT OR IGNORE INTO purchases (purchase_date, total_amount, source, store_name,
                                       order_number, receipt_url, email_message_id, notes, data_origin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ozon_pdf_cheque')
            """, (
                parsed.get('date'),
                parsed.get('total'),
                'ozon_pdf',
                'Ozon',
                str(parsed.get('cheque_number', '')),
                '',
                email_uid,
                f"Чек №{parsed.get('cheque_number', '')}" if parsed.get('cheque_number') else subject,
            ))
            purchase_id = cur.lastrowid
        except sqlite3.IntegrityError:
            # Race — purchase уже создан в параллельной сессии
            purchase_id = db.execute(
                "SELECT id FROM purchases WHERE email_message_id = ?", (email_uid,)
            ).fetchone()
            if purchase_id:
                purchase_id = purchase_id[0]
            else:
                print(f"  ⚠ Cannot get purchase_id, skipping")
                continue

        # Привязываем товары
        result = _create_purchase_and_items(db, purchase_id, parsed, all_items, email_uid, subject)
        db.commit()
        print(f"  ✅ {result}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Consumer Agent — Receipt Parser")
    parser.add_argument("--file", help="Путь к файлу чека")
    parser.add_argument("--cheque-id", type=int, help="ID записи в cheques_log")
    parser.add_argument("--batch", action="store_true", help="Обработать все нераспарсенные чеки")
    parser.add_argument("--dry-run", action="store_true", help="Только парсинг, без записи в БД")
    args = parser.parse_args()

    if args.batch:
        batch_process()
    elif args.file:
        print(f"\nПарсинг: {args.file}")
        parsed = process_file(args.file, cheque_log_id=args.cheque_id)
        if not parsed:
            print("  ❌ Не удалось распарсить")
        elif args.dry_run:
            import json
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
