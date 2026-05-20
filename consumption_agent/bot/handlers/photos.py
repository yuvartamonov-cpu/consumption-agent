"""Photo / tag / text Telegram handlers.

Thin orchestration layer: handles Telegram I/O (download, replies) and delegates
all heavy, Telegram-independent work to :mod:`services.photo_pipeline`.

Globals such as ``get_db``, ``RECEIPTS_DIR``, ``get_fx_rate``,
``search_product_info_gemini``, ``find_product_image_urls``, ``get_category_id``
and ``insert_tag_item`` are injected from ``telegram_bot`` via ``configure(shared=...)``
following the same pattern as the other extracted handler modules.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import date
from typing import Any
from urllib.parse import quote_plus

from telegram import Update
from telegram.ext import ContextTypes

from services import photo_pipeline as pipeline

log = logging.getLogger(__name__)


def configure(*, shared: dict[str, Any] | None = None, logger: Any | None = None, **_: Any) -> None:
    global log
    if shared:
        globals().update(shared)
    if logger is not None:
        log = logger


# ---------------------------------------------------------------------------
# /add_tag
# ---------------------------------------------------------------------------

async def add_tag(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['force_tag'] = True
    await update.message.reply_text(
        '📸 Отправьте фото бирки одежды/вещи.\n'
        'Я распознаю бренд, артикул, штрихкод и добавлю вещь в инвентарь.'
    )


# ---------------------------------------------------------------------------
# Text handler (follow-up notes after a vision confirmation)
# ---------------------------------------------------------------------------

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle plain text — used for extra info after vision_confirm."""
    text = (update.message.text or '').strip()

    item_id = ctx.user_data.pop('vision_awaiting_notes', None)
    if item_id:
        if not text:
            await update.message.reply_text('ℹ️ Дополнительная информация не добавлена')
            return

        notes_text = text[:50]
        conn = get_db()  # noqa: F821 — injected via configure(shared=...)
        try:
            row = conn.execute('SELECT notes FROM items WHERE id = ?', (item_id,)).fetchone()
            if row:
                existing_notes = row[0] or ''
                new_notes = (
                    existing_notes + '\nДоп. информация: ' + notes_text
                    if existing_notes
                    else 'Доп. информация: ' + notes_text
                )
                conn.execute('UPDATE items SET notes = ? WHERE id = ?', (new_notes, item_id))
                conn.commit()
                await update.message.reply_text(f'✅ Дополнительная информация сохранена: {notes_text}')
                return
        except Exception as e:
            log.warning(f'text_handler: failed to save notes: {e}')
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Memory Lane fast path
# ---------------------------------------------------------------------------

async def _save_memory_lane(update, photo, caption: str) -> bool:
    """Save a Memory Lane impression. Returns True if handled."""
    try:
        import memory_lane as _ml
    except ImportError:
        return False
    if not _ml.is_memory_lane_caption(caption):
        return False

    try:
        file = await photo.get_file()
        tmp_path = os.path.join(RECEIPTS_DIR, f'_ml_{update.message.message_id}.jpg')  # noqa: F821
        await file.download_to_drive(tmp_path)
        with open(tmp_path, 'rb') as fh:
            buf = fh.read()

        conn = get_db()  # noqa: F821
        try:
            asset_id = _ml.save_media(conn, buf, mime='image/jpeg')
            parsed = _ml.parse_caption(caption, conn)

            vision_info: dict = {}
            try:
                from vision_item import enrich_memory_lane
                vision_info = enrich_memory_lane(tmp_path, caption)
                if vision_info and 'error' not in vision_info:
                    if not parsed.get('topic') and vision_info.get('topic'):
                        parsed['topic'] = vision_info['topic']
                    v_tags = vision_info.get('style_tags', [])
                    existing = {x.lower() for x in parsed.get('style_tags', [])}
                    for t in v_tags:
                        if t.lower() not in existing:
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
        name = parsed.get('item_name') or vision_info.get('name', '')
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
        return True
    except Exception as e:
        log.warning(f'memory_lane save failed: {e}')
        return False  # fall through to standard handler


