#!/usr/bin/env python3
"""
Consumption Agent Telegram Bot
Команды:
  /start — приветствие
  /list  — инвентарь по категориям
  /alerts — активные алерты
  /add <название> [<цена>] [<категория>] — добавить товар
  /check — статистика
  /help — справка

Запуск: CONSUMPTION_BOT_TOKEN=xxx python3 telegram_bot.py
"""

import logging, os, sys, re, json, subprocess, tempfile, time, html, traceback, random, asyncio
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import urlopen, Request

def get_db_with_retry(max_retries=3, backoff_base=0.5):
    """Connect through the shared DB helper."""
    if db_connect is None:
        raise RuntimeError("consumption.db.connect is unavailable")
    return db_connect(
        DB_PATH,
        timeout=10,
        max_retries=max_retries,
        delay=backoff_base,
        check_same_thread=False,
    )


def db_execute_with_retry(conn, query, params=(), max_retries=3, backoff_base=0.5):
    """Execute a statement through the shared retry helper."""
    if db_execute_shared is None:
        raise RuntimeError("consumption.db.execute_with_retry is unavailable")
    return db_execute_shared(conn, query, params, max_retries=max_retries, delay=backoff_base)


from bot.markdown import (
    esc_md,
    markdown_to_plain_text,
    safe_edit_markdown_message,
    safe_send_markdown_message,
)


def extract_sms_display_time(notes: str | None) -> str:
    """Возвращает HH:MM из SMS notes без вывода сырого текста с балансом."""
    if not notes:
        return ''
    for pattern in (r'время\s+(\d{2}:\d{2})', r'\b(\d{2}:\d{2})\b'):
        match = re.search(pattern, notes)
        if match:
            return match.group(1)
    return ''


def sanitize_expense_note(notes: str | None) -> str:
    """Скрывает служебные OCR/Vision notes из пользовательских отчётов."""
    notes_clean = (notes or '').replace('\n', ' ').strip()
    if not notes_clean:
        return ''

    if notes_clean.startswith('{') and notes_clean.endswith('}'):
        try:
            parsed = json.loads(notes_clean)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and {'engine', 'ocr_score', 'source_path'} & set(parsed):
            return ''

    lower_notes = notes_clean.lower()
    tech_markers = ('engine', 'ocr_score', 'source_path')
    if sum(marker in lower_notes for marker in tech_markers) >= 2:
        return ''

    return notes_clean


def append_expense_row(lines, row, source_icons, *, note_limit=80, show_notes=True):
    """Добавляет строку расхода в Markdown-отчёт безопасно для Telegram."""
    _date_str, amount, store, source, notes = row
    amt = amount or 0
    src_icon = source_icons.get(source or '', '📧')
    notes_clean = (notes or '').replace('\n', ' ').strip()
    display_note = sanitize_expense_note(notes)

    lines.append(f'{src_icon} *{esc_md(store or "—")}* — {amt:,.0f} ₽'.replace(',', ' '))

    if source in ('sms', 'sms_sber'):
        sms_time = extract_sms_display_time(notes_clean)
        if sms_time:
            lines.append(f'   🕐 {sms_time}')
        return

    if show_notes and display_note:
        lines.append(f'   {esc_md(display_note[:note_limit])}')


def append_store_totals(lines, rows, heading):
    """Добавляет блок итогов по магазинам с экранированием Markdown."""
    by_store = {}
    for row in rows:
        store = row[2] or 'Другое'
        by_store[store] = by_store.get(store, 0) + (row[1] or 0)

    if len(by_store) <= 1:
        return

    lines.append(f'\n{heading}')
    for store, amount in sorted(by_store.items(), key=lambda x: -x[1]):
        lines.append(f'  • {esc_md(store)}: {amount:,.0f} ₽'.replace(',', ' '))


def add_months_safe(dt, months):
    """Добавляет месяцы к дате без падения на 29/30/31 числе."""
    months = int(months)
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def parse_drive_request(text: str):
    """Парсит '3ч 80км' или '2 часа 60 км'"""
    hours = None
    km = None
    t = text.lower()
    h_match = re.search(r'(\d+)[\s]*(?:ч|час|часа|часов|h)', t)
    k_match = re.search(r'(\d+)[\s]*(?:км|km)', t)
    if h_match:
        hours = float(h_match.group(1))
    if k_match:
        km = float(k_match.group(1))
    return hours, km


