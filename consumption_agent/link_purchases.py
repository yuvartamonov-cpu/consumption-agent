#!/usr/bin/env python3
"""
link_purchases.py — связывание recognized_items_log и items с purchases.

Задачи:
1. Для items без purchase_id — найти подходящий purchase по дате/чеку
2. Для recognized_items_log с matched_item_id — проставить purchase_id (если у item есть)
3. Обновить items.purchase_id для тех, что созданы через recognized

Использование:
  python3 link_purchases.py
  python3 link_purchases.py --dry-run
  python3 link_purchases.py --fix-missing
"""
import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "consumption.db"


def get_purchases(db):
    """Получить список purchases с датами и email_message_id."""
    rows = db.execute("""
        SELECT id, purchase_date, total_amount, email_message_id, order_number
        FROM purchases
        WHERE deleted_at IS NULL
        ORDER BY purchase_date
    """).fetchall()
    return [dict(r) for r in rows]


def get_items_without_purchase(db):
    """Items без purchase_id (не screen_ocr)."""
    rows = db.execute("""
        SELECT id, name, purchase_date, purchase_price, data_origin, created_at
        FROM items
        WHERE purchase_id IS NULL
          AND deleted_at IS NULL
          AND data_origin NOT IN ('screen_ocr')
        ORDER BY id
    """).fetchall()
    return [dict(r) for r in rows]


def _match_purchase(items_wo_purchase, purchases):
    """Для item без purchase_id найти подходящий purchase.
    
    Стратегия матчинга:
    1. Если у item есть purchase_date — ищем purchase с той же датой (за вычетом временной зоны)
    2. Если item был создан в тот же день что и purchase — возможно, это один чек
    3. Если ничего — пропускаем
    """
    results = []  # (item_id, purchase_id, метод)
    
    for item in items_wo_purchase:
        item_date = item.get('purchase_date', '')
        created = item.get('created_at', '')

        # Если дата указана — ищем purchase с такой же датой
        if item_date:
            # purchase_date может быть "2025-11-30" (ISO) или "Thu, 30 Nov..."
            for p in purchases:
                p_date = p['purchase_date'] or ''
                # Сравниваем 10 символов (YYYY-MM-DD)
                if item_date[:10] == p_date[:10]:
                    results.append((item['id'], p['id'], f"date_match:{item_date[:10]}"))
                    break
            else:
                # Не нашли по точной дате — пробуем нестрогий match
                for p in purchases:
                    p_date = p['purchase_date'] or ''
                    if p_date and item_date[:7] == p_date[:7]:  # YYYY-MM
                        results.append((item['id'], p['id'], f"month_match:{item_date[:7]}"))
                        break

        # Если даты нет — пробуем по времени создания
        if not item_date and created:
            c_date = created[:10]  # created_at format: "2026-04-29 ..."
            for p in purchases:
                p_date = p['purchase_date'] or ''
                if c_date == p_date[:10]:
                    results.append((item['id'], p['id'], f"created_match:{c_date}"))
                    break

    return results


def fix_missing_matches(db):
    """Проставить purchase_id для items, которые были привязаны к purchases через recognized_items_log."""
    # Items, у которых purchase_id = NULL, но они были созданы через распознавание
    # и у recognized_items_log для этих товаров есть purchase_id
    db.execute("""
        UPDATE items
        SET purchase_id = (
            SELECT p.id FROM purchases p
            WHERE p.email_message_id = (
                SELECT r.notes FROM recognized_items_log r
                WHERE r.matched_item_id = items.id
                  AND r.notes LIKE '%purchase_id=%'
                LIMIT 1
            )
            LIMIT 1
        )
        WHERE purchase_id IS NULL
          AND EXISTS (
            SELECT 1 FROM recognized_items_log r
            WHERE r.matched_item_id = items.id
              AND r.notes LIKE '%purchase_id=%'
          )
    """)
    affected = db.total_changes
    # total_changes — не надёжен, посчитаем сами
    cur = db.execute("SELECT changes()")
    return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description="Consumer Agent — Link Purchases")
    parser.add_argument("--dry-run", action="store_true", help="Не записывать изменения")
    parser.add_argument("--fix-missing", action="store_true",
                        help="Проставить purchase_id для items, привязанных через recognized")
    args = parser.parse_args()

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")

    print("=== Consumer Agent — Link Purchases ===\n")

    # Шаг 1: фикс отсутствующих связей
    if args.fix_missing:
        count = fix_missing_matches(db)
        print(f"  Проставлено purchase_id для {count} items (через recognized_items_log)")
        db.commit()

    # Шаг 2: матчинг items без purchase_id
    purchases = get_purchases(db)
    items_wo = get_items_without_purchase(db)

    print(f"  Purchases: {len(purchases)}")
    print(f"  Items без purchase_id (кроме screen_ocr): {len(items_wo)}")

    if not items_wo:
        print("\n✅ Все некор-мусорные items уже привязаны к purchases.")
        db.close()
        return

    matches = _match_purchase(items_wo, purchases)

    if args.dry_run:
        print(f"\n  Будет обновлено: {len(matches)} items")
        for item_id, p_id, method in matches[:20]:
            print(f"    item={item_id} → purchase={p_id} ({method})")
        if len(matches) > 20:
            print(f"    ... и ещё {len(matches) - 20}")
    else:
        updated = 0
        for item_id, p_id, method in matches:
            db.execute(
                "UPDATE items SET purchase_id = ?, notes = COALESCE(notes || '; ', '') || ? "
                "WHERE id = ? AND purchase_id IS NULL",
                (p_id, f"auto_linked:{method}", item_id)
            )
            if db.total_changes > 0:
                updated += 1

        db.commit()
        print(f"\n  Обновлено: {updated} items")
        for item_id, p_id, method in matches[:10]:
            print(f"    item={item_id} → purchase={p_id} ({method})")
        if len(matches) > 10:
            print(f"    ... и ещё {len(matches) - 10}")

    # Шаг 3: статистика
    total = db.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]
    with_p = db.execute("SELECT COUNT(*) FROM items WHERE purchase_id IS NOT NULL AND deleted_at IS NULL").fetchone()[0]
    sans_p = total - with_p
    sans_p_no_muss = db.execute(
        "SELECT COUNT(*) FROM items WHERE purchase_id IS NULL AND deleted_at IS NULL AND data_origin != 'screen_ocr'"
    ).fetchone()[0]

    print(f"\n=== Итог ===")
    print(f"  Всего items: {total}")
    print(f"  С purchase_id: {with_p}")
    print(f"  Без purchase_id (всего): {sans_p}")
    print(f"  Без purchase_id (кроме screen_ocr): {sans_p_no_muss}")

    db.close()


if __name__ == "__main__":
    main()
