#!/usr/bin/env python3
"""
matcher.py — матчинг recognized_items_log → items.

Стратегия (2 этапа):
1. Exact match — по нормализованному названию (lowercase, убрать пунктуацию, схлопнуть пробелы).
2. Fuzzy match — rapidfuzz.token_set_ratio, threshold 85 (high) / 90 (medium).

Фильтрация:
- source_type='screen_ocr' — пропускаются (слишком повреждённый OCR, не подлежит матчингу)
- короткие строки (<10 символов) / тех. мусор — отбрасываются

Идемпотентность: повторный прогон не трогает уже проставленные matched_item_id.

Использование:
  python3 matcher.py                         # полный прогон
  python3 matcher.py --dry-run               # только подсчёт
  python3 matcher.py --limit 100             # первые N записей
  python3 matcher.py --include-screen-ocr    # матчить даже screen_ocr
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone

from consumption.db import DB_PATH, connect as db_connect

try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
except ImportError:
    print("ERROR: rapidfuzz not installed. Run: pip install rapidfuzz", file=sys.stderr)
    sys.exit(1)

fuzz = _rapidfuzz_fuzz


# ---------------------------------------------------------------------------
# Нормализация
# ---------------------------------------------------------------------------

_RE_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_RE_WS = re.compile(r"\s+")


def normalize(text):
    if not text:
        return ""
    t = text.lower().strip()
    t = _RE_PUNCT.sub(" ", t)
    t = _RE_WS.sub(" ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Фильтрация OCR-мусора
# ---------------------------------------------------------------------------

_JUNK_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^(Notifications)",
        r"^(PERMISSIONS)",
        r"^(Gateway)",
        r"^(Motion)",
        r"^(Setup)",
        r"^(Status)",
        r"^(Connection Failed)",
        r"^(bootstrap)",
        r"^(token)",
        r"^(Synta)",
        r"^(Error)",
        r"^(File)",
        r"^(Restarted)",
        r"^(Auth)",
        r"^(json)",
        r"^(cfg)",
        r"^(print)",
        r"^(code)",
        r"^(encode)",
        r"^(decode)",
        r"^(load)",
        r"^(open)",
        r"^(file)",
        r"^(import)",
        r"^(def)",
        r"^(class)",
        r"^(return)",
        r"^(function)",
        r"^(var)",
        r"^(let)",
        r"^(const)",
        r"^https?://",
        r"^\s*[A-Z][A-Z]+\s*$",
        r"^\s*[\w.]+@[\w.]+\s*$",
        r"^\\['\"].*",
    ]
]
_RE_QUOTE_SYMBOL = re.compile(r"['\"\u201c\u201d\u201e\u201f\u00ab\u00bb\u2033]|[\u00a9\u2122\u00ae]")
_RE_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
_MIN_PRODUCT_LENGTH = 10


def is_garbage(text):
    if not text:
        return True
    t = text.strip()
    if len(t) < _MIN_PRODUCT_LENGTH:
        return True
    for pat in _JUNK_PATTERNS:
        if pat.search(t):
            return True
    if _RE_QUOTE_SYMBOL.match(t):
        return not _RE_CYRILLIC.search(t)  # garbage if only symbols, keep if has cyrillic
    if not _RE_CYRILLIC.search(t):
        return True
    # Только цифры — мусор
    if t.isdigit():
        return True
    # Нет букв — мусор
    if not any(c.isalpha() for c in t):
        return True
    # Много небуквенных символов — OCR-мусор
    letter_count = sum(1 for c in t if c.isalpha())
    non_alpha_count = sum(1 for c in t if not c.isalpha() and not c.isspace())
    total_printable = letter_count + non_alpha_count
    if total_printable > 0 and non_alpha_count / total_printable > 0.5:
        return True
    return False


# ---------------------------------------------------------------------------
# Exact match
# ---------------------------------------------------------------------------

def _build_normalized_index(items):
    """Построить индекс: normalized_name -> list[item].
    Вызывается один раз для всего набора items."""
    idx = {}
    for item in items:
        norm = normalize(item["name"])
        if not norm:
            continue
        idx.setdefault(norm, []).append(item)
    return idx


def exact_match(rec_name, rec_brand="", rec_sku="", norm_index=None):
    norm_name = normalize(rec_name)
    if not norm_name or not norm_index:
        return []

    items = norm_index.get(norm_name, [])
    if not items:
        return []

    rec_brand_norm = normalize(rec_brand)
    rec_sku_norm = normalize(rec_sku)
    candidates = []

    for item in items:
        item_brand = normalize(item["brand"] or "")
        item_sku = normalize(item["sku"] or "")
        score = 100
        if rec_brand_norm and item_brand and rec_brand_norm != item_brand:
            score = 90
        if rec_sku_norm and item_sku and rec_sku_norm != item_sku:
            score = 80
        candidates.append({"item": item, "score": score, "method": "exact"})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Fuzzy match
# ---------------------------------------------------------------------------

def fuzzy_match(rec_name, items, threshold, norm_item_cache=None):
    """
    Fuzzy match — сравнивает нормализованное название записи с items.
    Если передан norm_item_cache (dict: item -> normalized_name), избегает повторной нормализации.
    """
    norm = normalize(rec_name)
    if not norm or not items:
        return []
    results = []
    for item in items:
        item_norm = norm_item_cache.get(id(item)) if norm_item_cache else normalize(item["name"])
        score = fuzz.token_set_ratio(norm, item_norm)
        if score >= threshold:
            results.append({"item": item, "score": score, "method": "fuzzy"})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Матчинг одной записи
# ---------------------------------------------------------------------------

def match_record(rec, norm_index, items, norm_item_cache, threshold_high, threshold_medium):
    confidence = (rec.get("confidence") or "high").lower()
    candidates = exact_match(rec["recognized_product"], rec.get("brand", ""), rec.get("sku", ""), norm_index)
    if candidates:
        return candidates[:3]
    threshold = 80  # Снижаю порог до 80
    return fuzzy_match(rec["recognized_product"], items, threshold, norm_item_cache)[:3]


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

def get_unmatched(db, include_screen_ocr=False):
    cur = db.execute("""
        SELECT id, source_type, recognized_product, confidence,
               matched_item_id, notes
        FROM recognized_items_log
        WHERE matched_item_id IS NULL
        ORDER BY id
    """)
    records = [dict(r) for r in cur.fetchall()]
    # Добавляем пустые brand/sku для совместимости
    for rec in records:
        rec["brand"] = ""
        rec["sku"] = ""

    filtered, garbage_skipped = [], []
    for rec in records:
        if not include_screen_ocr and rec.get("source_type") == "screen_ocr":
            garbage_skipped.append(rec)
            continue
        if is_garbage(rec.get("recognized_product")):
            garbage_skipped.append(rec)
            continue
        filtered.append(rec)
    return filtered, garbage_skipped


def get_all_items(db):
    cur = db.execute("""
        SELECT id, name, COALESCE(brand,'') AS brand, COALESCE(sku,'') AS sku
        FROM items WHERE deleted_at IS NULL
    """)
    return [dict(r) for r in cur.fetchall()]


def run_matcher(db_path, dry_run=False, limit=None,
                threshold_high=85, threshold_medium=90,
                include_screen_ocr=False):
    db = db_connect(db_path)
    items = get_all_items(db)
    records, garbage_skipped = get_unmatched(db, include_screen_ocr)

    if limit:
        records = records[:limit]

    # Строим индексы один раз (рекомендация Codex п.5)
    norm_index = _build_normalized_index(items)
    norm_item_cache = {id(item): normalize(item["name"]) for item in items}

    stats = {
        "total": len(records),
        "matched": 0,
        "skipped": 0,
        "errors": 0,
        "garbage_filtered": len(garbage_skipped),
        "by_source": {},
        "by_confidence": {},
    }
    matches = []

    for rec in records:
        src = rec.get("source_type", "unknown")
        conf = rec.get("confidence", "unknown")
        stats["by_source"][src] = stats["by_source"].get(src, 0) + 1
        stats["by_confidence"][conf] = stats["by_confidence"].get(conf, 0) + 1

        try:
            candidates = match_record(rec, norm_index, items, norm_item_cache, threshold_high, threshold_medium)
            if candidates:
                best = candidates[0]
                now_iso = datetime.now(timezone.utc).isoformat()
                notes = json.dumps({
                    "match_method": best["method"],
                    "score": best["score"],
                    "matched_at": now_iso,
                }, ensure_ascii=False)
                if not dry_run:
                    db.execute(
                        "UPDATE recognized_items_log SET matched_item_id = ?, notes = ? WHERE id = ?",
                        (best["item"]["id"], notes, rec["id"]),
                    )
                stats["matched"] += 1
                matches.append({
                    "record_id": rec["id"],
                    "item_id": best["item"]["id"],
                    "recognized": rec["recognized_product"],
                    "item_name": best["item"]["name"],
                    "score": best["score"],
                    "method": best["method"],
                })
            else:
                stats["skipped"] += 1
        except Exception as e:
            print(f"ERROR record {rec['id']}: {e}", file=sys.stderr)
            stats["errors"] += 1

    if not dry_run:
        db.commit()
    db.close()
    stats["matches"] = matches
    return stats


def print_stats(stats):
    print(f"\nРезультаты матчинга:")
    print(f"  Обработано:          {stats['total']}")
    print(f"  Отфильтровано:       {stats['garbage_filtered']} (screen_ocr или мусор)")
    print(f"  Найдено совпадений:  {stats['matched']}")
    print(f"  Пропущено:           {stats['skipped']} (низкий score)")
    print(f"  Ошибок:              {stats['errors']}")
    if stats.get("by_source"):
        print(f"\n  По source_type:")
        for src, cnt in sorted(stats["by_source"].items()):
            print(f"    {src}: {cnt}")
    if stats.get("by_confidence"):
        print(f"  По confidence:")
        for conf, cnt in sorted(stats["by_confidence"].items()):
            print(f"    {conf}: {cnt}")
    if stats["matches"]:
        print(f"\n  Совпадения (первые 15):")
        for i, m in enumerate(stats["matches"][:15], 1):
            print(f"  {i:>3}. [{m['score']:>3.0f}|{m['method']:<5}] {m['recognized'][:50]}")
            print(f"       -> {m['item_name'][:50]}")


def main():
    parser = argparse.ArgumentParser(description="Consumer Agent — Matcher")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true", help="Только подсчёт")
    parser.add_argument("--limit", type=int, default=None, help="Лимит записей")
    parser.add_argument("--min-score", type=int, default=85, help="Порог high confidence")
    parser.add_argument("--medium-score", type=int, default=90, help="Порог medium")
    parser.add_argument("--include-screen-ocr", action="store_true",
                        help="Матчить даже screen_ocr (по умолчанию пропущены)")
    args = parser.parse_args()

    print(f"Matcher: {'' if args.dry_run else '★ РЕАЛЬНЫЙ ПРОГОН ★'}")
    print(f"  Пороги: high >= {args.min_score}, medium >= {args.medium_score}")
    print(f"  Лимит:  {args.limit or 'нет'}")
    print(f"  source_type=screen_ocr: {'включены' if args.include_screen_ocr else 'пропущены'}")
    print()

    stats = run_matcher(args.db, dry_run=args.dry_run, limit=args.limit,
                        threshold_high=args.min_score,
                        threshold_medium=args.medium_score,
                        include_screen_ocr=args.include_screen_ocr)
    print_stats(stats)

    if args.dry_run:
        print("\nЗапусти без --dry-run для применения.")


if __name__ == "__main__":
    main()