def calculate_drive_cost(tariff, hours, km):
    """Расчёт стоимости поездки по тарифу провайдера."""
    km_rate = tariff['km_rate'] or 0
    rate_type = tariff['rate_type']

    if rate_type == 'flat_km':
        # Фиксированный тариф (сутки/часы) + стоимость за км
        base = (tariff['hourly_rate'] or 0) + km * km_rate
    else:
        # Поминутный/почасовой тариф + стоимость за км
        h_rate = tariff['hourly_rate'] or 0
        base = h_rate * hours + km * km_rate

    return max(round(base, -1), 500)  # округляем до 10₽, минимум 500₽

try:
    from telegram import Update, PhotoSize, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
except Exception:  # pragma: no cover - import fallback for test envs without PTB
    class _TelegramStub:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class _FilterStub:
        def __and__(self, other):
            return self

        def __invert__(self):
            return self

    class _ContextTypesStub:
        DEFAULT_TYPE = object

    class _ApplicationBuilderStub:
        def token(self, *_args, **_kwargs):
            return self

        def build(self):
            raise RuntimeError("python-telegram-bot is unavailable")

    class _ApplicationStub:
        @staticmethod
        def builder():
            return _ApplicationBuilderStub()

    Update = PhotoSize = InlineKeyboardMarkup = _TelegramStub
    Application = _ApplicationStub
    CommandHandler = MessageHandler = CallbackQueryHandler = _TelegramStub
    ContextTypes = _ContextTypesStub
    filters = type(
        "_FiltersStub",
        (),
        {"PHOTO": _FilterStub(), "TEXT": _FilterStub(), "COMMAND": _FilterStub()},
    )()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from bot.access import (
    load_allowed_chat_ids,
    load_owner_chat_id,
    parse_allowed_chat_ids as _parse_allowed_chat_ids,
    register_authorized_handler,
)
from bot.app import HandlerDeps, register_callback_handlers, register_command_handlers

# Новый модуль категоризации (Шаг 5 рефакторинга)
try:
    from consumption.categorize import categorize as auto_categorize, slug_to_cat_id
except ImportError:
    auto_categorize = lambda n: None
    slug_to_cat_id = lambda s: None

try:
    from consumption.db import (
        DB_PATH as SHARED_DB_PATH,
        connect as db_connect,
        execute_with_retry as db_execute_shared,
    )
except ImportError:
    SHARED_DB_PATH = None
    db_connect = None
    db_execute_shared = None

DB_PATH = SHARED_DB_PATH or os.path.join(SCRIPT_DIR, 'consumption.db')
RECEIPTS_DIR = os.path.join(SCRIPT_DIR, 'receipts')
Path(RECEIPTS_DIR).mkdir(exist_ok=True)
TOKEN = os.environ.get('CONSUMPTION_BOT_TOKEN', '')
OWNER_CHAT_ID = load_owner_chat_id()
ALLOWED_CHAT_IDS = load_allowed_chat_ids(OWNER_CHAT_ID)


def get_credit_alert(alert_id: int):
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    row = conn.execute(
        'SELECT id, sender_name, payment_date, payment_amount, paid_confirmed_at FROM credit_alerts WHERE id = ?',
        (alert_id,)
    ).fetchone()
    conn.close()
    return row


def get_fine(fine_id: int):
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    row = conn.execute(
        'SELECT id, type, number, amount, description, vehicle, fine_date, vendor, paid_confirmed_at FROM fines WHERE id = ?',
        (fine_id,)
    ).fetchone()
    conn.close()
    return row


