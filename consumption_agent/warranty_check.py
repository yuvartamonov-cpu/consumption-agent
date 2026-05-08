#!/usr/bin/env python3
"""
Warranty/expiry/low-stock checks and alert generation.

Usage: python3 warranty_check.py [--notify]
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consumption.db")
WARRANTY_WARN_DAYS = 30
EXPIRY_WARN_DAYS = 7


def parse_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def calc_warranty_until(purchase_date, warranty_months):
    dt = parse_date(purchase_date)
    if not dt or not warranty_months:
        return None
    return dt + timedelta(days=int(warranty_months) * 30)


def ensure_items_schema(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    if "warranty_until" not in existing:
        conn.execute("ALTER TABLE items ADD COLUMN warranty_until TEXT")
    if "min_quantity" not in existing:
        conn.execute("ALTER TABLE items ADD COLUMN min_quantity INTEGER DEFAULT 0")
    conn.commit()


def update_warranty_until(conn):
    ensure_items_schema(conn)
    rows = conn.execute(
        """
        SELECT id, purchase_date, warranty_months
        FROM items
        WHERE warranty_months IS NOT NULL
          AND purchase_date IS NOT NULL
          AND deleted_at IS NULL
          AND (warranty_until IS NULL OR warranty_until = '')
    """
    ).fetchall()

    updated = 0
    for item_id, pdate, wmonths in rows:
        wu = calc_warranty_until(pdate, wmonths)
        if wu:
            conn.execute("UPDATE items SET warranty_until = ? WHERE id = ?", (wu.strftime("%Y-%m-%d"), item_id))
            updated += 1

    conn.commit()
    return updated


def check_warranties(conn):
    ensure_items_schema(conn)
    now = datetime.now()
    alerts = []

    rows = conn.execute(
        """
        SELECT id, name, warranty_until
        FROM items
        WHERE warranty_until IS NOT NULL
          AND deleted_at IS NULL
          AND status = 'in_use'
    """
    ).fetchall()

    for item_id, name, wu_str in rows:
        wu = parse_date(wu_str)
        if not wu:
            continue

        days_left = (wu - now).days
        if days_left < 0:
            alerts.append({
                "item_id": item_id,
                "name": name,
                "type": "warranty_expired",
                "message": f"Warranty expired {abs(days_left)}d ago: {name} (until {wu_str})",
            })
        elif days_left <= WARRANTY_WARN_DAYS:
            alerts.append({
                "item_id": item_id,
                "name": name,
                "type": "warranty_expiring",
                "message": f"Warranty expires in {days_left}d: {name} (until {wu_str})",
            })

    return alerts


def check_expiry_dates(conn):
    now = datetime.now()
    alerts = []

    rows = conn.execute(
        """
        SELECT id, name, expiry_date
        FROM items
        WHERE expiry_date IS NOT NULL
          AND deleted_at IS NULL
          AND status = 'in_use'
    """
    ).fetchall()

    for item_id, name, exp_str in rows:
        exp = parse_date(exp_str)
        if not exp:
            continue

        days_left = (exp - now).days
        if days_left < 0:
            alerts.append({
                "item_id": item_id,
                "name": name,
                "type": "expired",
                "message": f"Expiry date passed {abs(days_left)}d ago: {name} (until {exp_str})",
            })
        elif days_left <= EXPIRY_WARN_DAYS:
            alerts.append({
                "item_id": item_id,
                "name": name,
                "type": "expiry_approaching",
                "message": f"Expiry in {days_left}d: {name} (until {exp_str})",
            })

    return alerts


def check_low_stock(conn):
    ensure_items_schema(conn)
    alerts = []

    rows = conn.execute(
        """
        SELECT id, name, COALESCE(quantity, 0) AS quantity, COALESCE(min_quantity, 0) AS min_quantity
        FROM items
        WHERE deleted_at IS NULL
          AND COALESCE(min_quantity, 0) > 0
          AND COALESCE(quantity, 0) <= COALESCE(min_quantity, 0)
          AND status IN ('in_use', 'low_stock', 'storage')
    """
    ).fetchall()

    for item_id, name, quantity, min_quantity in rows:
        alerts.append({
            "item_id": item_id,
            "name": name,
            "type": "low_stock",
            "message": f"Low stock: {name} (qty {quantity}, min {min_quantity})",
        })

    return alerts


def save_alerts(conn, alerts):
    saved = 0
    for alert in alerts:
        existing = conn.execute(
            "SELECT id FROM alerts WHERE item_id = ? AND alert_type = ? AND status = 'pending'",
            (alert["item_id"], alert["type"]),
        ).fetchone()
        if existing:
            continue

        conn.execute(
            """
            INSERT INTO alerts (item_id, alert_type, title, message, scheduled_at, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """,
            (
                alert["item_id"],
                alert["type"],
                alert["name"],
                alert["message"],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        saved += 1

    conn.commit()
    return saved


def run_daily_alert_checks(conn):
    update_warranty_until(conn)
    alerts = check_warranties(conn) + check_expiry_dates(conn) + check_low_stock(conn)
    return save_alerts(conn, alerts)


def get_warranties_report(conn):
    ensure_items_schema(conn)
    now = datetime.now()

    rows = conn.execute(
        """
        SELECT id, name, warranty_until
        FROM items
        WHERE warranty_until IS NOT NULL
          AND deleted_at IS NULL
        ORDER BY warranty_until ASC
    """
    ).fetchall()

    if not rows:
        return "No warranty-tracked items."

    expired = []
    warning = []
    ok = []

    for _item_id, name, wu_str in rows:
        wu = parse_date(wu_str)
        if not wu:
            continue
        days_left = (wu - now).days

        line = f"- {name[:45]} until {wu_str}"
        if days_left < 0:
            expired.append(f"[EXPIRED] {line} ({abs(days_left)}d ago)")
        elif days_left <= WARRANTY_WARN_DAYS:
            warning.append(f"[WARN] {line} ({days_left}d left)")
        else:
            ok.append(f"[OK] {line} ({days_left}d)")

    parts = ["Warranty report", ""]
    if expired:
        parts.append("Expired:")
        parts.extend(expired)
        parts.append("")
    if warning:
        parts.append("Expiring soon (<30d):")
        parts.extend(warning)
        parts.append("")
    if ok:
        parts.append("Active:")
        parts.extend(ok)
    return "\n".join(parts)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    saved = run_daily_alert_checks(conn)
    print(f"Saved new alerts: {saved}")
    print("\n" + get_warranties_report(conn))
    conn.close()


if __name__ == "__main__":
    main()
