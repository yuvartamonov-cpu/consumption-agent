"""
purchase_duplicate_detector.py - обнаружение подозрительных дублей расходов
и отправка вопроса пользователю.

Логика:
1) Точное совпадение date + store + amount (из разных источников).
2) Один день + один магазин - две записи с близкими суммами
   (с учётом доставки), одна из SMS, другая из email.
- Если дубль найден, отправляется сообщение с вопросом и кнопками:
  🗑 Удалить дубль / ✅ Оставить оба.
"""

from __future__ import annotations

import sqlite3
import logging
import re
from typing import List, Optional

log = logging.getLogger(__name__)

DUPLICATE_SUSPICION_DAYS = 7  # анализируем последние N дней

# Магазины, где доставка - частая причина мнимого дубля
STORE_FUZZY_AMOUNT = frozenset({'Самокат', 'Яндекс Лавка', 'Яндекс Еда', 'Кухня на районе', 'ВкусВилл', 'Ozon', 'Ozon fresh'})

# Паттерн для поиска доставки в notes
_DELIVERY_FEE_RX = re.compile(r'доставк[аи]?\s*[:=+-]?\s*(\d[\d\s]*[.,]?\d*)', re.IGNORECASE)


def _esc_md(text: str | None) -> str:
    if not text:
        return ''
    for ch in ('\\', '`', '*', '_', '[', ']', '(', ')'):
        text = str(text).replace(ch, '\\' + ch)
    return text


def _row_val(row, key, idx):
    """Return value by key (dict-like) or index (tuple), works with both row factories."""
    try:
        return row[key]
    except (TypeError, IndexError):
        return row[idx]


def _extract_delivery_fee(notes: str) -> Optional[float]:
    if not notes:
        return None
    match = _DELIVERY_FEE_RX.search(notes)
    if not match:
        return None
    try:
        return float(match.group(1).replace(' ', '').replace(',', '.'))
    except Exception:
        return None


def _source_icon(source: str) -> str:
    """Иконка для источника записи."""
    icons = {
        'gmail': '📧 Gmail',
        'yandex': '📧 Яндекс',
        'yandex_food': '🍽 Яндекс',
        'mailru_zorea': '📧 Mail.ru Z',
        'mailru_neutrinon': '📧 Mail.ru N',
        'sms': '📱 SMS',
        'sms_sber': '📱 SMS(Сбер)',
        'sber_statement': '🏦 Выписка',
        'local': '📝 Локально',
        'manual': '✏️ Вручную',
    }
    return icons.get(source, f'🔗 {source}')


def _fetch_purchase_details(conn: sqlite3.Connection, purchase_id: int) -> dict:
    """Извлекает детали покупки по id."""
    row = conn.execute(
        'SELECT total_amount, notes, source FROM purchases WHERE id = ?',
        (purchase_id,),
    ).fetchone()
    if not row:
        return {'amount': 0, 'notes': '', 'source': '?'}
    return {'amount': row[0], 'notes': row[1] or '', 'source': row[2] or '?'}


def _extract_time_from_notes(notes: str) -> str:
    """Извлекает время из notes (формат 'время HH:MM')."""
    m = re.search(r'время\s+(\d{2}:\d{2})', notes)
    return m.group(1) if m else ''


