#!/usr/bin/env python3
"""
confirm_matches.py — ручной review спорных кейсов матчинга.

Показывает:
- recognized.product_name
- топ-3 кандидата из items с score
- выбор стрелками (Up/Down), Enter — подтвердить, 's' — пропустить, 'q' — выход

Сохраняет matched_item_id и обновляет notes.

Использование:
  python3 confirm_matches.py                          # все записи без matched_item_id
  python3 confirm_matches.py --source-type screen     # только screen
  python3 confirm_matches.py --limit 10               # первые 10
  python3 confirm_matches.py --candidate-id 123       # ручное назначение для записи
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "consumption.db"

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None


def get_records(db, source_type=None, limit=None, candidate_id=None):
    """Выбрать записи без matched_item_id."""
    where_parts = ["r.matched_item_id IS NULL"]
    params = []

    if source_type:
        where_parts.append("r.source_type = ?")
        params.append(source_type)

    if candidate_id:
        where_parts.append("r.id = ?")
        params.append(candidate_id)

    where = " AND ".join(where_parts)

    query = f"""
        SELECT r.id, r.source_type, r.recognized_product, r.confidence,
               r.notes, r.recognized_product AS search_name
        FROM recognized_items_log r
        WHERE {where}
        ORDER BY r.id
    """

    if limit:
        query += f" LIMIT {limit}"

    return [dict(r) for r in db.execute(query, params).fetchall()]


def get_top_candidates(db, record, items=None, threshold=80):
    """Найти топ-5 кандидатов из items через fuzzy match."""
    name = record["search_name"] or record["recognized_product"]
    if not name:
        return []

    if items is None:
        cur = db.execute(
            "SELECT id, name, COALESCE(brand,'') AS brand, COALESCE(sku,'') AS sku "
            "FROM items WHERE deleted_at IS NULL"
        )
        items = [dict(r) for r in cur.fetchall()]

    if not fuzz:
        # Without rapidfuzz, do basic substring matching
        results = []
        norm = name.lower()
        for item in items:
            inorm = item["name"].lower()
            if norm in inorm or inorm in norm:
                score = 70 + min(len(norm), len(inorm)) / max(len(norm), len(inorm), 1) * 30
                results.append((item, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:5]

    results = []
    norm = name.lower()
    for item in items:
        score = fuzz.token_set_ratio(norm, item["name"].lower())
        if score >= threshold:
            results.append((item, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:5]


def manual_approve(records, db):
    """Интерактивный режим подтверждения матчей."""
    if not records:
        print("Нет записей для проверки.")
        return

    # Load all items once
    cur = db.execute(
        "SELECT id, name, COALESCE(brand,'') AS brand, COALESCE(sku,'') AS sku "
        "FROM items WHERE deleted_at IS NULL"
    )
    all_items = [dict(r) for r in cur.fetchall()]
    print(f"Загружено {len(all_items)} товаров в базу кандидатов.\n")

    matched = 0
    skipped = 0

    for i, rec in enumerate(records):
        candidates = get_top_candidates(db, rec, all_items)

        print(f"\n{'='*60}")
        print(f"[{i+1}/{len(records)}] ID={rec['id']} | {rec['source_type']} | conf={rec['confidence']}")
        print(f"  Распознано: {rec['recognized_product']}")
        print(f"  Текущие notes: {rec['notes'] or '(пусто)'}")
        print()

        if not candidates:
            print("  ❌ Нет кандидатов (score < 80)")
            print("  [s]kip | [q]uit")
        else:
            print(f"  Кандидаты (топ-{len(candidates)}):")
            for j, (item, score) in enumerate(candidates, 1):
                brand = f" [{item['brand']}]" if item.get("brand") else ""
                print(f"    {j}. [{score:.0f}] {item['name']}{brand}")
            print()
            print("  [1-5] выбрать кандидата | [s]kip | [q]uit")

        # Simple non-interactive mode — show and ask
        choice = input("  > ").strip().lower()

        if choice == "q":
            print(f"\nВыход. Обработано: {i}, подтверждено: {matched}, пропущено: {skipped}")
            break
        elif choice == "s":
            skipped += 1
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                old_notes = json.loads(rec["notes"]) if rec.get("notes") and rec["notes"] != "(пусто)" else {}
            except (json.JSONDecodeError, TypeError):
                old_notes = {}
            if isinstance(old_notes, str):
                old_notes = {}
            old_notes["manual_review_skipped"] = True
            old_notes["manual_review_at"] = now_iso
            db.execute(
                "UPDATE recognized_items_log SET notes = ? WHERE id = ?",
                (json.dumps(old_notes, ensure_ascii=False), rec["id"]),
            )
            db.commit()
            continue

        # Try to parse number
        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(candidates):
                item, score = candidates[choice_num - 1]
                matched += 1
                now_iso = datetime.now(timezone.utc).isoformat()
                notes = json.dumps({
                    "match_method": "manual",
                    "score": round(score),
                    "matched_at": now_iso,
                    "user_confirmed": True,
                }, ensure_ascii=False)
                db.execute(
                    "UPDATE recognized_items_log SET matched_item_id = ?, notes = ? WHERE id = ?",
                    (item["id"], notes, rec["id"]),
                )
                db.commit()
                print(f"  ✅ Matched → item #{item['id']}: {item['name']}")
            else:
                print(f"  ❌ Номер вне диапазона (1-{len(candidates)})")
        except ValueError:
            print("  ❌ Непонятный ввод. [1-5], [s], [q]")

    print(f"\nГотово. Подтверждено: {matched}, пропущено: {skipped}")


def batch_auto(db, threshold=90):
    """Автоматически сматчить записи с score >= threshold (для проверки вручную)."""
    cur = db.execute(
        "SELECT id, source_type, recognized_product, confidence, matched_item_id, notes "
        "FROM recognized_items_log "
        "WHERE matched_item_id IS NULL "
        "AND source_type != 'screen_ocr' "
        "ORDER BY id"
    )
    records = [dict(r) for r in cur.fetchall()]

    cur = db.execute(
        "SELECT id, name, COALESCE(brand,'') AS brand, COALESCE(sku,'') AS sku "
        "FROM items WHERE deleted_at IS NULL"
    )
    items = [dict(r) for r in cur.fetchall()]

    if not fuzz:
        print("Требуется rapidfuzz для batch-режима. Установи: pip install rapidfuzz")
        return

    matched = 0
    for rec in records:
        name = rec.get("recognized_product")
        if not name:
            continue
        best = None
        best_score = 0
        for item in items:
            score = fuzz.token_set_ratio(name.lower(), item["name"].lower())
            if score > best_score:
                best_score = score
                best = item

        if best and best_score >= threshold:
            now_iso = datetime.now(timezone.utc).isoformat()
            notes = json.dumps({
                "match_method": "auto_high",
                "score": best_score,
                "matched_at": now_iso,
            }, ensure_ascii=False)
            db.execute(
                "UPDATE recognized_items_log SET matched_item_id = ?, notes = ? WHERE id = ?",
                (best["id"], notes, rec["id"]),
            )
            matched += 1
            print(f"  AUTO: [{best_score:.0f}] {name[:50]} → item #{best['id']}")

    db.commit()
    print(f"\nАвтоматически сматчено: {matched}")


def main():
    parser = argparse.ArgumentParser(description="Consumer Agent — Confirm Matches")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--source-type", help="Фильтр по source_type (screen, pdf_cheque)")
    parser.add_argument("--limit", type=int, default=20, help="Количество записей")
    parser.add_argument("--auto", action="store_true",
                        help="Автоматический режим (матчит при score >= 90)")
    parser.add_argument("--candidate-id", type=int,
                        help="ID записи для ручного назначения")
    args = parser.parse_args()

    db = sqlite3.connect(str(args.db))
    db.row_factory = sqlite3.Row

    print("Consumer Agent — Confirm Matches")
    print(f"  Источник: {args.source_type or 'все (кроме screen_ocr)'}")
    print(f"  Лимит:    {args.limit if not args.candidate_id else '1'}")
    print(f"  Режим:    {'авто' if args.auto else 'интерактивный'}")
    print()

    records = get_records(db, source_type=args.source_type,
                          limit=1 if args.candidate_id else args.limit,
                          candidate_id=args.candidate_id)

    if args.auto:
        batch_auto(db, threshold=90)
    else:
        manual_approve(records, db)

    db.close()


if __name__ == "__main__":
    main()
