"""
ml_watchlist.py — Price-drop watchlist для товаров из Memory Lane.

Пользователь нажимает «🔔 Следить за ценой» в результатах /ml_search,
бот сохраняет товар (item_id + product URL + текущая цена + порог).

Cron-задача периодически перепроверяет цены через ml_providers и
отправляет Telegram-уведомление, если цена упала на ≥ threshold_pct.

Public API:
    ensure_watchlist_schema(conn)
    add_to_watchlist(conn, ...) -> int
    remove_from_watchlist(conn, watch_id)
    list_watchlist(conn, profile_id='default') -> list[dict]
    check_price_drops(conn, threshold_pct=10) -> list[dict]
    record_price_check(conn, watch_id, new_price, dropped)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# Дефолтный порог падения цены, %
DEFAULT_THRESHOLD_PCT = 10.0

# Статусы watch-записи
STATUS_ACTIVE = 'active'
STATUS_NOTIFIED = 'notified'   # цена упала, уведомление отправлено
STATUS_DISMISSED = 'dismissed'  # пользователь убрал


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def ensure_watchlist_schema(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы ml_watchlist и ml_price_history. Идемпотентно."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            profile_id TEXT NOT NULL DEFAULT 'default',
            product_url TEXT NOT NULL,
            product_title TEXT,
            store TEXT,
            source TEXT,
            initial_price INTEGER,
            last_price INTEGER,
            last_checked_at TEXT,
            threshold_pct REAL DEFAULT 10.0,
            status TEXT NOT NULL DEFAULT 'active',
            chat_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            notified_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL REFERENCES ml_watchlist(id),
            price INTEGER,
            checked_at TEXT NOT NULL DEFAULT (datetime('now')),
            dropped_pct REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watch_item ON ml_watchlist(item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watch_status ON ml_watchlist(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watch_url ON ml_watchlist(product_url)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_hist_watch ON ml_price_history(watch_id)")
    conn.commit()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def add_to_watchlist(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    product_url: str,
    product_title: str = '',
    store: str = '',
    source: str = '',
    initial_price: Optional[int] = None,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    chat_id: Optional[int] = None,
    profile_id: str = 'default',
) -> int:
    """Добавляет товар в watchlist. Возвращает watch_id.

    Если такая пара (item_id, product_url) уже есть в active — реактивирует.
    """
    ensure_watchlist_schema(conn)

    # Проверяем существующую запись (любого статуса)
    existing = conn.execute("""
        SELECT id, status FROM ml_watchlist
        WHERE item_id = ? AND product_url = ? AND profile_id = ?
    """, (item_id, product_url, profile_id)).fetchone()

    if existing:
        watch_id, status = existing
        if status != STATUS_ACTIVE:
            conn.execute("""
                UPDATE ml_watchlist
                SET status = ?, last_price = COALESCE(?, last_price),
                    threshold_pct = ?, notified_at = NULL
                WHERE id = ?
            """, (STATUS_ACTIVE, initial_price, threshold_pct, watch_id))
            conn.commit()
        return watch_id

    cur = conn.execute("""
        INSERT INTO ml_watchlist (
            item_id, profile_id, product_url, product_title, store, source,
            initial_price, last_price, threshold_pct, chat_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_id, profile_id, product_url, product_title, store, source,
          initial_price, initial_price, threshold_pct, chat_id))
    conn.commit()
    return cur.lastrowid


def remove_from_watchlist(conn: sqlite3.Connection, watch_id: int) -> bool:
    """Помечает запись как dismissed. Возвращает True если изменено."""
    ensure_watchlist_schema(conn)
    cur = conn.execute("""
        UPDATE ml_watchlist SET status = ?
        WHERE id = ? AND status != ?
    """, (STATUS_DISMISSED, watch_id, STATUS_DISMISSED))
    conn.commit()
    return cur.rowcount > 0


