"""
purchase_duplicate_detector.py — обнаружение подозрительных дублей расходов
и отправка вопроса пользователю.

Логика:
- После сканирования (в cmd_dayexp / cmd_monthexp) проверяет активные записи
  на наличие дублей: одинаковая дата + магазин + сумма, но разные источники.
- Если дубль найден, отправляется сообщение с вопросом и кнопками:
  🗑 Удалить дубль / ✅ Оставить оба.
"""

from __future__ import annotations

import sqlite3
import logging
from typing import List, Tuple, Optional

log = logging.getLogger(__name__)

DUPLICATE_SUSPICION_DAYS = 7  # анализируем последние N дней


def _row_val(row, key, idx):
    """Return value by key (dict-like) or index (tuple), works with both row factories."""
    try:
        return row[key]
    except (TypeError, IndexError):
        return row[idx]


def find_suspected_duplicates(
    conn: sqlite3.Connection,
    days_back: int = DUPLICATE_SUSPICION_DAYS,
) -> List[dict]:
    """Ищет активные записи с одинаковой датой+магазин+сумма из разных источников.

    Возвращает список групп дублей. Каждая группа:
    {
        'purchase_date': str,
        'store_name': str,
        'total_amount': float,
        'count': int,
        'purchases': [(id, source, deleted_at), ...]
    }
    """
    rows = conn.execute(
        """
        SELECT purchase_date, store_name, total_amount, COUNT(*) as cnt,
               GROUP_CONCAT(id || ':' || COALESCE(source,'?'), ',') as id_src
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

    groups = []
    for r in rows:
        purchases = []
        id_src_val = _row_val(r, 'id_src', 4)
        for pair in id_src_val.split(','):
            parts = pair.split(':')
            pid = int(parts[0])
            src = ':'.join(parts[1:]) if len(parts) > 1 else '?'
            purchases.append({'id': pid, 'source': src})

        groups.append(
            {
                'purchase_date': _row_val(r, 'purchase_date', 0),
                'store_name': _row_val(r, 'store_name', 1),
                'total_amount': _row_val(r, 'total_amount', 2),
                'count': _row_val(r, 'cnt', 3),
                'purchases': purchases,
            }
        )

    return groups


def auto_resolve_if_email_dedup(
    conn: sqlite3.Connection, group: dict
) -> Optional[dict]:
    """Если в группе есть email-дубли (одинаковый источник вроде Mail.ru Zorea),
    автоматически удаляет дубли, оставляя самый новый."""
    if group['count'] < 2:
        return None

    # Группируем по источнику
    by_source: dict[str, list] = {}
    for p in group['purchases']:
        src = p['source']
        by_source.setdefault(src, []).append(p)

    resolved_any = False
    for src, purchases in by_source.items():
        if len(purchases) < 2:
            continue
        # Не автоматически решаем если все три — из разных источников
        if len(by_source) > 1 and all(len(v) == 1 for v in by_source.values()):
            continue

        # Одинаковый source — удаляем все, кроме первого
        # (сортируем по id, оставляем минимальный — первый созданный
        # или максимальный — самый новый. Оставляем самый новый, т.к. он полнее)
        sorted_p = sorted(purchases, key=lambda x: x['id'])
        keep = sorted_p[-1]  # самый новый (больший id)
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
        return None  # всё решено, вопрос не нужен

    return group  # не удалось авто-решить, нужен вопрос пользователю


def format_duplicate_question(group: dict) -> str:
    """Форматирует сообщение с вопросом о дубле."""
    lines = [
        f'⚠️ *Подозрение на дубль:*',
        f'{group["store_name"]} — {group["total_amount"]:.0f} ₽',
        f' ({group["purchase_date"]})',
        '',
        f'Записей: {group["count"]}',
    ]
    for p in group['purchases']:
        lines.append(f'  #{p["id"]} ({p["source"]})')
    lines.append('')
    lines.append('Это дублирование?')
    return '\n'.join(lines)


def build_duplicate_keyboard(group: dict) -> dict:
    """Возвращает inline-клавиатуру для вопроса о дубле."""
    ids = [str(p['id']) for p in group['purchases']]
    keyboard = {
        'inline_keyboard': [
            [
                {'text': '🗑 Удалить дубли', 'callback_data': f'dedup_delete:{",".join(ids)}'},
                {'text': '✅ Оставить', 'callback_data': f'dedup_keep:{",".join(ids)}'},
            ]
        ]
    }
    return keyboard
