import sqlite3
from datetime import datetime, timedelta

from warranty_check import run_daily_alert_checks


def setup_conn():
    conn = sqlite3.connect(':memory:')
    conn.execute(
        '''
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'in_use',
            quantity INTEGER DEFAULT 1,
            min_quantity INTEGER DEFAULT 0,
            purchase_date TEXT,
            warranty_months INTEGER,
            warranty_until TEXT,
            expiry_date TEXT,
            deleted_at TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            alert_type TEXT,
            title TEXT,
            message TEXT,
            scheduled_at TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    return conn


def test_generates_warranty_and_expiry_alerts():
    conn = setup_conn()
    warranty_date = (datetime.now() + timedelta(days=10)).strftime('%Y-%m-%d')
    expiry_date = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')

    conn.execute(
        "INSERT INTO items (name, status, warranty_until, quantity, min_quantity) VALUES (?, 'in_use', ?, 5, 1)",
        ('Laptop', warranty_date),
    )
    conn.execute(
        "INSERT INTO items (name, status, expiry_date, quantity, min_quantity) VALUES (?, 'in_use', ?, 5, 1)",
        ('Yogurt', expiry_date),
    )
    conn.commit()

    created = run_daily_alert_checks(conn)
    assert created == 2

    types = [r[0] for r in conn.execute("SELECT alert_type FROM alerts ORDER BY id").fetchall()]
    assert 'warranty_expiring' in types
    assert 'expiry_approaching' in types


def test_generates_low_stock_alert_when_quantity_at_threshold():
    conn = setup_conn()
    conn.execute(
        "INSERT INTO items (name, status, quantity, min_quantity) VALUES (?, 'in_use', 2, 2)",
        ('Toilet paper',),
    )
    conn.commit()

    created = run_daily_alert_checks(conn)
    assert created == 1

    row = conn.execute("SELECT alert_type, message FROM alerts").fetchone()
    assert row[0] == 'low_stock'
    assert 'Low stock:' in row[1]


def test_does_not_duplicate_pending_alerts():
    conn = setup_conn()
    warranty_date = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
    conn.execute(
        "INSERT INTO items (name, status, warranty_until, quantity, min_quantity) VALUES (?, 'in_use', ?, 1, 0)",
        ('Headphones', warranty_date),
    )
    conn.commit()

    first = run_daily_alert_checks(conn)
    second = run_daily_alert_checks(conn)

    assert first == 1
    assert second == 0
    count = conn.execute("SELECT COUNT(*) FROM alerts WHERE alert_type='warranty_expiring'").fetchone()[0]
    assert count == 1