def list_watchlist(
    conn: sqlite3.Connection,
    *,
    profile_id: str = 'default',
    status: str = STATUS_ACTIVE,
    limit: int = 50,
) -> list[dict]:
    """Возвращает активные watch-записи."""
    ensure_watchlist_schema(conn)
    cur = conn.execute("""
        SELECT id, item_id, product_url, product_title, store, source,
               initial_price, last_price, threshold_pct, status,
               last_checked_at, created_at, notified_at
        FROM ml_watchlist
        WHERE profile_id = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (profile_id, status, limit))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_watch(conn: sqlite3.Connection, watch_id: int) -> Optional[dict]:
    """Возвращает одну watch-запись или None."""
    ensure_watchlist_schema(conn)
    cur = conn.execute("""
        SELECT id, item_id, product_url, product_title, store, source,
               initial_price, last_price, threshold_pct, status,
               last_checked_at, created_at, notified_at, chat_id, profile_id
        FROM ml_watchlist WHERE id = ?
    """, (watch_id,))
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip([d[0] for d in cur.description], row))


# ---------------------------------------------------------------------------
# Price check logic
# ---------------------------------------------------------------------------
def record_price_check(
    conn: sqlite3.Connection,
    watch_id: int,
    new_price: Optional[int],
    *,
    dropped_pct: Optional[float] = None,
) -> None:
    """Записывает результат проверки цены в history и обновляет last_price."""
    ensure_watchlist_schema(conn)
    conn.execute("""
        INSERT INTO ml_price_history (watch_id, price, dropped_pct)
        VALUES (?, ?, ?)
    """, (watch_id, new_price, dropped_pct))
    if new_price is not None:
        conn.execute("""
            UPDATE ml_watchlist
            SET last_price = ?, last_checked_at = datetime('now')
            WHERE id = ?
        """, (new_price, watch_id))
    else:
        conn.execute("""
            UPDATE ml_watchlist SET last_checked_at = datetime('now')
            WHERE id = ?
        """, (watch_id,))
    conn.commit()


def compute_drop_pct(initial: Optional[int], current: Optional[int]) -> Optional[float]:
    """Считает % падения цены. None если данных недостаточно."""
    if initial is None or current is None or initial <= 0:
        return None
    if current >= initial:
        return 0.0
    return round((initial - current) / initial * 100.0, 1)


def mark_notified(conn: sqlite3.Connection, watch_id: int) -> None:
    """Помечает запись как notified, чтобы не спамить."""
    ensure_watchlist_schema(conn)
    conn.execute("""
        UPDATE ml_watchlist
        SET status = ?, notified_at = datetime('now')
        WHERE id = ?
    """, (STATUS_NOTIFIED, watch_id))
    conn.commit()


def reactivate_watch(conn: sqlite3.Connection, watch_id: int) -> None:
    """Возвращает уведомлённую запись в active (после действия пользователя)."""
    ensure_watchlist_schema(conn)
    conn.execute("""
        UPDATE ml_watchlist SET status = ?
        WHERE id = ?
    """, (STATUS_ACTIVE, watch_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Check engine
# ---------------------------------------------------------------------------
async def check_price_drops(
    conn: sqlite3.Connection,
    *,
    price_fetcher=None,
    profile_id: str = 'default',
) -> list[dict]:
    """Проверяет все active watch-записи на падение цены.

    price_fetcher(url) -> Optional[int]:
        async-функция, возвращающая текущую цену в рублях или None.
        По умолчанию используем ml_providers WB-поиск по URL.

    Возвращает список dict с упавшими ценами:
        {watch_id, item_id, store, title, url, old_price, new_price, dropped_pct, threshold_pct}
    """
    if price_fetcher is None:
        price_fetcher = _default_price_fetcher

    watches = list_watchlist(conn, profile_id=profile_id, status=STATUS_ACTIVE)
    drops: list[dict] = []

    for w in watches:
        try:
            new_price = await price_fetcher(w['product_url'])
        except Exception as e:
            log.warning('watchlist: ошибка проверки цены для watch %s: %s', w['id'], e)
            record_price_check(conn, w['id'], None)
            continue

        initial = w['initial_price']
        dropped = compute_drop_pct(initial, new_price)
        record_price_check(conn, w['id'], new_price, dropped_pct=dropped)

        if dropped is not None and dropped >= (w['threshold_pct'] or DEFAULT_THRESHOLD_PCT):
            drops.append({
                'watch_id': w['id'],
                'item_id': w['item_id'],
                'store': w['store'],
                'title': w['product_title'],
                'url': w['product_url'],
                'old_price': initial,
                'new_price': new_price,
                'dropped_pct': dropped,
                'threshold_pct': w['threshold_pct'],
                'chat_id': w.get('chat_id'),
            })

    log.info('watchlist: проверено %d, упавших %d', len(watches), len(drops))
    return drops


async def _default_price_fetcher(url: str) -> Optional[int]:
    """Дефолтный fetcher: пытается получить цену через ml_providers.

    Для Wildberries URL → парсим WB-id и запрашиваем v5 API.
    Для остальных — None (link-only, цену проверить нельзя).
    """
    if 'wildberries.ru' not in url:
        return None
    import re
    m = re.search(r'/catalog/(\d+)/', url)
    if not m:
        return None
    wb_id = m.group(1)
    try:
        import httpx
        import ml_providers as mp
        async with httpx.AsyncClient(timeout=10.0, headers={'User-Agent': mp._UA}) as client:
            resp = await client.get(
                f'https://card.wb.ru/cards/v2/detail?dest={mp._WB_DEST}&nm={wb_id}'
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            products = (data.get('data') or {}).get('products') or []
            if not products:
                return None
            return mp._wb_extract_price(products[0])
    except Exception as e:
        log.warning('watchlist: WB price fetch failed for %s: %s', url, e)
        return None


# ---------------------------------------------------------------------------
# Формирование уведомлений
# ---------------------------------------------------------------------------
def format_drop_notification(drop: dict) -> str:
    """HTML-сообщение для Telegram о падении цены."""
    import html
    title = html.escape((drop.get('title') or 'товар')[:80])
    store = html.escape(drop.get('store') or '?')
    old_p = drop['old_price']
    new_p = drop['new_price']
    pct = drop['dropped_pct']
    diff = old_p - new_p

    lines = [
        f'💸 <b>Цена упала на {pct}%!</b>',
        '',
        f'<b>{title}</b>',
        f'🛒 {store}',
        f'Было: <s>{old_p:,} ₽</s>  →  Стало: <b>{new_p:,} ₽</b>'.replace(',', ' '),
        f'Экономия: <b>{diff:,} ₽</b>'.replace(',', ' '),
        '',
        f'<a href="{html.escape(drop["url"])}">🔗 Открыть товар</a>',
    ]
    return '\n'.join(lines)