def confirm_fine_paid(fine_id: int, via: str = 'telegram_button') -> bool:
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    cur = conn.execute(
        '''
        UPDATE fines
        SET paid_confirmed_at = datetime('now'),
            type = CASE WHEN type = 'new' THEN 'fined' ELSE type END
        WHERE id = ? AND paid_confirmed_at IS NULL
        ''',
        (fine_id,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def confirm_credit_alert_paid(alert_id: int, via: str = 'telegram_button') -> bool:
    from consumption.db import connect as _db_connect
    conn = _db_connect()
    cur = conn.execute(
        '''
        UPDATE credit_alerts
        SET paid_confirmed_at = datetime('now'),
            paid_confirmed_via = ?,
            is_active = 0
        WHERE id = ? AND paid_confirmed_at IS NULL
        ''',
        (via, alert_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


from services.ocr import (
    TAG_BRANDS,
    TAG_COLOR_WORDS,
    TAG_MODEL_WORDS,
    _clean_ocr_lines,
    _extract_barcode,
    _extract_tag_size_from_image,
    _ocr_crop,
    _parse_receipt_lines,
    _score_ocr_text,
    _write_text_file,
    classify_image_type,
    decode_qr,
    ocr_image,
    parse_clothing_tag,
)


def get_fx_rate(currency: str, on_date: str | None = None) -> float:
    currency = (currency or 'RUB').upper()
    if currency == 'RUB':
        return 1.0

    fallback = {'EUR': 99.0, 'USD': 91.0, 'GBP': 118.0, 'HKD': 11.4}
    try:
        dt = datetime.strptime(on_date, '%Y-%m-%d') if on_date else datetime.now()
        url = f"https://www.cbr.ru/scripts/XML_daily.asp?date_req={dt.strftime('%d/%m/%Y')}"
        with urlopen(url, timeout=10) as resp:
            xml = resp.read().decode('windows-1251', errors='ignore')
        char_map = {'EUR': 'R01239', 'USD': 'R01235', 'GBP': 'R01035', 'HKD': 'R01300'}
        char_code = char_map.get(currency)
        if char_code:
            m = re.search(rf'<Valute ID="{char_code}">.*?<Value>([\d,]+)</Value>', xml, re.S)
            if m:
                return float(m.group(1).replace(',', '.'))
    except Exception as e:
        log.warning(f"FX lookup failed for {currency}: {e}")
    return fallback.get(currency, 1.0)


from services.images import (
    _clean_image_url,
    _fetch_html,
    find_product_image_urls,
)
from services.receipt_pipeline import persist_receipt, process_source
from repositories.items import (
    get_category_id,
    insert_item,
    insert_manual_item,
    insert_tag_item,
    insert_vision_photo_item,
    mark_replaced as mark_item_replaced,
    soft_delete as soft_delete_item,
    update_item_vision_metadata,
)
from repositories.media import (
    delete_media_asset,
    get_item_photo_path,
    link_item_photo,
    save_media_asset,
    unlink_item_photos,
)


def search_product_info_gemini(brand: str, article: str, barcode: str = None) -> dict:
    """Ищет информацию о товаре через Gemini API по данным бирки."""
    try:
        import google.generativeai as genai
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            log.warning('GEMINI_API_KEY not set, skipping Gemini search')
            return {}

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        query_parts = [f'Бренд: {brand}']
        if article:
            query_parts.append(f'Артикул: {article}')
        if barcode:
            query_parts.append(f'Штрихкод: {barcode}')

        prompt = f"""Найди информацию о товаре одежды по данным бирки:
{'\n'.join(query_parts)}

Верни результат в формате JSON:
{{
  "name": "название товара",
  "category": "категория (одежда/обувь/аксессуары)",
  "description": "описание",
  "color": "цвет",
  "material": "материал",
  "price_rub": "цена в рублях (число или null)",
  "image_url": "URL фото товара или null",
  "product_url": "URL страницы товара или null"
}}

Если не нашёл информацию, верни пустые значения."""

        response = model.generate_content(prompt)
        text = response.text

        # Извлекаем JSON из ответа
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            log.info(f'Gemini search result: {result}')
            return result

        return {}
    except Exception as e:
        log.warning(f'Gemini search failed: {e}')
        return {}


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger(__name__)
# Avoid leaking bot token via verbose HTTP client logs
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.INFO)

def generate_alerts() -> int:
    """Generate daily alerts: warranty + expiry + low_stock + replace."""
    total = 0
    # 1. Гарантии и сроки годности (warranty_check)
    try:
        from warranty_check import run_daily_alert_checks
        conn = get_db()
        total = run_daily_alert_checks(conn)
        conn.close()
    except Exception as e:
        log.warning(f"generate_alerts (warranty) failed: {e}")

    # 2. Напоминания о замене вещей
    try:
        total += generate_replace_alerts()
    except Exception as e:
        log.warning(f"generate_alerts (replace) failed: {e}")

    if total:
        log.info(f"Generated {total} alerts total")
    return total


def generate_replace_alerts() -> int:
    """Generate replacement reminders for items with replace_after_months.

    Алерт создаётся за 30 дней до даты замены.
    Повторно не создаётся, если уже есть pending/sent алерт за этот item
    за последние 7 дней.
    """
    from calendar import monthrange

    def add_months_safe(dt, months):
        month = dt.month - 1 + months
        year = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)

    today = date.today()
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT id, name, brand, purchase_date, replace_after_months
            FROM items
            WHERE replace_after_months IS NOT NULL
              AND purchase_date IS NOT NULL
              AND deleted_at IS NULL
              AND status != 'replaced'
        ''').fetchall()

        created = 0
        for row in rows:
            item_id = row['id']
            name = row['name']
            brand = row['brand']
            pd = datetime.strptime(row['purchase_date'][:10], '%Y-%m-%d').date()
            replace_date = add_months_safe(pd, row['replace_after_months'])
            days_left = (replace_date - today).days

            # Алерт только если замена через ≤30 дней или уже просрочена
            if days_left > 30:
                continue

            # Проверяем, нет ли недавнего алерта (pending/sent за 7 дней)
            recent = conn.execute('''
                SELECT 1 FROM alerts
                WHERE item_id = ? AND alert_type = 'replace_reminder'
                  AND created_at >= datetime('now', '-7 days')
                  AND status IN ('pending', 'sent')
                LIMIT 1
            ''', (item_id,)).fetchone()
            if recent:
                continue

            if days_left <= 0:
                title = f'🔴 Пора менять: {name}'
                msg = f'Срок замены истёк {-days_left} дн. назад ({replace_date})'
            else:
                title = f'🔄 Скоро замена: {name}'
                msg = f'Осталось {days_left} дн. до замены ({replace_date})'
            if brand:
                msg += f'\nБренд: {brand}'

            conn.execute('''
                INSERT INTO alerts (item_id, alert_type, title, message, scheduled_at, status)
                VALUES (?, 'replace_reminder', ?, ?, datetime('now'), 'pending')
            ''', (item_id, title, msg))
            created += 1

        conn.commit()
        if created:
            log.info(f"Generated {created} replace alerts")
        return created
    except Exception as e:
        log.warning(f"generate_replace_alerts failed: {e}")
        return 0
    finally:
        conn.close()


async def run_daily_alert_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Daily task for alert generation (09:00 local by default)."""
    # SQLite datetime('now') returns UTC, so we compare in UTC to avoid a
    # 3h timezone gap (MSK is UTC+3) that would mask freshly created alerts.
    job_started_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    generated = generate_alerts()
    sent = 0
    conn = None
    try:
        conn = get_db()
        rows = conn.execute(
            """
            SELECT id, alert_type, title, message
            FROM alerts
            WHERE status = 'pending' AND created_at >= ?
            ORDER BY id
            """,
            (job_started_at,),
        ).fetchall()
        for row in rows:
            text = row['message'] or f"{row['title']} ({row['alert_type']})"

            # Для replace_reminder добавляем кнопку "✅ Заменено"
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            if row['alert_type'] == 'replace_reminder':
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton('✅ Заменено', callback_data=f'item_replaced:{row["id"]}')
                ]])
                await ctx.bot.send_message(chat_id=OWNER_CHAT_ID, text=text, reply_markup=kb)
            else:
                await ctx.bot.send_message(chat_id=OWNER_CHAT_ID, text=text)

            conn.execute("UPDATE alerts SET status='sent' WHERE id=?", (row['id'],))
            sent += 1
        conn.commit()
    except Exception as e:
        log.warning(f"daily alert delivery failed: {e}")
    finally:
        if conn is not None:
            conn.close()
    log.info(f"daily alert job completed (new alerts: {generated}, sent: {sent})")


def get_db(max_retries=3, delay=1):
    """Connect through the shared DB helper."""
    if db_connect is None:
        raise RuntimeError("consumption.db.connect is unavailable")
    return db_connect(
        DB_PATH,
        timeout=10,
        max_retries=max_retries,
        delay=delay,
        check_same_thread=False,
    )


async def run_price_drop_check(ctx: ContextTypes.DEFAULT_TYPE):
    """Cron-задача: проверяет цены и шлёт уведомления о падениях."""
    import ml_watchlist as mw
    log.info('[watchlist] запуск проверки цен')
    conn = get_db()
    try:
        drops = await mw.check_price_drops(conn)
        for drop in drops:
            chat_id = drop.get('chat_id')
            if not chat_id:
                # Fallback: первый allowed chat
                chat_id = next(iter(ALLOWED_CHAT_IDS), None)
                if not chat_id:
                    continue
            text = mw.format_drop_notification(drop)
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton('❌ Больше не следить',
                                         callback_data=f'ml_unwatch:{drop["watch_id"]}')
                ]])
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode='HTML',
                    disable_web_page_preview=True,
                    reply_markup=kb,
                )
                mw.mark_notified(conn, drop['watch_id'])
            except Exception as e:
                log.warning(f'[watchlist] не удалось отправить уведомление: {e}')
        log.info(f'[watchlist] завершено: {len(drops)} уведомлений')
    finally:
        conn.close()











def add_authorized_handler(app: Application, handler):
    """Register handler with access guard before callback execution."""
    register_authorized_handler(app, handler, ALLOWED_CHAT_IDS)

def main():
    # Инициализируем schema memory_lane (создаём таблицы topic_rules и др., если ещё нет)
    try:
        import memory_lane as _ml
        conn = get_db()
        _ml.ensure_memory_lane_schema(conn)
        conn.close()
    except Exception as e:
        log.warning(f'memory_lane schema init failed: {e}')

    if not TOKEN:
        print('❌ Укажите CONSUMPTION_BOT_TOKEN')
        print('   export CONSUMPTION_BOT_TOKEN=...')
        sys.exit(1)
    app = Application.builder().token(TOKEN).build()

    # Telegram bot menu commands
    from telegram import BotCommand
    menu = [
        BotCommand('start', 'Приветствие'),
        BotCommand('help', 'Помощь'),
        BotCommand('list', 'Инвентарь по категориям'),
        BotCommand('items', 'Вещи со сроком замены'),
        BotCommand('items_full', 'Вещи с полной инфой'),
        BotCommand('items_last', 'Последние добавленные вещи'),
        BotCommand('add', 'Добавить товар /add name price category'),
        BotCommand('add_item', 'Мастер добавления товара'),
        BotCommand('add_tag', 'Распознать бирку по фото'),
        BotCommand('alerts', 'Активные алерты'),
        BotCommand('dayexp', 'Расходы за сегодня'),
        BotCommand('monthexp', 'Расходы за месяц'),
        BotCommand('check', 'Статистика'),
        BotCommand('debts', 'Кредитные платежи'),
        BotCommand('fines', 'Неоплаченные штрафы'),
        BotCommand('warranties', 'Гарантии'),
        BotCommand('set_warranty', 'Установить гарантию /set_warranty id YYYY-MM-DD'),
        BotCommand('last_drives', 'Последние поездки каршеринга'),
        BotCommand('find_car', 'Подбор тарифа /find_car 3ч 80км'),
        BotCommand('parse', 'Парсинг последнего фото'),
        BotCommand('ml_last', 'Последние Memory Lane'),
        BotCommand('ml_search', 'Поиск в Memory Lane'),
        BotCommand('ml_stats', 'CTR по источникам'),
        BotCommand('ml_watch', 'Watchlist цен'),
        BotCommand('ml_unwatch', 'Убрать из watchlist /ml_unwatch id'),
        BotCommand('topic_set', 'Ассоциация слово→тема'),
        BotCommand('topic_list', 'Список правил тем'),
    ]
    async def _set_menu(_app):
        try:
            await _app.bot.set_my_commands(menu)
        except Exception as e:
            pass

    handler_deps = HandlerDeps(
        add_authorized_handler=add_authorized_handler,
        get_db=get_db,
        docs_dir=os.path.join(SCRIPT_DIR, 'docs'),
        log=log,
        shared=globals(),
    )
    register_command_handlers(app, handler_deps)
    register_callback_handlers(app, handler_deps)

    # Generate alerts once at startup
    gen = generate_alerts()

    # Schedule daily checks at 09:00 local server time when JobQueue is available
    from datetime import time as dt_time
    if app.job_queue is not None:
        app.job_queue.run_daily(
            run_daily_alert_job,
            time=dt_time(hour=9, minute=0, second=0),
            name='daily_alert_checks'
        )
        # Memory Lane price-drop проверка ежедневно в 10:00
        app.job_queue.run_daily(
            run_price_drop_check,
            time=dt_time(hour=10, minute=0, second=0),
            name='ml_price_drop_check'
        )
    else:
        log.warning('JobQueue is unavailable; skipping in-process daily schedule')

    log.info(f'Bot started, polling... (alerts at startup: {gen})')
    app.run_polling()

if __name__ == '__main__':
    main()
