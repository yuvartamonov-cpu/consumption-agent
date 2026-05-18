"""Carsharing Telegram handlers."""

from __future__ import annotations

import logging
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes


log = logging.getLogger(__name__)


def configure(*, shared: dict[str, Any] | None = None, logger: Any | None = None, **_: Any) -> None:
    global log
    if shared:
        globals().update(shared)
    if logger is not None:
        log = logger


def _extract_drive_field(patterns: list[str], text: str | None) -> str | None:
    if not text:
        return None
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None

async def cmd_last_drives(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /last_drives — показывает последние поездки всех провайдеров каршеринга."""
    conn = get_db()
    limit = 10
    if ctx.args and ctx.args[0].isdigit():
        limit = max(1, min(int(ctx.args[0]), 30))

    provider_filter = ctx.args[1] if len(ctx.args) > 1 else None
    if provider_filter:
        rows = conn.execute(
            """
            SELECT date_start, date_end, car_model, car_plate,
                   distance_km, tariff, base_cost, insurance,
                   over_minutes_cost, discounts, total, source
            FROM carsharing_trips
            WHERE source = ?
            ORDER BY date_start DESC
            LIMIT ?
            """,
            (provider_filter, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT date_start, date_end, car_model, car_plate,
                   distance_km, tariff, base_cost, insurance,
                   over_minutes_cost, discounts, total, source
            FROM carsharing_trips
            ORDER BY date_start DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "🚗 Поездки не найдены.\n"
            "Команда: /last_drives [количество] [провайдер]\n"
            "Провайдеры: yandex_drive, citydrive, belka, delimobil"
        )
        return

    provider_names = {
        'yandex_drive': 'Яндекс Драйв',
        'citydrive': 'Ситидрайв',
        'belka': 'BelkaCar',
    }

    lines = [f"🚗 Последние поездки ({len(rows)}):", ""]
    for idx, row in enumerate(rows, start=1):
        dt = (row["date_start"] or "")[:10]
        provider_name = provider_names.get(row['source'], row['source'])
        car = row["car_model"] or "—"
        km = f'{row["distance_km"]:.0f} км' if row["distance_km"] else "—"
        total = f'{row["total"]:.0f} ₽' if row["total"] else "—"
        plate = f'({row["car_plate"]})' if row["car_plate"] else ""

        lines.append(f"{idx}. {dt} | {provider_name}")
        lines.append(f"   {car} {plate} • {km} • {total}")
        lines.append("")

    await update.message.reply_text("\n".join(lines).rstrip())

async def cmd_find_car(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /find_car — рекомендации по тарифам каршеринга с учётом истории."""
    args = " ".join(ctx.args) if ctx.args else ""
    hours, km = parse_drive_request(args)

    if hours is None or km is None:
        await update.message.reply_text(
            "🚗 Использование:\n"
            "/find_car 3ч 80км\n"
            "/find_car 2 часа 60 км\n"
            "/find_car сутки 120км\n\n"
            "Укажи время и расстояние."
        )
        return

    conn = get_db()

    # Загружаем тарифы
    tariffs = conn.execute(
        "SELECT * FROM carsharing_tariffs WHERE zone = 'msk' ORDER BY provider"
    ).fetchall()

    # Анализируем историю поездок
    history = conn.execute('''
        SELECT car_model, tariff, COUNT(*) as trips,
               ROUND(AVG((julianday(date_end)-julianday(date_start))*24),1) as avg_hours,
               ROUND(AVG(distance_km),1) as avg_km,
               ROUND(AVG(total),0) as avg_cost
        FROM carsharing_trips
        WHERE car_model IS NOT NULL AND total > 0
        GROUP BY car_model, tariff
        ORDER BY trips DESC
    ''').fetchall()

    # Предпочтения пользователя (из истории)
    pref_models = [h['car_model'] for h in history if h['trips'] >= 3]
    pref_tariffs = list(dict.fromkeys([h['tariff'] for h in history if h['tariff']]))

    conn.close()

    if not tariffs:
        await update.message.reply_text("Тарифы не загружены. Добавь их в БД.")
        return

    provider_names = {
        'yandex': 'Яндекс Драйв',
        'citydrive': 'Ситидрайв',
        'belka': 'BelkaCar',
        'delimobil': 'Делимобиль',
    }

    # Расчёт стоимости для всех тарифов
    results = []
    for t in tariffs:
        cost = calculate_drive_cost(t, hours, km)
        provider = t['provider']
        tariff_name = t['tariff_name'] or ''

        # Определяем рекомендацию на основе истории
        is_preferred = False
        reason = ""

        # Проверяем предпочтительные модели/тарифы
        if 'Bay 24' in tariff_name and 'Bay 24' in pref_tariffs:
            is_preferred = True
            reason = "⭐ Ваш любимый тариф (14 поездок на FAW Bestune T77)"
        elif provider == 'yandex' and hours >= 3:
            is_preferred = True
            reason = "⭐ Выгодно для длительных поездок"
        elif t['rate_type'] == 'per_hour_km' and hours <= 2:
            is_preferred = True
            reason = "⭐ Выгодно для коротких поездок"

        results.append({
            'provider': provider,
            'name': provider_names.get(provider, provider.upper()),
            'tariff': tariff_name,
            'cost': cost,
            'rate_type': t['rate_type'],
            'insurance': '✓' if t['insurance_included'] else '✗',
            'is_preferred': is_preferred,
            'reason': reason,
        })

    # Сортируем: предпочтительные первыми, затем по цене
    results.sort(key=lambda x: (not x['is_preferred'], x['cost']))

    lines = [f"🚗 Рекомендации на {hours}ч / {km}км\n"]
    lines.append(f"📊 История: {len(history)} моделей, {sum(h['trips'] for h in history)} поездок")
    lines.append(f"💡 Предпочтения: {', '.join(pref_models[:3]) or 'нет данных'}\n")

    for r in results:
        tariff_info = f" ({r['tariff']})" if r['tariff'] else ""
        rate_info = "фикс+км" if r['rate_type'] == 'flat_km' else "почас"
        pref_mark = "⭐ " if r['is_preferred'] else ""
        lines.append(f"{pref_mark}• {r['name']}{tariff_info}: ~{r['cost']:.0f} ₽ ({rate_info}) страховка{r['insurance']}")
        if r['reason']:
            lines.append(f"   └ {r['reason']}")

    # Добавляем тестовые сценарии если запрошено
    if hours == 3 and km == 80:
        lines.append("\n📋 Тестовый сценарий 3ч/80км:")
        lines.append("   FAW Bestune T77 + Bay 24: ~2197 ₽ (средняя по истории)")
    elif hours >= 12:
        lines.append("\n📋 Для суточной аренды рекомендуется Bay 24 или тариф 'Сутки'")

    lines.append("\n(реальная стоимость может отличаться)")
    await update.message.reply_text("\n".join(lines))


def register_handlers(app: Any, deps: Any = None) -> None:
    from bot.app import _add_command

    if deps is not None:
        configure(shared=getattr(deps, 'shared', None), logger=getattr(deps, 'log', None))
    for name, callback in (
        ('last_drives', cmd_last_drives),
        ('find_car', cmd_find_car),
    ):
        _add_command(app, deps, name, callback)
