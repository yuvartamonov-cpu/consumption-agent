import asyncio
import json
import logging
import os
from datetime import date
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from repositories.items import insert_manual_item, update_item_vision_metadata, get_category_id
from repositories.media import save_media_asset, link_item_photo
from bot.ai_categorizer import suggest_category

log = logging.getLogger(__name__)

async def start_interactive_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE, item_data: dict, get_db):
    msg = await update.message.reply_text(f"🔍 Анализирую товар: {item_data['name']}...")
    
    # 1. Если есть фото, обогащаем через Vision СНАЧАЛА
    photos = item_data.get('photos', [])
    vision_enriched = {}
    file_bytes = None
    if photos:
        best = photos[-1]
        try:
            file = await best.get_file()
            file_bytes = await file.download_as_bytearray()
            
            # Запускаем Vision
            tmp_path = os.path.join(os.path.dirname(__file__), '..', '..', 'receipts', f'_tmp_vision_{update.message.message_id}.jpg')
            with open(tmp_path, 'wb') as fh:
                fh.write(file_bytes)
                
            from vision_item import recognize_item
            vision_enriched = await asyncio.to_thread(recognize_item, tmp_path)
            try:
                os.remove(tmp_path)
            except Exception:
                pass
                
            if vision_enriched and 'error' not in vision_enriched:
                item_data['vision'] = vision_enriched
                if not item_data['brand'] and vision_enriched.get('brand'):
                    item_data['brand'] = vision_enriched['brand']
        except Exception as e:
            log.warning(f"Vision failed in interactive add: {e}")

    item_data['file_bytes'] = file_bytes
    
    # 2. Запрашиваем AI для категории
    await _ask_category_suggestion(update, ctx, item_data, msg, get_db)

async def _ask_category_suggestion(update: Update, ctx: ContextTypes.DEFAULT_TYPE, item_data: dict, msg, get_db):
    conn = get_db()
    try:
        # Учитываем vision данные для лучшей классификации
        prompt_name = item_data['name']
        if item_data.get('brand'):
            prompt_name += f" (Бренд: {item_data['brand']})"
        if item_data.get('vision') and item_data['vision'].get('description'):
            prompt_name += f" - Описание: {item_data['vision']['description']}"
            
        suggestion = await asyncio.to_thread(
            suggest_category, conn, prompt_name, item_data.get('rejected_cats', [])
        )
    finally:
        conn.close()

    confidence = suggestion.get('confidence', 0)
    attempts = item_data.get('attempts', 0) + 1
    item_data['attempts'] = attempts
    item_data['current_suggestion'] = suggestion
    
    # Store state
    ctx.user_data['pending_add_item'] = item_data

    # Show inline keyboard
    kb = []
    if suggestion.get('action') == 'new':
        cat_name = suggestion.get('new_category_name', 'Новая категория')
        text = f"🤖 Предлагаю создать НОВУЮ категорию:\n📁 *{cat_name}*\n(Уверенность: {confidence}%)"
    else:
        # Find category name
        conn = get_db()
        row = conn.execute("SELECT name FROM categories WHERE id=?", (suggestion['category_id'],)).fetchone()
        conn.close()
        cat_name = row[0] if row else "Прочее"
        text = f"🤖 Предлагаю категорию:\n📁 *{cat_name}*\n(Уверенность: {confidence}%)"

    kb.append([InlineKeyboardButton("✅ Подтвердить", callback_data="addcat_confirm")])
    if attempts < 3:
        kb.append([InlineKeyboardButton("❌ Другой вариант", callback_data="addcat_reject")])
    else:
        kb.append([InlineKeyboardButton("❌ В Прочее", callback_data="addcat_other")])

    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def handle_addcat_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE, get_db):
    query = update.callback_query
    await query.answer()
    
    item_data = ctx.user_data.get('pending_add_item')
    if not item_data:
        await query.edit_message_text("❌ Данные устарели.")
        return

    action = query.data
    suggestion = item_data['current_suggestion']
    
    if action == "addcat_confirm":
        cat_id = suggestion.get('category_id')
        if suggestion.get('action') == 'new':
            # Create new category
            conn = get_db()
            slug = suggestion.get('new_category_slug', 'new_cat').lower().replace(' ', '_')
            name = suggestion.get('new_category_name', 'Новая')
            # Check if exists
            row = conn.execute("SELECT id FROM categories WHERE slug=?", (slug,)).fetchone()
            if row:
                cat_id = row[0]
            else:
                conn.execute("INSERT INTO categories (slug, name) VALUES (?, ?)", (slug, name))
                conn.commit()
                row = conn.execute("SELECT id FROM categories WHERE slug=?", (slug,)).fetchone()
                cat_id = row[0]
            conn.close()
        
        await _save_item_final(update, ctx, item_data, cat_id, get_db, query.message)
        ctx.user_data.pop('pending_add_item', None)
        
    elif action == "addcat_reject":
        if suggestion.get('action') == 'existing':
            item_data.setdefault('rejected_cats', []).append(suggestion['category_id'])
        await query.edit_message_text("🔄 Ищу другой вариант...")
        await _ask_category_suggestion(update, ctx, item_data, query.message, get_db)
        
    elif action == "addcat_other":
        conn = get_db()
        row = conn.execute("SELECT id FROM categories WHERE slug='other'").fetchone()
        conn.close()
        cat_id = row[0] if row else None
        await _save_item_final(update, ctx, item_data, cat_id, get_db, query.message)
        ctx.user_data.pop('pending_add_item', None)

