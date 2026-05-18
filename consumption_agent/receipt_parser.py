#!/usr/bin/env python3
"""Unified receipt parser CLI.

Inputs:
  image/pdf/text -> OCR/parser -> structured receipt -> matcher -> purchase/items.

Default file mode is dry-run. Use --apply to write to the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from consumption.db import DB_PATH, connect as db_connect
from services.receipt_pipeline import (
    StructuredReceipt,
    persist_receipt,
    process_source,
    result_to_json,
)


def parse_fiscal_text(text: str) -> dict | None:
    """Compatibility wrapper returning the old dict-like parsed receipt shape."""
    receipt = process_source(text, input_type="text", vision_fallback=False)
    if not receipt.items and receipt.total is None:
        return None
    return _legacy_dict(receipt)


def process_file(
    filepath: str,
    cheque_log_id: int | None = None,
    *,
    vision_fallback: bool = True,
) -> dict | None:
    """Parse a receipt file and return a compatibility dict, without DB writes."""
    receipt = process_source(filepath, input_type="auto", vision_fallback=vision_fallback)
    if not receipt.items and receipt.total is None:
        return None
    _print_receipt_summary(receipt, prefix="  ")
    return _legacy_dict(receipt)


def _legacy_dict(receipt: StructuredReceipt) -> dict:
    items = [
        {
            "name": item.name,
            "price": item.price,
            "qty": item.qty,
            "total": item.total,
            "is_delivery": item.is_delivery,
            "matched_item_id": item.matched_item_id,
            "match_score": item.match_score,
        }
        for item in receipt.items
    ]
    return {
        "date": receipt.date,
        "store": receipt.store,
        "total": receipt.total,
        "items": items,
        "item_count": len([item for item in items if not item["is_delivery"]]),
        "delivery_total": receipt.delivery_total,
        "raw_text": receipt.raw_text,
        "ocr_score": receipt.ocr_score,
        "engine": receipt.engine,
        "source_path": receipt.source_path,
    }


def _print_receipt_summary(receipt: StructuredReceipt, *, prefix: str = "") -> None:
    product_count = len(receipt.product_items)
    delivery = f", delivery={receipt.delivery_total:.2f}" if receipt.delivery_total else ""
    print(
        f"{prefix}{receipt.store or 'Unknown'} | {receipt.date} | "
        f"total={receipt.total or 0:.2f} | items={product_count}{delivery} | "
        f"engine={receipt.engine} score={receipt.ocr_score}"
    )


def apply_file(
    filepath: str,
    *,
    db_path: str = DB_PATH,
    dry_run: bool = True,
    source: str = "receipt_pipeline",
    data_origin: str = "receipt_pipeline",
    vision_fallback: bool = True,
) -> tuple[StructuredReceipt, object]:
    receipt = process_source(filepath, input_type="auto", vision_fallback=vision_fallback)
    conn = db_connect(db_path)
    try:
        apply_result = persist_receipt(
            conn,
            receipt,
            dry_run=dry_run,
            source=source,
            data_origin=data_origin,
            receipt_url=filepath if Path(filepath).exists() else None,
        )
    finally:
        conn.close()
    return receipt, apply_result


def batch_process(
    *,
    db_path: str = DB_PATH,
    dry_run: bool = False,
    limit: int | None = None,
    vision_fallback: bool = True,
) -> dict:
    """Process unlinked cheques_log rows with source='ozon_pdf'."""
    conn = db_connect(db_path)
    stats = {"seen": 0, "processed": 0, "skipped": 0, "errors": 0, "dry_run": dry_run}
    try:
        rows = conn.execute(
            """
            SELECT cl.id, cl.email_uid, cl.receipt_url, cl.subject
            FROM cheques_log cl
            LEFT JOIN purchases p ON cl.email_uid = p.email_message_id AND p.deleted_at IS NULL
            WHERE cl.source = 'ozon_pdf'
              AND cl.receipt_url IS NOT NULL
              AND cl.receipt_url != ''
              AND p.id IS NULL
            ORDER BY cl.id
            """
        ).fetchall()
        if limit:
            rows = rows[:limit]

        print(f"Unprocessed ozon_pdf receipts: {len(rows)}")
        for row in rows:
            stats["seen"] += 1
            filepath = row["receipt_url"]
            if not Path(filepath).exists():
                stats["skipped"] += 1
                print(f"  skip missing file: {filepath}")
                continue

            try:
                receipt = process_source(filepath, input_type="auto", vision_fallback=vision_fallback)
                _print_receipt_summary(receipt, prefix="  ")
                apply_result = persist_receipt(
                    conn,
                    receipt,
                    dry_run=dry_run,
                    source="ozon_pdf",
                    data_origin="ozon_pdf_cheque",
                    receipt_url=filepath,
                    email_message_id=row["email_uid"],
                    order_number=str(row["id"]),
                )
                stats["processed"] += 1
                print(
                    f"    {'dry-run ' if dry_run else ''}"
                    f"purchase_id={apply_result.purchase_id}, "
                    f"created={len(apply_result.created_item_ids)}, "
                    f"matched={len(apply_result.matched_item_ids)}"
                )
            except Exception as exc:
                stats["errors"] += 1
                print(f"  error {filepath}: {exc}")
        return stats
    finally:
        conn.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Consumption Agent receipt pipeline")
    parser.add_argument("--file", help="Receipt file path: image, PDF, or text")
    parser.add_argument("--cheque-id", type=int, help="Compatibility option; accepted for old scripts")
    parser.add_argument("--batch", action="store_true", help="Process unlinked ozon_pdf cheques_log rows")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Parse and match without DB writes")
    parser.add_argument("--apply", action="store_true", help="Write parsed receipt to DB")
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    parser.add_argument("--no-vision-fallback", action="store_true")
    args = parser.parse_args()

    vision_fallback = not args.no_vision_fallback

    if args.batch:
        stats = batch_process(
            db_path=args.db,
            dry_run=args.dry_run,
            limit=args.limit,
            vision_fallback=vision_fallback,
        )
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    if args.file:
        dry_run = args.dry_run or not args.apply
        receipt, apply_result = apply_file(
            args.file,
            db_path=args.db,
            dry_run=dry_run,
            vision_fallback=vision_fallback,
        )
        _print_receipt_summary(receipt)
        if args.json or dry_run:
            print(result_to_json(receipt, apply_result))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