def find_suspected_duplicates(
    conn: sqlite3.Connection,
    days_back: int = DUPLICATE_SUSPICION_DAYS,
) -> List[dict]:
    """Ищет подозрительные дубли расходов.

    Возвращает список групп кандидатов в дубли. Каждая группа:
    {
        'purchase_date': str,
        'store_name': str,
        'total_amount': float,  # максимальная сумма в группе
        'count': int,
        'purchases': [{'id': int, 'source': str, 'amount': float}, ...]
    }
    """
    groups = []

    # --- Режим 1: точное совпадение дата + магазин + сумма ---
    rows = conn.execute(
        """
        SELECT purchase_date, store_name, total_amount, COUNT(*) as cnt,
               GROUP_CONCAT(id || ':' || COALESCE(source,'?') || ':' || COALESCE(ROUND(total_amount, 2), '0'), ',') as id_src_amt
        FROM purchases
        WHERE purchase_date >= date('now', ?)
          AND deleted_at IS NULL
          AND total_amount IS NOT NULL
          AND store_name IS NOT NULL
          AND store_name != ''
        GROUP BY purchase_date, store_name, total_amount
        HAVING cnt > 1
        ORDER BY purchase_date DESC, total_amount DESC
        """,
        (f'-{days_back} days',),
    ).fetchall()

    for r in rows:
        purchases = []
        id_src_amt_val = _row_val(r, 'id_src_amt', 4)
        for pair in id_src_amt_val.split(','):
            parts = pair.split(':')
            pid = int(parts[0])
            src = parts[1] if len(parts) > 1 else '?'
            amt = float(parts[2]) if len(parts) > 2 else 0.0
            purchases.append({'id': pid, 'source': src, 'amount': amt})

        groups.append({
            'purchase_date': _row_val(r, 'purchase_date', 0),
            'store_name': _row_val(r, 'store_name', 1),
            'total_amount': float(_row_val(r, 'total_amount', 2)),
            'count': _row_val(r, 'cnt', 3),
            'purchases': purchases,
        })

    # --- Режим 2: один день + один магазин + близкие суммы из разных источников ---
    store_rows = conn.execute(
        """
        SELECT purchase_date, store_name, COUNT(*) as cnt,
               GROUP_CONCAT(id || ':' || COALESCE(source,'?') || ':' || COALESCE(ROUND(total_amount, 2), '0'), ',') as id_src_amt
        FROM purchases
        WHERE purchase_date >= date('now', ?)
          AND deleted_at IS NULL
          AND total_amount IS NOT NULL
          AND total_amount > 0
          AND store_name IS NOT NULL
          AND store_name != ''
        GROUP BY purchase_date, store_name
        HAVING cnt >= 2
        ORDER BY purchase_date DESC, store_name
        """,
        (f'-{days_back} days',),
    ).fetchall()

    existing_keys = {(g['purchase_date'], g['store_name']) for g in groups}

    for r in store_rows:
        date = _row_val(r, 'purchase_date', 0)
        store = _row_val(r, 'store_name', 1)
        id_src_amt_val = _row_val(r, 'id_src_amt', 3)

        if (date, store) in existing_keys:
            continue

        purchases = []
        for pair in id_src_amt_val.split(','):
            parts = pair.split(':')
            pid = int(parts[0])
            src = parts[1] if len(parts) > 1 else '?'
            amt = float(parts[2]) if len(parts) > 2 else 0.0
            purchases.append({'id': pid, 'source': src, 'amount': amt})

        if store not in STORE_FUZZY_AMOUNT:
            continue

        sms_purchases = [p for p in purchases if p['source'] in ('sms', 'sms_sber')]
        email_purchases = [p for p in purchases if p['source'] not in ('sms', 'sms_sber')]

        if not sms_purchases or not email_purchases:
            continue

        delivery_rows = conn.execute(
            """
            SELECT id, notes
            FROM purchases
            WHERE purchase_date = ? AND store_name = ? AND deleted_at IS NULL
            """,
            (date, store),
        ).fetchall()

        notes_map = {dr[0]: (dr[1] or '') for dr in delivery_rows}

        matched = False
        for sp in sms_purchases:
            if matched:
                break
            for ep in email_purchases:
                sp_delivery = _extract_delivery_fee(notes_map.get(sp['id'], ''))
                ep_delivery = _extract_delivery_fee(notes_map.get(ep['id'], ''))

                sp_base = sp['amount'] - (sp_delivery or 0)
                ep_base = ep['amount'] - (ep_delivery or 0)

                diffs = [
                    abs(sp['amount'] - ep['amount']),
                    abs(sp_base - ep_base),
                    abs(sp['amount'] - ep_base),
                    abs(sp_base - ep['amount']),
                ]
                min_diff = min(diffs)

                if min_diff <= 500 and sp['amount'] > 0 and ep['amount'] > 0:
                    groups.append({
                        'purchase_date': date,
                        'store_name': store,
                        'total_amount': max(sp['amount'], ep['amount']),
                        'count': 2,
                        'purchases': [sp, ep],
                    })
                    matched = True
                    break

    return groups


