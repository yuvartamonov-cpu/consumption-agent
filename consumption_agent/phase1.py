#!/usr/bin/env python3
"""Phase 1 tools for consumption_agent.

Works with partial data. No schema changes required.

Commands:
  summary
  list [all|missing|warranty|expiry|ocr]
  alerts
  warranties
  scan-alerts
"""
from __future__ import annotations

import argparse
import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "consumption.db"


@dataclass
class ItemView:
    id: int
    name: str
    category_name: str
    data_origin: str
    purchase_date: Optional[str]
    purchase_price: Optional[float]
    brand: Optional[str]
    warranty_months: Optional[int]
    expiry_date: Optional[str]
    purchase_id: Optional[int]
    status: str


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    candidates = [
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def add_months(start: date, months: int) -> date:
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def get_warranty_until(item: sqlite3.Row) -> Optional[date]:
    purchase_dt = parse_date(item["purchase_date"])
    warranty_months = item["warranty_months"]
    if not purchase_dt or warranty_months is None:
        return None
    return add_months(purchase_dt, int(warranty_months))


def get_missing_fields(item: sqlite3.Row) -> list[str]:
    missing = []
    if not item["brand"]:
        missing.append("brand")
    if item["purchase_price"] is None:
        missing.append("price")
    if not item["purchase_date"]:
        missing.append("purchase_date")
    if item["warranty_months"] is None:
        missing.append("warranty")
    if not item["expiry_date"]:
        missing.append("expiry")
    if item["purchase_id"] is None:
        missing.append("purchase_link")
    return missing


def get_data_completeness(item: sqlite3.Row) -> str:
    can_warranty = can_alert_warranty(item)
    can_expiry = can_alert_expiry(item)
    if can_warranty or can_expiry:
        return "enriched"
    extra_fields = [
        item["purchase_date"],
        item["purchase_price"],
        item["brand"],
        item["warranty_months"],
        item["expiry_date"],
    ]
    if any(v is not None and v != "" for v in extra_fields):
        return "partial"
    return "minimal"


def can_alert_warranty(item: sqlite3.Row) -> bool:
    return parse_date(item["purchase_date"]) is not None and item["warranty_months"] is not None


def can_alert_expiry(item: sqlite3.Row) -> bool:
    return parse_date(item["expiry_date"]) is not None


def fetch_items(conn: sqlite3.Connection, filter_name: str = "all") -> list[sqlite3.Row]:
    query = """
    SELECT i.*, COALESCE(c.name, 'Без категории') AS category_name
    FROM items i
    LEFT JOIN categories c ON c.id = i.category_id
    WHERE i.deleted_at IS NULL
    """
    params: list[object] = []
    if filter_name == "ocr":
        query += " AND i.data_origin = 'screen_ocr'"
    elif filter_name == "warranty":
        query += " AND i.purchase_date IS NOT NULL AND i.warranty_months IS NOT NULL"
    elif filter_name == "expiry":
        query += " AND i.expiry_date IS NOT NULL"
    elif filter_name == "missing":
        query += " AND (i.brand IS NULL OR i.purchase_price IS NULL OR i.purchase_date IS NULL OR i.warranty_months IS NULL OR i.expiry_date IS NULL OR i.purchase_id IS NULL)"
    query += " ORDER BY i.created_at DESC, i.id DESC"
    return conn.execute(query, params).fetchall()


def format_item_line(item: sqlite3.Row) -> str:
    missing = get_missing_fields(item)
    completeness = get_data_completeness(item)
    parts = [
        f"#{item['id']}",
        item["name"][:60],
        f"[{item['category_name']}]",
        f"src={item['data_origin']}",
        completeness,
    ]
    if missing:
        parts.append("missing=" + ",".join(missing))
    return " | ".join(parts)


def summary(conn: sqlite3.Connection) -> str:
    items = conn.execute("SELECT * FROM items WHERE deleted_at IS NULL").fetchall()
    purchases = conn.execute("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL").fetchone()[0]
    alerts_total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    alerts_pending = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]

    minimal = partial = enriched = 0
    missing_price = missing_purchase_date = missing_warranty = missing_expiry = 0
    ocr_items = 0
    for item in items:
        comp = get_data_completeness(item)
        if comp == "minimal":
            minimal += 1
        elif comp == "partial":
            partial += 1
        else:
            enriched += 1
        if item["purchase_price"] is None:
            missing_price += 1
        if not item["purchase_date"]:
            missing_purchase_date += 1
        if item["warranty_months"] is None:
            missing_warranty += 1
        if not item["expiry_date"]:
            missing_expiry += 1
        if item["data_origin"] == "screen_ocr":
            ocr_items += 1

    lines = [
        "Phase 1 summary",
        f"- items: {len(items)}",
        f"- purchases: {purchases}",
        f"- alerts: {alerts_total} total / {alerts_pending} pending",
        f"- completeness: minimal={minimal}, partial={partial}, enriched={enriched}",
        f"- source: screen_ocr={ocr_items}, other={len(items) - ocr_items}",
        f"- missing: price={missing_price}, purchase_date={missing_purchase_date}, warranty={missing_warranty}, expiry={missing_expiry}",
    ]
    return "\n".join(lines)


def list_items(conn: sqlite3.Connection, filter_name: str, limit: int) -> str:
    rows = fetch_items(conn, filter_name)
    shown = rows[:limit]
    lines = [f"Items ({filter_name}) — {len(rows)} total, showing {len(shown)}"]
    for item in shown:
        lines.append("- " + format_item_line(item))
    return "\n".join(lines)


