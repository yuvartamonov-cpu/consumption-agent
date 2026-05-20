"""Finance and status Telegram handlers."""

from __future__ import annotations

import logging
import os
import traceback
from datetime import date
from typing import Any, Callable

from telegram import Update
from telegram.ext import ContextTypes


log = logging.getLogger(__name__)
_get_db: Callable[..., Any] | None = None


def configure(*, get_db: Callable[..., Any] | None = None, logger: Any | None = None, shared: dict[str, Any] | None = None) -> None:
    global _get_db, log
    if shared:
        globals().update(shared)
    if get_db is not None:
        _get_db = get_db
    if logger is not None:
        log = logger


def get_db(*args: Any, **kwargs: Any) -> Any:
    if _get_db is None:
        raise RuntimeError('Telegram finance handlers are not configured with get_db')
    return _get_db(*args, **kwargs)


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.execute("SELECT alert_type,title,message FROM alerts WHERE status='pending' ORDER BY created_at").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text('✅ Нет активных алертов')
        return
    icons = {'warranty_expiring':'⚠️','warranty_expired':'❌','expiry_approaching':'⏳','expired':'🚫','low_stock':'📉','price_drop':'💰'}
    lines = ['🔔 Активные алерты:\n']
    for r in rows:
        icon = icons.get(r['alert_type'], '🔔')
        lines.append(f'{icon} {r["title"]}')
        if r['message']:
            lines.append(f'   {r["message"]}')
    await update.message.reply_text('\n'.join(lines))


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /check — расширенный PDF-отчёт."""
    try:
        from fpdf import FPDF
        pdf = FPDF()
        dp = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        if not os.path.exists(dp):
            dp = 'DejaVuSans'
        pdf.add_font('DejaVu', '', dp, uni=True)
        pdf.add_font('DejaVu', 'B', dp.replace('.ttf', '-Bold.ttf'), uni=True)
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        conn = get_db()
        c = conn.cursor()

        # Заголовок
        pdf.set_font('DejaVu', 'B', 16)
        pdf.cell(0, 10, 'Consumption Agent — Отчёт', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        pdf.cell(0, 6, f'Дата: {date.today().isoformat()}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

        # Статистика
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Общая статистика', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        stats = [
            ('Товаров', c.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]),
            ('Покупок', c.execute("SELECT COUNT(*) FROM purchases WHERE deleted_at IS NULL").fetchone()[0]),
            ('Категорий', c.execute("SELECT COUNT(*) FROM categories").fetchone()[0]),
            ('С гарантией', c.execute("SELECT COUNT(*) FROM items WHERE warranty_months>0 AND deleted_at IS NULL").fetchone()[0]),
            ('Алертов', c.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]),
        ]
        for k, v in stats:
            pdf.cell(0, 6, f'  {k}: {v}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

        # Топ-10 категорий по сумме
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Топ-10 категорий по сумме', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        cats = conn.execute('''
            SELECT c.name, COUNT(i.id) as cnt, COALESCE(ROUND(SUM(i.purchase_price),0),0) as total
            FROM items i JOIN categories c ON i.category_id = c.id
            WHERE i.deleted_at IS NULL
            GROUP BY c.id ORDER BY total DESC LIMIT 10
        ''').fetchall()
        for r in cats:
            pdf.cell(0, 6, f'  {r["name"]:25s} {r["cnt"]:4d} шт.  {r["total"]:>8.0f} ₽', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

        # График трат по месяцам (прямоугольники)
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Траты по месяцам', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        months = conn.execute('''
            SELECT substr(purchase_date,1,7) as m, ROUND(SUM(purchase_price),0) as total
            FROM items WHERE deleted_at IS NULL AND purchase_price>0 AND purchase_price NOTNULL
            GROUP BY m HAVING m NOTNULL AND GLOB('[0-9][0-9][0-9][0-9]-[0-9][0-9]', m)
            ORDER BY m
        ''').fetchall()
        if months:
            max_total = max(r['total'] for r in months)
            bar_w = 160 / max(len(months), 1)
            pdf.set_font('DejaVu', '', 7)
            for r in months:
                h = (r['total'] / max_total) * 40 if max_total > 0 else 0
                x = pdf.get_x()
                y = pdf.get_y()
                pdf.set_fill_color(100, 150, 255)
                pdf.rect(x, y, bar_w, h, 'F')
                pdf.set_fill_color(0, 0, 0)
                pdf.set_xy(x, y + h + 1)
                if pdf.get_y() > 270:
                    pdf.set_xy(x, y - 1)
                pdf.cell(bar_w, 4, r['m'][-2:] if len(r['m'])==7 else r['m'], align='C')
                pdf.set_xy(x + bar_w, y)
            pdf.ln(6)
        pdf.ln(4)

        # Активные алерты
        pdf.set_font('DejaVu', 'B', 12)
        a_cnt = c.execute("SELECT COUNT(*) FROM alerts WHERE status='pending'").fetchone()[0]
        pdf.cell(0, 8, f'Активные алерты ({a_cnt})', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        if a_cnt:
            for r in conn.execute("SELECT alert_type, title, message FROM alerts WHERE status='pending' ORDER BY alert_type LIMIT 10").fetchall():
                pdf.cell(0, 6, f'  [{r["alert_type"][:15]:15s}] {r["title"][:50]}', new_x='LMARGIN', new_y='NEXT')
        else:
            pdf.cell(0, 6, '  Нет активных алертов', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

        # Топ-20 товаров по цене (без дублей)
        pdf.set_font('DejaVu', 'B', 12)
        pdf.cell(0, 8, 'Топ-20 товаров по цене', new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('DejaVu', '', 10)
        top_items = conn.execute('''
            SELECT name, purchase_price, category_id FROM items
            WHERE deleted_at IS NULL AND purchase_price > 0 AND purchase_price NOTNULL
            GROUP BY ROUND(purchase_price,-2), name HAVING MIN(id)
            ORDER BY purchase_price DESC LIMIT 20
        ''').fetchall()
        for r in top_items:
            price_str = f'{r["purchase_price"]:>8.0f} ₽' if r["purchase_price"] else ''
            cat_str = (r["category_id"] or '')[:10]
            pdf.cell(0, 6, f'  {price_str}  {r["name"][:45]:45s} [{cat_str}]', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)

        # Сохраняем и шлём
        pdf_path = '/tmp/consumption_agent_report.pdf'
        pdf.output(pdf_path)
        conn.close()

        await update.message.reply_text('📊 Отчёт готов:', reply_to_message_id=update.message.message_id)
        await update.message.reply_document(open(pdf_path, 'rb'), filename='report.pdf')

    except Exception as e:
        log.warning(f'cmd_check error: {e}')
        print(traceback.format_exc())
        await update.message.reply_text(f'❌ Ошибка генерации отчёта: {e}')


async def cmd_debts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /debts — принудительная проверка кредитов и займов.
    Сканирует почты + SMS, показывает ближайшие платежи."""
    await update.message.reply_text('🔍 Проверяю почты и SMS на предмет кредитных уведомлений...')

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'credit_alerts.py'],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        log = result.stdout + result.stderr
        print(f'[debts] scan result:\n{log[:500]}')
    except Exception as e:
        print(f'[debts] scan error: {e}')

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, sender_name, payment_amount, payment_date,
                   CAST(julianday(payment_date) - julianday('now') AS INTEGER) as days_left
            FROM credit_alerts
            WHERE is_active = 1 AND paid_confirmed_at IS NULL
              AND payment_date NOT NULL AND payment_date != ''
              AND julianday(payment_date) - julianday('now') <= 30
            ORDER BY payment_date
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text('✅ Нет активных кредитных платежей на ближайшие 30 дней.')
        return

    lines = ['💳 *Кредиты к оплате (30 дней):*']
    total = 0.0
    urgent_count = 0
    for r in rows:
        rid, sender, amount, pay_date, days = r
        if days < 0:
            prefix = '🔴 ПРОСРОЧЕН'
            urgent_count += 1
        elif days <= 3:
            prefix = '🟡 СРОЧНО'
            urgent_count += 1
        elif days <= 7:
            prefix = '🟢 На этой неделе'
        else:
            prefix = '⚪'

        amount_str = f'{amount:.2f} ₽' if amount else '—'
        date_str = pay_date or '—'
        days_str = f'(через {days} дн.)' if days >= 0 else f'(просрочено {-days} дн.)'
        lines.append(f'\n{prefix} *{esc_md(sender)}* — {amount_str}')
        lines.append(f'   📅 {date_str} {days_str}')

        if amount:
            total += amount

    lines.append(f'\n💰 *Итого: {total:.2f} ₽*')
    if urgent_count:
        lines.append(f'⚠️ {urgent_count} платеж(а/ей) требуют внимания!')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')