def auto_resolve_if_email_dedup(
    conn: sqlite3.Connection, group: dict
) -> Optional[dict]:
    """Если в группе есть email-дубли (одинаковый источник),
    автоматически удаляет дубли, оставляя самый новый.
    Если в группе SMS+email с близкими суммами - не авто-решаем."""
    if group['count'] < 2:
        return None

    # Группируем по источнику
    by_source: dict[str, list] = {}
    for p in group['purchases']:
        src = p['source']
        by_source.setdefault(src, []).append(p)

    # Если есть и SMS и email - не авто-решаем, спрашиваем пользователя
    has_sms = any(src.startswith('sms') for src in by_source)
    has_email = any(not src.startswith('sms') for src in by_source)
    if has_sms and has_email:
        return group

    resolved_any = False
    for src, purchases in by_source.items():
        if len(purchases) < 2:
            continue
        sorted_p = sorted(purchases, key=lambda x: x['id'])
        keep = sorted_p[-1]
        for p in sorted_p[:-1]:
            conn.execute(
                'UPDATE purchases SET deleted_at = datetime("now") WHERE id = ?',
                (p['id'],),
            )
            log.info(
                f"auto-dedup: deleted #{p['id']} ({p['source']}), "
                f"kept #{keep['id']} from same source"
            )
            resolved_any = True

    conn.commit()
    if resolved_any:
        return None

    return group


def format_duplicate_question(group: dict) -> str:
    """Форматирует сообщение с вопросом о дубле.
    Если передан _conn, вытаскивает детали из БД (время, иконку)."""
    conn = group.get('_conn')

    lines = [
        f'⚠️ *Подозрение на дубль:*',
        f'{_esc_md(group["store_name"])} - {group["purchase_date"]}',
        '',
    ]

    for p in group['purchases']:
        if conn:
            details = _fetch_purchase_details(conn, p['id'])
        else:
            details = {'amount': p.get('amount', 0), 'notes': '', 'source': p.get('source', '?')}

        src_icon = _source_icon(details.get('source', p.get('source', '?')))
        amt = details.get('amount', p.get('amount', 0))
        notes = details.get('notes', '')

        time_str = _extract_time_from_notes(notes)
        time_part = f' в {time_str}' if time_str else ''

        lines.append(f'  {_esc_md(src_icon)}: *{amt:.0f} ₽*{time_part}')

    lines.append('')
    lines.append('Это одно и то же списание?')
    return '\n'.join(lines)


def build_duplicate_keyboard(group: dict) -> dict:
    """Возвращает inline-клавиатуру для вопроса о дубле.
    🗑 Удалить дубль — удаляет запись с меньшей суммой (обычно SMS без доставки).
    ✅ Оставить оба — оставляет всё как есть."""
    purchases = sorted(group['purchases'], key=lambda p: (-p.get('amount', 0), -p['id']))
    # Первая — с наибольшей суммой (email-чек с доставкой), её оставляем
    delete_ids = ','.join(str(p['id']) for p in purchases[1:])
    keep_ids = ','.join(str(p['id']) for p in purchases)
    
    keyboard = {
        'inline_keyboard': [
            [
                {'text': '🗑 Удалить дубль', 'callback_data': f'dedup_delete:{delete_ids}'},
                {'text': '✅ Оставить оба', 'callback_data': f'dedup_keep:{keep_ids}'},
            ]
        ]
    }
    return keyboard