# ---------------------------------------------------------------------------
# Tag processing branch
# ---------------------------------------------------------------------------

async def _process_tag(update, tag_probe: dict, text: str, purchase_date: str | None) -> None:
    log.info(f'photo_handler: processing tag, brand={tag_probe.get("brand")}, article={tag_probe.get("article")}')
    tag = tag_probe
    fx_date = purchase_date or date.today().isoformat()
    rate = await asyncio.to_thread(get_fx_rate, tag['currency'], fx_date)  # noqa: F821
    price_rub = round(tag['price'] * rate, 2) if tag['price'] else None

    conn = get_db()  # noqa: F821
    cat_id = get_category_id(conn, 'cat_clo_everyday')  # noqa: F821
    item_name = ' '.join(x for x in [tag.get('brand'), tag.get('model'), tag.get('color')] if x) or (tag.get('article') or 'tag_item')
    insert_tag_item(conn, tag=tag, item_name=item_name, price_rub=price_rub, category_id=cat_id, purchase_date=fx_date)  # noqa: F821
    conn.commit()
    conn.close()

    search_query = ' '.join(x for x in [tag.get('brand'), tag.get('model'), tag.get('article'), tag.get('color')] if x) or (tag.get('barcode') or 'fashion tag')

    gemini_info = await asyncio.to_thread(
        search_product_info_gemini,  # noqa: F821
        tag.get('brand', ''),
        tag.get('article', ''),
        tag.get('barcode'),
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

    if gemini_info and gemini_info.get('image_url'):
        try:
            await update.message.reply_photo(
                photo=gemini_info['image_url'],
                caption=f"🔍 Gemini: {gemini_info.get('name', search_query)}",
            )
        except Exception as e:
            log.warning(f"Failed to send Gemini image: {e}")

    image_urls = await asyncio.to_thread(find_product_image_urls, search_query)  # noqa: F821
    for engine_url in image_urls.values():
        if not engine_url or engine_url.startswith('https://www.google.com/search'):
            continue
        cap = next((k for k, v in image_urls.items() if v == engine_url), 'Photo')
        try:
            await update.message.reply_photo(photo=engine_url, caption=f"{cap}: {search_query}")
        except Exception as e:
            log.warning(f"Failed to send image {engine_url}: {e}")


# ---------------------------------------------------------------------------
# Item recognition branch
# ---------------------------------------------------------------------------

async def _process_item(update, ctx, receipt_path: str, image_type: str) -> None:
    log.info(f'photo_handler: recognizing item, image_type={image_type}, path={receipt_path}')
    from vision_item import recognize_item_async

    start_time = time.time()
    item_info = await recognize_item_async(receipt_path)
    log.info(f'photo_handler: recognize_item took {time.time() - start_time:.1f}s')
    log.info(f'photo_handler: recognize_item result={item_info}')

    not_recognized = (
        '❌ Объект не распознан\n\n'
        'Попробуйте:\n'
        '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
        '• Использовать команду /add_item <название>'
    )

    if item_info and item_info.get('error') == 'timeout':
        await update.message.reply_text(not_recognized)
        return
    if item_info and 'error' not in item_info and item_info.get('name'):
        with open(receipt_path, 'rb') as fh:
            file_bytes = fh.read()
        item_data = {
            'name': item_info.get('name', 'Предмет'),
            'brand': item_info.get('brand'),
            'vision': item_info,
            'file_bytes': file_bytes,
            'data_origin': 'vision_photo',
        }
        from bot.handlers.items_add import _ask_category_suggestion
        msg = await update.message.reply_text(f"🔍 Анализирую товар: {item_data['name']}...")
        await _ask_category_suggestion(update, ctx, item_data, msg, get_db)  # noqa: F821
        return

    await update.message.reply_text(not_recognized)


# ---------------------------------------------------------------------------
# Receipt response formatting
# ---------------------------------------------------------------------------

def _format_receipt_reply(extraction) -> str:
    parts = ['🧾 Чек распознан']
    if extraction.store and extraction.store != 'Неизвестный':
        parts.append(f"🏪 {extraction.store}")
    if extraction.date:
        parts.append(f"Дата: {extraction.date}")
    if extraction.total:
        total_clean = f"{extraction.total:.2f}".rstrip('0').rstrip('.')
        parts.append(f"Сумма: {total_clean} ₽")
    else:
        parts.append("Сумма: не определена")

    if extraction.items:
        parts.append(f"📦 Товары ({len(extraction.items)}):")
        for item in extraction.items:
            price_str = f"{item['price']:.2f} ₽".rstrip('0').rstrip('.').rstrip('₽').strip() + ' ₽'
            qty_str = f" × {item['qty']}" if item.get('qty', 1) > 1 else ''
            parts.append(f"  • {item['name']} — {price_str}{qty_str}")

    if extraction.delivery_total or extraction.delivery_items:
        dl_total = extraction.delivery_total or sum(d.get('price', 0) for d in extraction.delivery_items)
        dl_clean = f"{dl_total:.2f} ₽".rstrip('0').rstrip('.').rstrip('₽').strip() + ' ₽'
        parts.append(f"\n🚚 Доставка: {dl_clean}")

    if not extraction.items and not extraction.delivery_total:
        parts.append("Товары: не найдены")
        parts.append("Добавьте вручную /add <название> <цена>")

    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Main photo handler
# ---------------------------------------------------------------------------

async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text('❌ Это не фото. Пожалуйста, отправьте изображение.')
        return

    photo = update.message.photo[-1]
    caption = update.message.caption or ''
    log.info(f'photo_handler: message_id={update.message.message_id}, caption={caption!r}')

    # --- mode parsing (pure) ---
    force_tag_flag = ctx.user_data.pop('force_tag', False)
    mode = pipeline.parse_photo_mode(
        caption,
        receipts_remaining=ctx.user_data.get('receipts_remaining', 0),
        force_tag_flag=force_tag_flag,
    )
    if mode.receipts_remaining is not None:
        ctx.user_data['receipts_remaining'] = mode.receipts_remaining
    force_receipt = mode.force_receipt

    # --- /add_item redirect ---
    from bot.handlers.items import cmd_add_item
    if mode.redirect_add_item:
        log.info(f'photo_handler: redirecting to cmd_add_item, args={mode.add_item_args}')
        ctx.args = mode.add_item_args
        await cmd_add_item(update, ctx)
        return
    if not force_receipt:
        is_item_desc, item_args = pipeline.looks_like_item_description(caption)
        if is_item_desc:
            log.info(f'photo_handler: redirecting to cmd_add_item (item description), args={item_args}')
            ctx.args = item_args
            await cmd_add_item(update, ctx)
            return

    # --- Memory Lane fast path ---
    if not force_receipt and await _save_memory_lane(update, photo, caption):
        return

    # --- download photo ---
    receipt_path = os.path.join(RECEIPTS_DIR, f'receipt_{update.message.message_id}.jpg')  # noqa: F821
    file = await photo.get_file()
    await file.download_to_drive(receipt_path)
    log.info(f'Saved receipt: {receipt_path}')

    image_sha = pipeline.file_sha256(receipt_path)

    # --- fast image classification ---
    image_type = 'other'
    if force_receipt:
        image_type = 'receipt'
    elif mode.force_tag:
        image_type = 'tag'
    else:
        try:
            from vision_item import classify_photo_async
            v_type = await asyncio.wait_for(classify_photo_async(receipt_path), timeout=15.0)
            if v_type and v_type != 'unknown':
                image_type = v_type
                log.info(f"Vision classify (fast path): {v_type}")
        except asyncio.TimeoutError:
            log.warning("Vision classify timeout after 15s (fast path)")
        except Exception as e:
            log.warning(f"Vision classify failed (fast path): {e}")

    # --- QR + OCR (only for receipts/tags) ---
    qr_data = None
    total_amount = None
    purchase_date = None
    text = ''
    if image_type in ('receipt', 'tag'):
        conn = get_db()  # noqa: F821
        try:
            with pipeline.timer() as t:
                qr_ocr = await asyncio.to_thread(pipeline.run_qr_ocr, receipt_path)
            pipeline.log_ocr_attempt(
                conn, image_sha=image_sha, engine='tesseract',
                image_type=image_type, status='ok', elapsed_ms=t.elapsed_ms,
            )
        except Exception as e:
            pipeline.log_ocr_attempt(
                conn, image_sha=image_sha, engine='tesseract',
                image_type=image_type, status='error', error=str(e),
            )
            raise
        finally:
            conn.close()
        qr_data = qr_ocr.qr_data
        total_amount = qr_ocr.total_amount
        purchase_date = qr_ocr.purchase_date
        text = qr_ocr.text

    if image_type == 'other':
        image_type = pipeline.classify_from_ocr(text or '')

    tag_probe = await asyncio.to_thread(pipeline.probe_clothing_tag, text or '', receipt_path)
    pyzbar_barcode = pipeline.read_pyzbar_barcode(receipt_path)

    # --- refine image type (pure heuristic) ---
    detection = pipeline.resolve_image_type(
        image_type,
        tag_probe=tag_probe,
        pyzbar_barcode=pyzbar_barcode,
        qr_data=qr_data,
        total_amount=total_amount,
        force_receipt=force_receipt,
    )
    image_type = detection.image_type
    log.info(
        "Итоговый тип изображения: %s (is_real_tag=%s, brand=%s, article=%s, barcode=%s, pyzbar=%s)",
        image_type, detection.is_real_tag, detection.has_brand,
        detection.has_article, detection.has_barcode, pyzbar_barcode,
    )

    # --- item recognition branch ---
    if image_type in pipeline.ITEM_IMAGE_TYPES and not qr_data:
        try:
            await _process_item(update, ctx, receipt_path, image_type)
        except Exception as e:
            log.warning(f'Vision item recognition failed: {e}')
            await update.message.reply_text(
                '❌ Товар не распознан по фото\n\n'
                'Попробуйте:\n'
                '• Отправить фото с описанием (например: "пиджак Corneliani")\n'
                '• Использовать команду /add_item <название>'
            )
        return

    # --- tag branch ---
    if image_type == 'tag':
        await _process_tag(update, tag_probe, text, purchase_date)
        return

    # --- receipt branch ---
    try:
        def _process_receipt_photo():
            conn = get_db()  # noqa: F821
            try:
                return pipeline.extract_receipt(
                    conn, receipt_path,
                    total_amount=total_amount, purchase_date=purchase_date,
                )
            finally:
                conn.close()

        extraction = await asyncio.to_thread(_process_receipt_photo)
        log.info(
            'receipt_pipeline: engine=%s score=%s products=%s delivery=%s purchase_id=%s',
            extraction.engine, extraction.ocr_score, len(extraction.items),
            extraction.delivery_total, extraction.purchase_id,
        )
        await update.message.reply_text(_format_receipt_reply(extraction))
    except Exception as e:
        log.warning(f'receipt_pipeline failed: {e}')
        from services.ocr import _parse_receipt_lines
        items = _parse_receipt_lines(text or '', total_amount)
        fallback = pipeline.ReceiptExtraction(
            purchase_id=None, store=None, total=total_amount,
            date=purchase_date, engine=None, ocr_score=0, items=items,
        )
        await update.message.reply_text(_format_receipt_reply(fallback))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_handlers(app: Any, deps: Any = None) -> None:
    from telegram.ext import CommandHandler, MessageHandler, filters

    if deps is not None:
        configure(shared=getattr(deps, 'shared', None), logger=getattr(deps, 'log', None))

    add = deps.add_authorized_handler if deps is not None else (lambda a, h: a.add_handler(h))
    add(app, CommandHandler('add_tag', add_tag))
    add(app, MessageHandler(filters.PHOTO, photo_handler))
    add(app, MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