async def cmd_fines(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /fines — принудительная проверка штрафов на всех почтах + SMS.
    Показывает неоплаченные с кнопкой ✅ Оплачено."""
    await update.message.reply_text('🔍 Проверяю почты и SMS на предмет штрафов...')

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'scripts/fines_bot.py', '--days', '7', '--check-sms'],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        log = result.stdout + result.stderr
        print(f'[fines] scan result:\n{log[:500]}')
    except Exception as e:
        print(f'[fines] scan error: {e}')

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, type, number, amount, description, vehicle, fine_date, vendor, paid_confirmed_at
            FROM fines
            WHERE paid_confirmed_at IS NULL
            ORDER BY fine_date DESC
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text('✅ Нет неоплаченных штрафов.')
        return

    # Группируем по номеру — берём последний статус (DESC по дате)
    by_number = {}
    for r in rows:
        num = r[2] or str(r[0])
        by_number[num] = r

    # Берём только те, где нет paid_confirmed_at (по номеру штрафа дедуплицируем)
    active = []
    for r in by_number.values():
        if r[1] == 'new' or (r[1] in ('paid', 'fined') and r[8] is None):
            active.append(r)

    if not active:
        await update.message.reply_text('✅ Нет неоплаченных штрафов.')
        return

    # Сводка
    total = sum(r[3] or 0 for r in active)
    summary_lines = [f'🚨 *Штрафы: {len(active)} шт., всего {total:.0f} ₽*']
    for r in active:
        amount = r[3] or 0
        desc = (r[4] or '').strip()[:60]
        date_str = r[6] or '—'
        summary_lines.append(f'  🔴 {amount:.0f} ₽ — {esc_md(desc) or "Штраф"} ({date_str})')
    summary_lines.append(f'\n⬇️ Отправляю детали с кнопками...')
    await update.message.reply_text('\n'.join(summary_lines), parse_mode='Markdown')

    # Каждый штраф отдельным сообщением с кнопкой
    for r in active:
        fine_id = r[0]
        amount = r[3] or 0
        desc = (r[4] or '').strip()
        date_str = r[6] or '—'
        vendor = (r[7] or '').strip()[:60]
        veh = (r[5] or '').strip()[:15]
        num_str = (r[2] or '')[:20]

        detail_lines = [f'🚨 *Штраф: {amount:.0f} ₽*']
        if desc:
            detail_lines.append(f'📋 {esc_md(desc)}')
        detail_lines.append(f'📅 {date_str}')
        if veh:
            detail_lines.append(f'🚗 {veh}')
        if vendor:
            detail_lines.append(f'🏛 {vendor}')
        if num_str:
            detail_lines.append(f'№ {num_str}')

        keyboard = {
            'inline_keyboard': [[
                {'text': '✅ Оплачено', 'callback_data': f'fine_paid:{fine_id}'}
            ]]
        }

        await update.message.reply_text(
            '\n'.join(detail_lines),
            parse_mode='Markdown',
            reply_markup=keyboard
        )

async def cmd_dayexp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /dayexp [N] — чеки за N дней (включая сегодня) с принудительным сканированием.
    По умолчанию N=1 — только сегодня."""
    n_days = 1
    if ctx.args and len(ctx.args) > 0:
        try:
            n_days = int(ctx.args[0])
            if n_days < 1:
                n_days = 1
        except ValueError:
            pass

    day_label = f'последние {n_days} дн.' if n_days > 1 else 'сегодня'
    msg = await update.message.reply_text(f'🔍 Сканирую почты и SMS за {day_label}...')

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, 'daily_cheque_scan.py',
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        log_text = (stdout + stderr).decode('utf-8', errors='replace')[:500]
        print(f'[dayexp] scan result:\n{log_text}')
    except asyncio.TimeoutError:
        print('[dayexp] scan timeout')
    except Exception as e:
        print(f'[dayexp] scan error: {e}')
        await msg.edit_text('⚠️ Ошибка сканирования почт.')
        return

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date >= date('now', ?)
              AND purchase_date <= date('now')
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY purchase_date DESC, total_amount DESC
        """, (f'-{n_days - 1} days',)).fetchall()
    finally:
        conn.close()

    if not rows:
        date_range = f'{datetime.now().strftime("%d.%m.%Y")}' if n_days == 1 else f'за последние {n_days} дн. (по {datetime.now().strftime("%d.%m.%Y")})'
        await msg.edit_text(f'📭 {date_range} покупок не найдено.')
        return

    total = sum(r[1] or 0 for r in rows)
    source_icons = {'gmail': '📧', 'yandex': '📧', 'yandex_food': '🍽', 'sms': '📱', 'sms_sber': '📱', 'sber_statement': '🏦', 'local': '📝', 'manual': '✏️'}

    today_str = datetime.now().strftime('%d.%m.%Y')
    title = f'📊 *Расходы за сегодня ({today_str})*' if n_days == 1 else f'📊 *Расходы за последние {n_days} дн. (по {today_str})*'
    lines = [title]
    lines.append(f'_{len(rows)} покупок, всего {total:,.0f} ₽_\n'.replace(',', ' '))

    for row in rows:
        append_expense_row(lines, row, source_icons, note_limit=80, show_notes=False)

    append_store_totals(lines, rows, '📌 *По магазинам:*')

    await safe_edit_markdown_message(msg, '\n'.join(lines))

    # Проверка на подозрительные дубли (SMS/Email, близкие суммы)
    try:
        import purchase_duplicate_detector as pdd
        conn = get_db()
        try:
            groups = pdd.find_suspected_duplicates(conn, days_back=n_days)
            for group in groups:
                resolved = pdd.auto_resolve_if_email_dedup(conn, group)
                if resolved:
                    resolved['_conn'] = conn  # для format_duplicate_question
                    question = pdd.format_duplicate_question(resolved)
                    kb = pdd.build_duplicate_keyboard(resolved)
                    await safe_send_markdown_message(
                        ctx.bot,
                        update.effective_chat.id,
                        question,
                        reply_markup=kb,
                    )
        finally:
            conn.close()
    except ImportError:
        pass
    except Exception as e:
        log.warning(f'dayexp duplicate check failed: {e}')

async def cmd_monthexp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /monthexp — расходы с начала месяца с расшифровкой по дням.
    За текущий день — принудительное сканирование почт + SMS (фоново)."""
    msg = await update.message.reply_text('🔍 Сканирую почты и SMS — собираю данные за месяц...')

    # Фоновое сканирование
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, 'daily_cheque_scan.py',
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        log_text = (stdout + stderr).decode('utf-8', errors='replace')[:500]
        print(f'[monthexp] scan result:\n{log_text}')
    except asyncio.TimeoutError:
        print('[monthexp] scan timeout')
    except Exception as e:
        print(f'[monthexp] scan error: {e}')

    today = datetime.now()
    month_start = today.strftime('%Y-%m-01')
    today_str = today.strftime('%Y-%m-%d')
    month_names = {
        1:'Январь', 2:'Февраль', 3:'Март', 4:'Апрель',
        5:'Май', 6:'Июнь', 7:'Июль', 8:'Август',
        9:'Сентябрь', 10:'Октябрь', 11:'Ноябрь', 12:'Декабрь'
    }
    month_name = f'{month_names.get(today.month, "")} {today.year}'

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT purchase_date, total_amount, store_name, source, notes
            FROM purchases
            WHERE purchase_date >= ? AND purchase_date <= ?
              AND (deleted_at IS NULL OR deleted_at = '')
            ORDER BY purchase_date, total_amount DESC
        """, (month_start, today_str)).fetchall()
    finally:
        conn.close()

    if not rows:
        await msg.edit_text(f'📭 За {month_name} (с 1 по {today.day}) покупок не найдено.')
        return

    grand_total = sum(r[1] or 0 for r in rows)
    source_icons = {'gmail': '📧', 'yandex': '📧', 'yandex_food': '🍽', 'sms': '📱', 'sms_sber': '📱', 'local': '📝', 'manual': '✏️'}

    lines = [f'📊 *Расходы с 1 {month_name.lower()} по {today.day} число*']
    lines.append(f'_{len(rows)} покупок, всего {grand_total:,.0f} ₽_\n'.replace(',', ' '))

    # Группировка по дням
    by_day = {}
    for r in rows:
        d = r[0]
        if d not in by_day:
            by_day[d] = []
        by_day[d].append(r)

    for day in sorted(by_day.keys(), reverse=True):
        day_rows = by_day[day]
        day_total = sum(r[1] or 0 for r in day_rows)
        day_label = 'Сегодня' if day == today_str else day
        lines.append(f'\n📅 *{day_label}* — {day_total:,.0f} ₽ ({len(day_rows)} покупок)'.replace(',', ' '))

        for row in day_rows:
            append_expense_row(lines, row, source_icons, note_limit=60, show_notes=False)

    append_store_totals(lines, rows, '📌 *Всего по магазинам:*')

    await safe_edit_markdown_message(msg, '\n'.join(lines))

    # Проверка на подозрительные дубли
    try:
        import purchase_duplicate_detector as pdd
        conn = get_db()
        try:
            groups = pdd.find_suspected_duplicates(conn)
            for group in groups:
                resolved = pdd.auto_resolve_if_email_dedup(conn, group)
                if resolved:
                    resolved['_conn'] = conn
                    question = pdd.format_duplicate_question(resolved)
                    kb = pdd.build_duplicate_keyboard(resolved)
                    await safe_send_markdown_message(
                        ctx.bot,
                        update.effective_chat.id,
                        question,
                        reply_markup=kb,
                    )
        finally:
            conn.close()
    except ImportError:
        pass
    except Exception as e:
        log.warning(f'monthexp duplicate check failed: {e}')


def register_handlers(app: Any, deps: Any = None) -> None:
    from bot.app import _add_command

    if deps is not None:
        configure(
            get_db=getattr(deps, 'get_db', None),
            logger=getattr(deps, 'log', None),
            shared=getattr(deps, 'shared', None),
        )
    for name, callback in (
        ('alerts', cmd_alerts),
        ('check', cmd_check),
        ('debts', cmd_debts),
        ('fines', cmd_fines),
        ('dayexp', cmd_dayexp),
        ('monthexp', cmd_monthexp),
    ):
        _add_command(app, deps, name, callback)
