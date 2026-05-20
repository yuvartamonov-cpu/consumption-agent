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


def append_expense_row(lines, row, source_icons, *, note_limit=80):
    """Добавляет строку расхода в Markdown-отчёт безопасно для Telegram."""
    _date_str, amount, store, source, notes = row
    amt = amount or 0
    src_icon = source_icons.get(source or '', '📧')
    notes_clean = (notes or '').replace('\n', ' ').strip()

    lines.append(f'{src_icon} *{esc_md(store or "—")}* — {amt:,.0f} ₽'.replace(',', ' '))

    if source in ('sms', 'sms_sber'):
        sms_time = extract_sms_display_time(notes_clean)
        if sms_time:
            lines.append(f'   🕐 {sms_time}')
        return

    if notes_clean:
        lines.append(f'   {esc_md(notes_clean[:note_limit])}')


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




async def add_tag(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Флаг: следующее фото будет принудительно обработано как бирка
    ctx.user_data['force_tag'] = True
    await update.message.reply_text(
        '📸 Отправьте фото бирки одежды/вещи.\n'
        'Я распознаю бренд, артикул, штрихкод и добавлю вещь в инвентарь.'
    )


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений — для доп. информации после vision_confirm."""
    text = (update.message.text or '').strip()

    # Проверяем, ждём ли доп. информацию о товаре
    item_id = ctx.user_data.pop('vision_awaiting_notes', None)
    if item_id:
        # Если текст пустой — не сохраняем ничего
        if not text:
            await update.message.reply_text('ℹ️ Дополнительная информация не добавлена')
            return

        # Ограничиваем 50 символами
        notes_text = text[:50]
        conn = get_db()
        try:
            # Добавляем к существующим notes
            row = conn.execute('SELECT notes FROM items WHERE id = ?', (item_id,)).fetchone()
            if row:
                existing_notes = row[0] or ''
                new_notes = existing_notes + '\nДоп. информация: ' + notes_text if existing_notes else 'Доп. информация: ' + notes_text
                conn.execute('UPDATE items SET notes = ? WHERE id = ?', (new_notes, item_id))
                conn.commit()
                await update.message.reply_text(f'✅ Дополнительная информация сохранена: {notes_text}')
                return
        except Exception as e:
            log.warning(f'text_handler: failed to save notes: {e}')
        finally:
            conn.close()

    # Если не ждём доп. информацию — игнорируем (или можно добавить другую логику)
    # Пока просто не отвечаем на обычные текстовые сообщения


async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import asyncio  # Fix for UnboundLocalError when skipping vision api
    
    if not update.message.photo:
        await update.message.reply_text('❌ Это не фото. Пожалуйста, отправьте изображение.')
        return

    # Get the highest resolution photo
    photo: PhotoSize = update.message.photo[-1]
    caption = update.message.caption or ''
    log.info(f'photo_handler: message_id={update.message.message_id}, caption={caption!r}')

    force_receipt = False
    
    # Проверяем, есть ли активная сессия загрузки чеков
    remaining_receipts = ctx.user_data.get('receipts_remaining', 0)
    if remaining_receipts > 0:
        force_receipt = True
        ctx.user_data['receipts_remaining'] = remaining_receipts - 1
        log.info(f"Forcing receipt processing from session. Remaining: {ctx.user_data['receipts_remaining']}")
        
    caption_lower = caption.strip().lower()
    if caption_lower.startswith('чек'):
        force_receipt = True
        parts = caption_lower.split()
        if len(parts) > 1 and parts[1].isdigit():
            count = int(parts[1])
            if count > 1:
                ctx.user_data['receipts_remaining'] = count - 1
            log.info(f"Started receipt session with {count} items")
        log.info("Forcing receipt processing due to caption.")

    force_tag = ctx.user_data.pop('force_tag', False)
    if caption_lower.startswith('бирка') or caption_lower.startswith('tag'):
        force_tag = True
        log.info("Forcing tag processing due to caption.")

    # Взаимоисключающие режимы: если чек, то не бирка
    if force_receipt:
        force_tag = False

    # === Редирект: /add_item + фото ===
    # Если caption начинается с /add_item — перенаправляем в cmd_add_item
    if not force_receipt and caption.strip().startswith('/add_item'):
        log.info(f'photo_handler: redirecting to cmd_add_item, args={caption.strip().split()[1:]}')
        ctx.args = caption.strip().split()[1:]
        await cmd_add_item(update, ctx)
        return

    # Если caption выглядит как описание вещи (есть бренд или срок замены)
    # — тоже перенаправляем в cmd_add_item
    if not force_receipt and caption.strip():
        from brand_parser import parse_brand_and_name
        bp = parse_brand_and_name(caption)
        if bp['name'] and (bp['brand'] or bp['replace_months']):
            log.info(f'photo_handler: redirecting to cmd_add_item (detected item description), args={caption.strip().split()}')
            ctx.args = caption.strip().split()
            await cmd_add_item(update, ctx)
            return

    # Phase B: Memory Lane fast path — если в caption есть триггер-слова или
    # хэштеги, сохраняем в memory_lane_items + media_assets и завершаем,
    # не попадая в OCR/QR-пайплайн чеков.
    try:
        import memory_lane as _ml
    except ImportError:
        _ml = None
    if not force_receipt and _ml is not None and _ml.is_memory_lane_caption(caption):
        try:
            file = await photo.get_file()
            tmp_path = os.path.join(RECEIPTS_DIR, f'_ml_{update.message.message_id}.jpg')
            await file.download_to_drive(tmp_path)
            with open(tmp_path, 'rb') as fh:
                buf = fh.read()

            conn = get_db()
            try:
                asset_id = _ml.save_media(conn, buf, mime='image/jpeg')
                parsed = _ml.parse_caption(caption, conn)

                # Обогащаем через Vision API — тема, теги, описание
                vision_info = {}
                try:
                    from vision_item import enrich_memory_lane
                    vision_info = enrich_memory_lane(tmp_path, caption)
                    if vision_info and 'error' not in vision_info:
                        # Тема из Vision если caption не дала
                        if not parsed.get('topic') and vision_info.get('topic'):
                            parsed['topic'] = vision_info['topic']
                        # Дополняем style_tags
                        v_tags = vision_info.get('style_tags', [])
                        existing = set(parsed.get('style_tags', []))
                        for t in v_tags:
                            if t.lower() not in {x.lower() for x in existing}:
                                parsed.setdefault('style_tags', []).append(t)
                except Exception as e:
                    log.warning(f'Vision enrich failed (non-critical): {e}')

                item_id = _ml.save_memory_lane(conn, caption, asset_id, parsed, vision_info=vision_info or None)
            finally:
                conn.close()

            os.remove(tmp_path)

            liked = ', '.join(parsed.get('liked', [])) or '—'
            tags = ', '.join(parsed.get('style_tags', [])) or '—'
            topic = parsed.get('topic') or '—'
            desc = vision_info.get('description', '')
            # Название: из caption (brand_parser) или Vision
            name = parsed.get('item_name') or vision_info.get('name', '')
            # Бренд: из caption (brand_parser) приоритетнее Vision
            brand = parsed.get('brand') or vision_info.get('brand')

            parts = [f'🧠 Memory Lane #{item_id}']
            if name:
                parts.append(f'📌 {name}')
            if brand:
                parts.append(f'🏷️ Бренд: {brand}')
            parts.append(f'Реакция: {liked}')
            parts.append(f'Стиль: {tags}')
            parts.append(f'Тема: {topic}')
            if desc:
                parts.append(f'📝 {desc}')
            if vision_info.get('estimated_price_rub'):
                parts.append(f'💰 Оценка: ~{vision_info["estimated_price_rub"]} ₽')

            await update.message.reply_text('\n'.join(parts))
            return
        except Exception as e:
            log.warning(f'memory_lane save failed: {e}')
            # fall through to standard handler

    receipt_path = os.path.join(RECEIPTS_DIR, f'receipt_{update.message.message_id}.jpg')
    file = await photo.get_file()
    await file.download_to_drive(receipt_path)
    log.info(f'Saved receipt: {receipt_path}')

    # === Быстрая классификация типа фото (Vision API, ~1-2 токена) ===
    # Определяем тип ДО OCR/QR, чтобы не тратить время на чеки для фото предметов
    image_type = 'other'
    if force_receipt:
        image_type = 'receipt'
        log.info("Forcing image_type to 'receipt' due to caption or session")
    elif force_tag:
        image_type = 'tag'
        log.info("Forcing image_type to 'tag' due to command or caption")
    else:
        try:
            from vision_item import classify_photo_async
            v_type = await asyncio.wait_for(
                classify_photo_async(receipt_path),
                timeout=15.0
            )
            if v_type and v_type != 'unknown':
                image_type = v_type
                log.info(f"Vision classify (fast path): {v_type}")
        except asyncio.TimeoutError:
            log.warning("Vision classify timeout after 15s (fast path)")
        except Exception as e:
            log.warning(f"Vision classify failed (fast path): {e}")

    # QR/OCR только для чеков и бирок — для предметов не нужен
    qr_data = None
    total_amount = None
    purchase_date = None
    text = ''
    if image_type in ('receipt', 'tag'):
        # Decode QR code (Ozon format) — в отдельном потоке
        log.info(f'photo_handler: decoding QR in thread for {receipt_path}')
        qr_data = await asyncio.to_thread(decode_qr, receipt_path)
        if qr_data:
            total_amount = qr_data.get('s')
            if total_amount:
                total_amount = float(total_amount)
            date_str = qr_data.get('t')
            if date_str and len(date_str) >= 8:
                purchase_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        # Run OCR only for receipts/tags — в отдельном потоке
        log.info(f'photo_handler: running OCR in thread for {receipt_path}')
        text = await asyncio.to_thread(ocr_image, receipt_path)
        # Save raw OCR for debugging
        with open(receipt_path.replace('.jpg', '_ocr.txt'), 'w', encoding='utf-8') as f:
            f.write(text or 'NO_OCR_TEXT')

    # Если fast path не сработал (image_type всё ещё 'other'), используем OCR-классификацию как fallback
    if image_type == 'other':
        image_type = classify_image_type(text or '')

    tag_probe = await asyncio.to_thread(parse_clothing_tag, text or '', receipt_path)

    # Проверяем штрихкод через pyzbar (более надёжный метод)
    pyzbar_barcode = None
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image
        img = Image.open(receipt_path)
        codes = decode(img)
        if codes:
            pyzbar_barcode = codes[0].data.decode('utf-8')
            log.info(f'pyzbar found barcode: {pyzbar_barcode}')
    except Exception as e:
        log.debug(f'pyzbar failed: {e}')

    # Считаем биркой только если:
    # 1. Есть brand + (article или barcode)
    # 2. ИЛИ есть чёткий штрихкод EAN-13 (через OCR или pyzbar)
    # 3. И текст содержит признаки бирки (размер, состав, страна)
    has_barcode = (tag_probe.get('barcode') and len(str(tag_probe.get('barcode'))) >= 8) or (pyzbar_barcode and len(pyzbar_barcode) >= 8)
    has_article = tag_probe.get('article') and len(str(tag_probe.get('article'))) >= 5
    has_brand = tag_probe.get('brand') and len(str(tag_probe.get('brand'))) >= 2

    # Проверяем, есть ли в тексте признаки бирки
    raw_text = (tag_probe.get('raw') or '').upper()
    tag_indicators = ['СОСТАВ', 'СТРАНА', 'РАЗМЕР', 'SIZE', 'MADE IN', 'АРТИКУЛ', 'ARTICLE', 'CARE', 'WASH']
    has_tag_indicators = any(ind in raw_text for ind in tag_indicators)

    is_real_tag = (
        (has_brand and (has_article or has_barcode)) or
        (has_barcode and has_tag_indicators) or
        (pyzbar_barcode and len(pyzbar_barcode) >= 10)  # EAN-13 штрихкод = точно бирка
    )

    # Если Vision API сказал tech/other, но есть признаки бирки — переопределяем
    if not force_receipt and image_type in ('unknown', 'other', 'tech') and is_real_tag and not total_amount:
        image_type = 'tag'
        log.info(f"Тип изображения: tag (brand={tag_probe.get('brand')}, article={tag_probe.get('article')}, barcode={pyzbar_barcode or tag_probe.get('barcode')})")

    # === Корректировка 'tag' -> 'receipt', если Vision API ошибся ===
    # (Например, если на чеке Самоката есть штрихкод, и is_real_tag стал True, или Vision определил чек как бирку)
    receipt_indicators = ['КАССОВЫЙ ЧЕК', 'ФИСКАЛЬНЫЙ', 'ФН ', 'ФПД', 'ОФД', 'ИНН', 'ИТОГ', 'БЕЗНАЛИЧ', 'СУММА', 'ДОСТАВКА']
    has_receipt_indicators = len([ind for ind in receipt_indicators if ind in raw_text]) >= 2
    has_fns_qr = bool(qr_data and 't' in qr_data and 's' in qr_data and 'fn' in qr_data)

    if image_type == 'tag' and (force_receipt or has_fns_qr or has_receipt_indicators or (total_amount and not is_real_tag)):
        log.info(f"Корректировка: переопределяем 'tag' -> 'receipt' (force_receipt={force_receipt}, has_fns_qr={has_fns_qr}, has_receipt_indicators={has_receipt_indicators}, total_amount={total_amount})")
        image_type = 'receipt'

    log.info(f"Итоговый тип изображения: {image_type} (is_real_tag={is_real_tag}, has_brand={has_brand}, has_article={has_article}, has_barcode={has_barcode}, pyzbar={pyzbar_barcode})")

    items = []

    # === Если это предмет/одежда/еда/интерьер (не чек и не бирка) — распознаём как вещь ===
    if image_type in ('clothing', 'food', 'interior', 'tech', 'item', 'other', 'unknown') and not qr_data:
        log.info(f'photo_handler: recognizing item, image_type={image_type}, path={receipt_path}')
        try:
            from vision_item import recognize_item_async
            import time
            start_time = time.time()
            item_info = await recognize_item_async(receipt_path)
            elapsed = time.time() - start_time
            log.info(f'photo_handler: recognize_item took {elapsed:.1f}s')
            log.info(f'photo_handler: recognize_item result={item_info}')
            if item_info and item_info.get('error') == 'timeout':
                # Таймаут распознавания — сообщаем пользователю, не сохраняем в БД
                await update.message.reply_text(
                    '❌ Объект не распознан\n\n'
                    'Попробуйте:\n'
                    '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
                    '• Использовать команду /add_item <название>'
                )
                return
            if item_info and 'error' not in item_info and item_info.get('name'):
                item_name = item_info.get('name', 'Предмет')
                item_brand = item_info.get('brand')
                item_cat = item_info.get('category', 'другое')
                item_desc = item_info.get('description', '')
                item_color = item_info.get('color')
                item_price = item_info.get('estimated_price_rub')
                style_tags = item_info.get('style_tags', [])

                # Сохраняем в БД сразу (при отклонении удалим)
                conn = get_db()
                cat_map = {
                    'одежда': 'cat_clo_everyday', 'обувь': 'cat_clo_everyday',
                    'техника': 'cat_electronics', 'мебель': 'cat_furniture',
                    'еда': 'cat_food', 'интерьер': 'cat_furniture',
                    'косметика': 'cat_cosmetics', 'аксессуары': 'cat_accessories',
                    'бытовая техника': 'cat_appliances',
                }
                slug = cat_map.get(item_cat.lower(), 'other')
                cat_id = get_category_id(conn, slug)

                notes_parts = ['Добавлено через распознавание фото']
                if item_color:
                    notes_parts.append(f'Цвет: {item_color}')
                if item_info.get('material'):
                    notes_parts.append(f'Материал: {item_info["material"]}')
                if item_desc:
                    notes_parts.append(f'Описание: {item_desc}')
                if item_price:
                    notes_parts.append(f'Оценочная цена: ~{item_price} ₽')
                notes = '\n'.join(notes_parts)

                attrs = json.dumps({
                    'color': item_color,
                    'description': item_desc,
                    'style_tags': style_tags,
                    'vision_type': item_info.get('type'),
                    'material': item_info.get('material'),
                    'estimated_price_rub': item_price,
                }, ensure_ascii=False)

                new_item_id = insert_vision_photo_item(
                    conn,
                    name=item_name,
                    brand=item_brand,
                    purchase_price=item_price,
                    category_id=cat_id,
                    attributes=attrs,
                    notes=notes,
                    purchase_date=date.today().isoformat(),
                )

                # Сохраняем фото и связываем с item
                asset_id = None
                try:
                    with open(receipt_path, 'rb') as fh:
                        buf = fh.read()
                    asset_id = save_media_asset(conn, buf, mime='image/jpeg')
                    if asset_id:
                        link_item_photo(conn, item_id=new_item_id, media_asset_id=asset_id)
                        log.info(f'vision_photo: linked photo to item_id={new_item_id}, asset_id={asset_id}')
                except Exception as e:
                    log.warning(f'vision_photo: failed to save photo: {e}')

                conn.commit()
                conn.close()

                # Показываем результат с кнопками Подтвердить/Отклонить
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                parts = ['📷 Предмет распознан']
                parts.append(f'📌 {item_name}')
                if item_brand:
                    parts.append(f'🏷️ Бренд: {item_brand}')
                parts.append(f'📂 Категория: {item_cat}')
                if item_color:
                    parts.append(f'🎨 Цвет: {item_color}')
                if item_desc:
                    parts.append(f'📝 {item_desc}')
                if style_tags:
                    parts.append(f'🏷️ Теги: {", ".join(style_tags)}')
                if item_price:
                    parts.append(f'💰 Оценка: ~{item_price} ₽')
                parts.append(f'\nID: {new_item_id}')
                parts.append('Сохранить в инвентарь?')

                # Сохраняем данные для колбэка
                ctx.user_data['vision_pending'] = {
                    'item_id': new_item_id,
                    'asset_id': asset_id,
                    'receipt_path': receipt_path,
                }

                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton('✅ Подтвердить', callback_data='vision_confirm'),
                    InlineKeyboardButton('❌ Отклонить', callback_data='vision_reject')
                ]])
                await update.message.reply_text('\n'.join(parts), reply_markup=kb)
                return
            else:
                # Результат есть, но name не распознан — сообщаем и не идём в чек
                await update.message.reply_text(
                    '❌ Объект не распознан\n\n'
                    'Попробуйте:\n'
                    '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
                    '• Использовать команду /add_item <название>'
                )
                return

        except Exception as e:
            log.warning(f'Vision item recognition failed: {e}')
            await update.message.reply_text(
                '❌ Товар не распознан по фото\n\n'
                'Попробуйте:\n'
                '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
                '• Использовать команду /add_item <название>'
            )
            return

    if image_type == 'tag':
        # === Обработка бирки ===
        log.info(f'photo_handler: processing tag, brand={tag_probe.get("brand")}, article={tag_probe.get("article")}')
        tag = tag_probe
        fx_date = purchase_date or date.today().isoformat()
        rate = await asyncio.to_thread(get_fx_rate, tag['currency'], fx_date)
        price_rub = round(tag['price'] * rate, 2) if tag['price'] else None

        conn = get_db()
        cat_id = get_category_id(conn, 'cat_clo_everyday')

        item_name = ' '.join(x for x in [tag.get('brand'), tag.get('model'), tag.get('color')] if x) or (tag.get('article') or 'tag_item')
        insert_tag_item(conn, tag=tag, item_name=item_name, price_rub=price_rub, category_id=cat_id, purchase_date=fx_date)
        conn.commit()
        conn.close()

        search_query = ' '.join(x for x in [tag.get('brand'), tag.get('model'), tag.get('article'), tag.get('color')] if x) or (tag.get('barcode') or 'fashion tag')

        # Ищем информацию через Gemini
        gemini_info = await asyncio.to_thread(
            search_product_info_gemini,
            tag.get('brand', ''),
            tag.get('article', ''),
            tag.get('barcode')
        )

        google_images_url = f"https://www.google.com/search?tbm=isch&q={quote_plus(search_query)}"
        yandex_images_url = f"https://yandex.ru/images/search?text={quote_plus(search_query)}"
        bing_images_url = f"https://www.bing.com/images/search?q={quote_plus(search_query)}"
        response_lines = ['🧥 Бирка распознана']
        response_lines.append(f"Бренд: {tag['brand'] if tag.get('brand') else 'не найден'}")
        if tag.get('model'):
            response_lines.append(f"Модель: {tag['model']}")
        if tag.get('article'):
            response_lines.append(f"Артикул: {tag['article']}")
        if tag.get('barcode'):
            response_lines.append(f"Штрихкод: {tag['barcode']}")
        if tag.get('size'):
            response_lines.append(f"Размер: {tag['size']}")
        if tag.get('color'):
            response_lines.append(f"Цвет: {tag['color']}")
        if tag.get('price'):
            if tag.get('currency') == 'RUB':
                response_lines.append(f"Цена: {tag['price']} ₽")
            else:
                response_lines.append(f"Цена: {tag['price']} {tag['currency']} (≈ {price_rub:.0f} ₽)")
        response_lines.append("Пробую прислать фото.")
        # Добавляем информацию от Gemini
        if gemini_info:
            response_lines.append('\n🔍 Найдено через Gemini:')
            if gemini_info.get('name'):
                response_lines.append(f"📌 Название: {gemini_info['name']}")
            if gemini_info.get('category'):
                response_lines.append(f"📂 Категория: {gemini_info['category']}")
            if gemini_info.get('color'):
                response_lines.append(f"🎨 Цвет: {gemini_info['color']}")
            if gemini_info.get('material'):
                response_lines.append(f"🧵 Материал: {gemini_info['material']}")
            if gemini_info.get('price_rub'):
                response_lines.append(f"💰 Цена: ~{gemini_info['price_rub']} ₽")
            if gemini_info.get('product_url'):
                response_lines.append(f"🔗 Ссылка: {gemini_info['product_url']}")

        response_lines.append(f"\nСсылки на фото:\nGoogle: {google_images_url}\nYandex: {yandex_images_url}\nBing: {bing_images_url}")
        if not tag.get('brand'):
            response_lines.append("⚠️ Бренд не найден в OCR. Нужна часть бирки с логотипом/названием бренда крупным планом.")
        if not tag.get('brand') and not tag.get('article'):
            response_lines.append(f"OCR: {(text or '')[:180].replace(chr(10), ' ')}")
        await update.message.reply_text('\n'.join(response_lines))

        # Отправляем фото от Gemini если есть
        if gemini_info and gemini_info.get('image_url'):
            try:
                await update.message.reply_photo(
                    photo=gemini_info['image_url'],
                    caption=f"🔍 Gemini: {gemini_info.get('name', search_query)}"
                )
            except Exception as e:
                log.warning(f"Failed to send Gemini image: {e}")

        # Отправляем фото из поиска
        image_urls = await asyncio.to_thread(find_product_image_urls, search_query)
        for engine_url in image_urls.values():
            if not engine_url or engine_url.startswith('https://www.google.com/search'):
                continue
            caption = next((k for k, v in image_urls.items() if v == engine_url), 'Photo')
            try:
                await update.message.reply_photo(photo=engine_url, caption=f"{caption}: {search_query}")
            except Exception as e:
                log.warning(f"Failed to send image {engine_url}: {e}")
        return

    # Unified receipt pipeline: image -> OCR/parser -> structured receipt -> matcher -> DB.
    try:
        def _process_receipt_photo():
            conn = get_db()
            try:
                receipt = process_source(receipt_path, input_type='image', vision_fallback=True)
                if total_amount and receipt.total is None:
                    receipt.total = total_amount
                if purchase_date:
                    receipt.date = purchase_date
                apply_result = persist_receipt(
                    conn,
                    receipt,
                    dry_run=False,
                    source='telegram_photo',
                    data_origin='telegram_photo',
                    receipt_url=receipt_path,
                )
                return receipt, apply_result
            finally:
                conn.close()

        receipt_result, apply_result = await asyncio.to_thread(_process_receipt_photo)
        purchase_id = apply_result.purchase_id
        purchase_date = receipt_result.date
        total_amount = receipt_result.total
        items = [
            {
                'name': item.name,
                'price': item.price or 0,
                'qty': item.qty,
                'total': item.total or item.price or 0,
            }
            for item in receipt_result.product_items
        ]
        delivery_items = [
            {
                'name': item.name,
                'price': item.price or item.total or 0,
                'qty': item.qty,
                'total': item.total or item.price or 0,
            }
            for item in receipt_result.delivery_items
        ]
        delivery = receipt_result.delivery_total
        vision_result = {'store': receipt_result.store}
        log.info(
            'receipt_pipeline: engine=%s score=%s products=%s delivery=%s purchase_id=%s',
            receipt_result.engine,
            receipt_result.ocr_score,
            len(items),
            delivery,
            purchase_id,
        )
    except Exception as e:
        log.warning(f'receipt_pipeline failed: {e}')
        items = _parse_receipt_lines(text or '', total_amount)
        delivery_items = []
        delivery = 0
        vision_result = {}
        purchase_id = None

    response_parts = ['🧾 Чек распознан']

    # Магазин из Vision API
    store_name = (vision_result or {}).get('store')
    if store_name and store_name != 'Неизвестный':
        response_parts.append(f"🏪 {store_name}")

    if purchase_date:
        response_parts.append(f"Дата: {purchase_date}")

    if total_amount:
        total_amount_clean = f"{total_amount:.2f}".rstrip('0').rstrip('.')
        response_parts.append(f"Сумма: {total_amount_clean} ₽")
    else:
        response_parts.append("Сумма: не определена")

    if items:
        response_parts.append(f"📦 Товары ({len(items)}):" )
        for item in items:
            price_str = f"{item['price']:.2f} ₽".rstrip('0').rstrip('.').rstrip('₽').strip() + ' ₽'
            qty_str = f" × {item['qty']}" if item.get('qty', 1) > 1 else ''
            response_parts.append(f"  • {item['name']} — {price_str}{qty_str}")

    # Доставка отдельным блоком
    if delivery or delivery_items:
        dl_total = delivery or sum(dli.get('price', 0) for dli in delivery_items)
        dl_clean = f"{dl_total:.2f} ₽".rstrip('0').rstrip('.').rstrip('₽').strip() + ' ₽'
        response_parts.append(f"\n🚚 Доставка: {dl_clean}")

    if not items and not delivery:
        response_parts.append("Товары: не найдены")
        response_parts.append("Добавьте вручную /add <название> <цена>")

    response_text = '\n'.join(response_parts)

    await update.message.reply_text(response_text)
















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
    add_authorized_handler(app, CommandHandler('add_tag', add_tag))
    add_authorized_handler(app, MessageHandler(filters.PHOTO, photo_handler))
    add_authorized_handler(app, MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

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