async def _save_item_final(update, ctx, item_data, cat_id, get_db, msg):
    conn = get_db()
    try:
        # 1. Insert item
        notes_parts = ['Добавлено через /add_item']
        if item_data.get('replace_days'):
            notes_parts.append(f"Ожидается замена через {item_data['replace_days']} дн.")
        elif item_data.get('replace_months'):
            notes_parts.append(f"Ожидается замена через {item_data['replace_months']} мес.")
        
        if item_data.get('vision'):
            v = item_data['vision']
            if v.get('color'): notes_parts.append(f"Цвет: {v['color']}")
            if v.get('material'): notes_parts.append(f"Материал: {v['material']}")
            if v.get('description'): notes_parts.append(f"Описание: {v['description']}")
            if v.get('estimated_price_rub'): notes_parts.append(f"Оценка: ~{v['estimated_price_rub']} ₽")
            
        notes = '\n'.join(notes_parts)
        
        item_id = insert_manual_item(
            conn,
            name=item_data['name'],
            brand=item_data['brand'],
            category_id=cat_id,
            replace_months=item_data.get('replace_months'),
            replace_days=item_data.get('replace_days'),
            notes=notes,
            data_origin=item_data.get('data_origin', 'manual'),
        )
        
        # update vision metadata if needed
        if item_data.get('vision') and not item_data.get('vision_metadata_saved'):
            v = item_data['vision']
            attrs = json.dumps({
                'color': v.get('color'),
                'description': v.get('description'),
                'style_tags': v.get('style_tags', []),
                'material': v.get('material'),
                'estimated_price_rub': v.get('estimated_price_rub'),
            }, ensure_ascii=False)
            update_item_vision_metadata(conn, item_id=item_id, brand=item_data['brand'], attributes=attrs, notes=notes)

        
        # 2. Save photo if any
        if item_data.get('file_bytes'):
            db_path = conn.execute("PRAGMA database_list").fetchall()[0][2]
            media_dir = os.path.join(os.path.dirname(db_path), 'data', 'media')
            asset_id = save_media_asset(conn, item_data['file_bytes'], mime='image/jpeg', base_dir=media_dir)
            if asset_id:
                link_item_photo(conn, item_id=item_id, media_asset_id=asset_id)
                
        conn.commit()
        
        # Fetch actual category name
        row = conn.execute("SELECT name FROM categories WHERE id=?", (cat_id,)).fetchone()
        cat_name = row[0] if row else "Прочее"
        
        from bot.markdown import esc_md
        lines = [f"✅ Добавлено: *{esc_md(item_data['name'])}*"]
        if item_data.get('brand'):
            lines.append(f"🏷 Бренд: {esc_md(item_data['brand'])}")
        lines.append(f"📂 Категория: {esc_md(cat_name)}")
        
        await msg.edit_text('\n'.join(lines), parse_mode='Markdown')
        
    except Exception as e:
        log.error(f"Failed to save final item: {e}")
        await msg.edit_text(f"❌ Ошибка при сохранении: {e}")
    finally:
        conn.close()