def list_alerts(conn: sqlite3.Connection, status: str = "pending", limit: int = 50) -> str:
    rows = conn.execute(
        """
        SELECT a.*, i.name AS item_name
        FROM alerts a
        LEFT JOIN items i ON i.id = a.item_id
        WHERE (? = 'all' OR a.status = ?)
        ORDER BY COALESCE(a.scheduled_at, a.created_at) ASC, a.id ASC
        LIMIT ?
        """,
        (status, status, limit),
    ).fetchall()
    if not rows:
        return "Активных уведомлений нет"
    lines = [f"Alerts ({status})"]
    for row in rows:
        when = row["scheduled_at"] or row["created_at"]
        lines.append(
            f"- #{row['id']} [{row['status']}] {row['alert_type']} | {row['item_name'] or 'без item'} | {when} | {row['title']}"
        )
    return "\n".join(lines)


def list_warranties(conn: sqlite3.Connection, limit: int) -> str:
    rows = fetch_items(conn, "warranty")
    entries = []
    today = date.today()
    for item in rows:
        warranty_until = get_warranty_until(item)
        if not warranty_until:
            continue
        days_left = (warranty_until - today).days
        entries.append((days_left, item, warranty_until))
    entries.sort(key=lambda x: x[0])
    shown = entries[:limit]
    if not shown:
        return "Товаров с гарантией пока нет"
    lines = [f"Warranties — {len(entries)} total, showing {len(shown)}"]
    for days_left, item, warranty_until in shown:
        lines.append(
            f"- #{item['id']} {item['name'][:60]} | buy={item['purchase_date']} | warranty={item['warranty_months']}m | until={warranty_until.isoformat()} | days_left={days_left}"
        )
    return "\n".join(lines)


def alert_exists(conn: sqlite3.Connection, item_id: int, alert_type: str, scheduled_at: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM alerts
        WHERE item_id = ?
          AND alert_type = ?
          AND scheduled_at = ?
          AND status IN ('pending', 'sent')
        LIMIT 1
        """,
        (item_id, alert_type, scheduled_at),
    ).fetchone()
    return row is not None


def create_alert(
    conn: sqlite3.Connection,
    item_id: int,
    alert_type: str,
    title: str,
    message: str,
    scheduled_at: str,
) -> bool:
    if alert_exists(conn, item_id, alert_type, scheduled_at):
        return False
    conn.execute(
        """
        INSERT INTO alerts (item_id, alert_type, title, message, scheduled_at, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (item_id, alert_type, title, message, scheduled_at),
    )
    return True


def scan_alerts(conn: sqlite3.Connection, today: Optional[date] = None) -> str:
    today = today or date.today()
    rows = conn.execute("SELECT * FROM items WHERE deleted_at IS NULL").fetchall()
    created = 0
    warranty_ready = 0
    expiry_ready = 0

    for item in rows:
        if can_alert_warranty(item):
            warranty_ready += 1
            warranty_until = get_warranty_until(item)
            if warranty_until is not None:
                days_left = (warranty_until - today).days
                if 0 <= days_left <= 30:
                    created += int(
                        create_alert(
                            conn,
                            item["id"],
                            "warranty_expiring",
                            f"Гарантия скоро истечёт: {item['name'][:80]}",
                            f"У товара '{item['name']}' гарантия истекает через {days_left} дн. ({warranty_until.isoformat()}).",
                            warranty_until.isoformat(),
                        )
                    )
        if can_alert_expiry(item):
            expiry_ready += 1
            expiry_dt = parse_date(item["expiry_date"])
            if expiry_dt is not None:
                days_left = (expiry_dt - today).days
                if 0 <= days_left <= 7:
                    created += int(
                        create_alert(
                            conn,
                            item["id"],
                            "expiry_expiring",
                            f"Срок годности скоро истечёт: {item['name'][:80]}",
                            f"У товара '{item['name']}' срок годности истекает через {days_left} дн. ({expiry_dt.isoformat()}).",
                            expiry_dt.isoformat(),
                        )
                    )
    conn.commit()
    return (
        "Alert scan complete\n"
        f"- warranty-ready items: {warranty_ready}\n"
        f"- expiry-ready items: {expiry_ready}\n"
        f"- new alerts created: {created}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 1 tools for consumption_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary")

    list_parser = sub.add_parser("list")
    list_parser.add_argument("filter", nargs="?", default="all", choices=["all", "missing", "warranty", "expiry", "ocr"])
    list_parser.add_argument("--limit", type=int, default=50)

    alerts_parser = sub.add_parser("alerts")
    alerts_parser.add_argument("status", nargs="?", default="pending", choices=["pending", "sent", "dismissed", "all"])
    alerts_parser.add_argument("--limit", type=int, default=50)

    warranties_parser = sub.add_parser("warranties")
    warranties_parser.add_argument("--limit", type=int, default=50)

    sub.add_parser("scan-alerts")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    conn = connect_db()
    try:
        if args.command == "summary":
            print(summary(conn))
        elif args.command == "list":
            print(list_items(conn, args.filter, args.limit))
        elif args.command == "alerts":
            print(list_alerts(conn, args.status, args.limit))
        elif args.command == "warranties":
            print(list_warranties(conn, args.limit))
        elif args.command == "scan-alerts":
            print(scan_alerts(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
