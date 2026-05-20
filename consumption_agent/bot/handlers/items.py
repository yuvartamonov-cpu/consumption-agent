"""Item, inventory, warranty, and parser Telegram handlers."""

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


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        log.info("cmd_list: Начало выполнения")
        conn = get_db()
        log.info("cmd_list: БД подключена")
        total = conn.execute("SELECT COUNT(*) FROM items WHERE deleted_at IS NULL").fetchone()[0]
        log.info(f"cmd_list: Всего товаров = {total}")
        rows = conn.execute("""
            SELECT c.name, COUNT(i.id) as cnt, COALESCE(SUM(i.purchase_price), 0) as total_p
            FROM items i JOIN categories c ON i.category_id = c.id
            WHERE i.deleted_at IS NULL
            GROUP BY c.name ORDER BY cnt DESC
        """).fetchall()
        log.info(f"cmd_list: Получено категорий = {len(rows)}")
        conn.close()
        lines = [f'📦 Инвентарь: {total} товаров\n']
        for r in rows:
            lines.append(f'• {r["name"]}: {r["cnt"]} шт. ({r["total_p"]:.0f} ₽)')
        lines.append(f'\nВсего категорий: {len(rows)}')
        await update.message.reply_text('\n'.join(lines))
    except Exception as e:
        log.error(f"Ошибка в cmd_list: {e}")
        await update.message.reply_text(f'❌ Ошибка: {e}')

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = ' '.join(ctx.args)
    if not text:
        await update.message.reply_text('❌ Пример: /add Носки 350 одежда')
        return
    # Parse: name, optional price, optional category
    parts = text.rsplit(None, 2)  # try splitting from right
    name = text
    price = None
    category = None
    # Try category extraction (из consumption.categorize — Шаг 5)
    cats = {'еда':'cat_food','продукты':'cat_food','одежда':'cat_clo_everyday','обувь':'cat_clo_shoes',
            'техника':'cat_tech','книги':'cat_culture_books','спорт':'cat_sport','косметика':'cat_cosmetics',
            'здоровье':'cat_health_med','дом':'cat_home','авто':'cat_auto','животные':'cat_pets',
            'мебель':'cat_home_furn','аксесс':'cat_clo_access','хобби':'cat_hobbies',
            'интим':'cat_sexual','подписка':'cat_subscriptions'}
    for kw, cid in cats.items():
        if kw in text.lower():
            # Extract price before category
            m = re.search(r'(\d[\d\s]*\d)\s*(?:₽|руб|р)?', text)
            if m:
                price = float(m.group(1).replace(' ', ''))
                name = text[:m.start()].strip().rstrip(',').strip()
                try:
                    name = name.rsplit(None, 1)[0] if name.split()[-1].lower() == kw else name
                except: pass
            else:
                name = text.replace(kw, '').strip().strip(',').strip()
            category = cid
            break
    if not category:
        m = re.search(r'(\d[\d\s]*\d)\s*(?:₽|руб|р)?', text)
        if m:
            price = float(m.group(1).replace(' ', ''))
            name = text[:m.start()].strip().rstrip(',').strip()
    if not name or len(name) < 2:
        await update.message.reply_text('❌ Слишком короткое название')
        return
    conn = get_db()
    cat_id = None
    if category:
        row = conn.execute("SELECT id FROM categories WHERE id=? OR slug=? LIMIT 1", (category, category)).fetchone()
        if row: cat_id = row[0]
    # Автокатегоризация из consumption.categorize если пользователь не указал
    if cat_id is None:
        auto_cat = auto_categorize(name)
        if auto_cat:
            row = conn.execute("SELECT id FROM categories WHERE id=? LIMIT 1", (auto_cat,)).fetchone()
            if row: cat_id = row[0]
    if cat_id is None:
        row = conn.execute("SELECT id FROM categories WHERE slug='other' LIMIT 1").fetchone()
        if row: cat_id = row[0]
    insert_item(
        conn,
        name=name.strip(),
        purchase_price=price,
        purchase_date=date.today().isoformat(),
        category_id=cat_id,
        status='in_use',
        quantity=1,
        data_origin='telegram',
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f'✅ Добавлено: {name.strip()}{f" ({price:.0f} ₽)" if price else ""}')

async def cmd_parse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Парсинг необработанных чеков Ozon из почты."""
    await update.message.reply_text('🔄 Проверяю необработанные чеки Ozon...')

    limit = 10
    if ctx.args and ctx.args[0].isdigit():
        limit = min(int(ctx.args[0]), 50)

    try:
        conn = get_db()
        # Находим чеки без привязанных товаров
        rows = conn.execute("""
            SELECT cl.id, cl.cheque_date, cl.subject, cl.receipt_url, p.id as purchase_id
            FROM cheques_log cl
            LEFT JOIN purchases p ON p.email_message_id = CAST(cl.id AS TEXT)
            WHERE cl.source = 'ozon'
              AND cl.receipt_url IS NOT NULL
              AND cl.receipt_url != ''
              AND (p.id IS NULL OR NOT EXISTS (
                  SELECT 1 FROM items i WHERE i.purchase_id = p.id
              ))
            ORDER BY cl.id DESC
            LIMIT ?
        """, (limit,)).fetchall()

        if not rows:
            await update.message.reply_text('✅ Все чеки Ozon уже обработаны')
            conn.close()
            return

        lines = ['🔍 Найдено необработанных:', '']
        for r in rows:
            date_str = r['cheque_date'][:10] if r['cheque_date'] else '?'
            url = (r['receipt_url'] or '')[:60]
            status = '✅ привязан' if r['purchase_id'] else '❌ без покупки'
            lines.append(f'  • {date_str} — {status}')

        lines.append('')
        lines.append('Для обработки нужны свежие куки Ozon.')
        lines.append('Обновите куки в .ozon_cookies.txt или используйте /add_tag')

        await update.message.reply_text('\n'.join(lines))
        conn.close()
    except Exception as e:
        log.warning(f'cmd_parse error: {e}')
        await update.message.reply_text(f'❌ Ошибка: {e}')

async def cmd_warranties(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /warranties — отчёт по гарантиям."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from warranty_check import get_warranties_report, update_warranty_until, check_warranties, save_alerts
        conn = get_db()
        # Пересчёт warranty_until
        update_warranty_until(conn)
        # Проверка и сохранение алертов
        alerts = check_warranties(conn)
        if alerts:
            save_alerts(conn, alerts)
        # Отчёт
        report = get_warranties_report(conn)
        conn.close()
        await update.message.reply_text(report, parse_mode='Markdown')
    except Exception as e:
        log.error(f'cmd_warranties error: {e}')
        await update.message.reply_text(f'❌ Ошибка: {e}')

async def cmd_add_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /add_item — добавить вещь в инвентарь с фото, брендом и сроком замены.
    Формат:
      /add_item Название
      /add_item Название | бренд Бренд | замена 60 мес
      /add_item Название | бренд Бренд | замена 5 лет
    Можно прикрепить фото к сообщению."""
    text = ' '.join(ctx.args).strip()
    if not text:
        await update.message.reply_text(
            '❌ Укажите название вещи\n\n'
            'Пример:\n'
            '/add_item Стремянка 5 ступеней\n'
            '/add_item Пылесос | бренд Xiaomi | замена 60 мес\n'
            '/add_item Носки | бренд Nike | замена 12 мес\n\n'
            'Можно прикрепить фото к сообщению.'
        )
        return

    # Парсим поля через универсальный brand_parser
    from brand_parser import parse_brand_and_name
    bp = parse_brand_and_name(text)
    name = bp['name'] or text
    brand = bp['brand']
    replace_months = bp['replace_months']
    replace_days = bp.get('replace_days')

    # Нормализуем название — убираем лишнее
    name = name.strip().strip(',;')
    if not name:
        await update.message.reply_text('❌ Название не может быть пустым')
        return

    item_data = {
        'name': name,
        'brand': brand,
        'replace_months': replace_months,
        'replace_days': replace_days,
        'photos': []
    }
    
    if update.message and update.message.photo:
        item_data['photos'] = update.message.photo
    elif update.message and update.message.reply_to_message and update.message.reply_to_message.photo:
        item_data['photos'] = update.message.reply_to_message.photo
        log.info(f'add_item: using photo from reply_to_message {update.message.reply_to_message.message_id}')

    from bot.handlers.items_add import start_interactive_add
    
    # get_db is available in globals since it's injected
    await start_interactive_add(update, ctx, item_data, get_db)
    return

async def cmd_items(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /items — список вещей с группировкой и сроками замены.
    /items — все вещи, которым скоро нужна замена
    /items all — все вещи
    /items <категория> — вещи по категории"""
    conn = get_db()
    try:
        all_items = conn.execute('''
            SELECT i.id, i.name, i.brand, i.category_id, i.lifespan_months,
                   i.purchase_date, i.status, i.replace_after_months, i.replace_after_days, i.notes,
                   i.attributes,
                   COALESCE(c.name, i.category_id) AS category_name
            FROM items i
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL AND i.is_delivery = 0
              AND i.data_origin IN ('manual', 'local', 'telegram_photo', 'vision_photo', 'telegram_tag')
            ORDER BY i.category_id, i.name
        ''').fetchall()
    finally:
        conn.close()

    if not all_items:
        await update.message.reply_text('📭 В инвентаре пока нет вещей. Добавьте через /add_item')
        return

    today = datetime.now().date()

    # Фильтр
    args = ' '.join(ctx.args).lower() if ctx.args else ''
    if args and args != 'all':
        # Поиск по названию, бренду, категории, описанию, тегам
        filtered = []
        for r in all_items:
            name = (r[1] or '').lower()
            brand = (r[2] or '').lower()
            cat = (r[11] or r[3] or '').lower()  # category_name, fallback category_id
            notes = (r[9] or '').lower()
            attrs = {}
            try:
                attrs = json.loads(r[10] or '{}')  # attributes
            except (json.JSONDecodeError, IndexError):
                pass
            desc = (attrs.get('description') or '').lower()
            tags = ' '.join(attrs.get('style_tags', [])).lower()
            color = (attrs.get('color') or '').lower()
            material = (attrs.get('material') or '').lower()

            search_text = f'{name} {brand} {cat} {notes} {desc} {tags} {color} {material}'
            if args in search_text:
                filtered.append(r)
    elif args == 'all':
        filtered = all_items
    else:
        # По умолчанию: те, у кого есть replace_after_months/days или lifespan_months, и они истекают
        filtered = []
        for r in all_items:
            rep_days = r[8]  # replace_after_days (точное значение)
            rep_months = r[7] or r[4]  # replace_after_months, потом lifespan_months
            if not rep_months and not rep_days:
                continue
            purchase = r[5]
            if purchase:
                try:
                    pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                    if rep_days:
                        replace_date = pd + timedelta(days=rep_days)
                    else:
                        replace_date = add_months_safe(pd, rep_months)
                    days_left = (replace_date - today).days
                    if days_left <= 90:  # ближайшие 3 месяца
                        filtered.append((days_left, r))
                except (TypeError, ValueError) as e:
                    log.warning('Не удалось вычислить срок замены для item_id=%s: %s', r[0], e)
        filtered.sort(key=lambda x: x[0])
        filtered = [r[1] for r in filtered]
        if not filtered:
            # Если нет вещей к замене, показываем последние добавленные
            filtered = all_items[-10:]

    if not filtered:
        await update.message.reply_text('📭 Ничего не найдено по вашему запросу.')
        return

    # Группируем по категориям
    by_cat = {}
    for r in filtered:
        cat = r[3] or 'cat_other'
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(r)

    # Человеческие названия категорий
    cat_names = {
        'cat_clo_everyday': '👕 Повседневная одежда',
        'cat_clo_underwear': '👙 Нижнее бельё / носки',
        'cat_clo_shoes': '👟 Обувь',
        'cat_clo_access': '🧣 Аксессуары',
        'cat_tech': '💻 Техника',
        'cat_home': '🏠 Хозтовары',
        'cat_home_furn': '🪑 Мебель',
        'cat_home_kitchen': '🍳 Кухня',
        'cat_cosmetics': '🧴 Косметика',
        'cat_health_med': '💊 Здоровье',
        'cat_culture_books': '📚 Книги',
        'cat_hobbies': '🎮 Хобби',
        'cat_pets': '🐾 Животные',
        'cat_sport': '🏋️ Спорт',
        'cat_auto': '🚗 Авто',
        'cat_food': '🍎 Продукты',
        'cat_other': '📦 Прочее',
    }

    lines = ['📋 *Инвентарь:*']
    for cat, items in sorted(by_cat.items()):
        cat_label = cat_names.get(cat, f'📁 {cat}')
        lines.append(f'\n*{cat_label}:*')
        for r in items:
            name = r[1]
            brand = r[2]
            rep = r[7] or r[4]
            purchase = r[5]

            name_str = esc_md(name)
            if brand:
                name_str += f' ({esc_md(brand)})'

            # Срок замены
            if rep and purchase:
                try:
                    pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                    replace_date = add_months_safe(pd, rep)
                    days = (replace_date - today).days
                    if days <= 0:
                        suffix = ' 🔴 Пора менять!'
                    elif days <= 30:
                        suffix = f' 🟡 Через {days} дн.'
                    else:
                        suffix = ''
                except (TypeError, ValueError) as e:
                    log.warning('Не удалось показать срок замены для item_id=%s: %s', r[0], e)
                    suffix = ''
            else:
                suffix = ''

            lines.append(f'  • {name_str}{suffix}')

    lines.append(f'\nВсего: {len(filtered)} вещей')
    if not args or args == 'all':
        lines.append('\n/items all — показать всё')
        lines.append('/items <категория> — фильтр')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')

async def cmd_items_full(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /items_full — полный вывод с фото и всеми данными.
    /items_full all — все вещи с полной информацией
    /items_full — вещи с заменой <30 дней (с 🔴)"""
    log.info(f'cmd_items_full called by chat_id={update.effective_chat.id if update.effective_chat else None}, args={ctx.args}')
    conn = get_db()
    try:
        all_items = conn.execute('''
            SELECT i.id, i.name, i.brand, i.category_id, i.lifespan_months,
                   i.purchase_date, i.status, i.replace_after_months, i.replace_after_days, i.notes,
                   i.attributes,
                   COALESCE(c.name, i.category_id) AS category_name
            FROM items i
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL AND i.is_delivery = 0
              AND i.data_origin IN ('manual', 'local', 'vision_photo', 'telegram_photo', 'telegram_tag')
            ORDER BY i.category_id, i.name
        ''').fetchall()
        # Загружаем фото (file_path из media_assets)
        photos = {}
        for row in conn.execute('''
            SELECT ip.item_id, ma.file_path
            FROM item_photos ip
            JOIN media_assets ma ON ip.media_asset_id = ma.id
            WHERE ip.item_id IN (SELECT id FROM items WHERE deleted_at IS NULL)
        '''):
            photos[row[0]] = row[1]
    finally:
        conn.close()

    if not all_items:
        await update.message.reply_text('📭 В инвентаре пока нет вещей.')
        return

    today = datetime.now().date()
    args = ' '.join(ctx.args).lower() if ctx.args else ''

    if args == 'all':
        filtered = all_items
    elif args:
        # Фильтр по названию, бренду, описанию, категории
        filtered = []
        for r in all_items:
            name = (r[1] or '').lower()
            brand = (r[2] or '').lower()
            cat = (r[11] or r[3] or '').lower()  # category_name, fallback category_id
            notes = (r[9] or '').lower()
            attrs = {}
            try:
                attrs = json.loads(r[10] or '{}')  # attributes
            except json.JSONDecodeError:
                pass
            desc = (attrs.get('description') or '').lower()
            tags = ' '.join(attrs.get('style_tags', [])).lower()
            color = (attrs.get('color') or '').lower()
            material = (attrs.get('material') or '').lower()

            # Ищем во всех полях
            search_text = f'{name} {brand} {cat} {notes} {desc} {tags} {color} {material}'
            if args in search_text:
                filtered.append(r)
    else:
        # По умолчанию: только с заменой <30 дней (🔴)
        filtered = []
        for r in all_items:
            rep_days = r[8]  # replace_after_days
            rep_months = r[7] or r[4]
            if not rep_months and not rep_days:
                continue
            purchase = r[5]
            if purchase:
                try:
                    pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                    if rep_days:
                        replace_date = pd + timedelta(days=rep_days)
                    else:
                        replace_date = add_months_safe(pd, rep_months)
                    days_left = (replace_date - today).days
                    if days_left <= 30:
                        filtered.append(r)
                except (TypeError, ValueError):
                    continue

    if not filtered:
        await update.message.reply_text('📭 Ничего не найдено. Используй /items_full all или /items_full <название>')
        return

    cat_names = {
        'cat_clo_everyday': '👕 Повседневная одежда',
        'cat_clo_underwear': '👙 Нижнее бельё / носки',
        'cat_clo_shoes': '👟 Обувь',
        'cat_clo_access': '🧣 Аксессуары',
        'cat_tech': '💻 Техника',
        'cat_home': '🏠 Хозтовары',
        'cat_home_furn': '🪑 Мебель',
        'cat_home_kitchen': '🍳 Кухня',
        'cat_cosmetics': '🧴 Косметика',
        'cat_health_med': '💊 Здоровье',
        'cat_culture_books': '📚 Книги',
        'cat_hobbies': '🎮 Хобби',
        'cat_pets': '🐾 Животные',
        'cat_sport': '🏋️ Спорт',
        'cat_auto': '🚗 Авто',
        'cat_food': '🍎 Продукты',
        'cat_other': '📦 Прочее',
    }

    # Отправляем каждый item отдельным сообщением (с фото если есть)
    import asyncio
    for idx, r in enumerate(filtered):
        item_id = r[0]
        # Задержка между сообщениями чтобы избежать rate limit
        if idx > 0:
            await asyncio.sleep(0.5)
        name = r[1]
        brand = r[2]
        cat = r[3] or 'cat_other'
        rep_months = r[7] or r[4]
        rep_days = r[8]
        purchase = r[5]
        notes = r[9] or ''
        attrs = {}
        try:
            attrs = json.loads(r[10] or '{}')
        except json.JSONDecodeError:
            pass

        # Заголовок
        header = f'*{esc_md(name)}*'
        if brand:
            header += f' ({esc_md(brand)})'

        # Статус замены
        status_line = ''
        rep_days = r[8]
        rep_months = r[7] or r[4]
        if (rep_months or rep_days) and purchase:
            try:
                pd = datetime.strptime(purchase[:10], '%Y-%m-%d').date()
                if rep_days:
                    replace_date = pd + timedelta(days=rep_days)
                else:
                    replace_date = add_months_safe(pd, rep_months)
                days = (replace_date - today).days
                if days <= 0:
                    status_line = '🔴 *ПОРА МЕНЯТЬ!*'
                elif days <= 30:
                    status_line = f'🟡 Замена через *{days} дн.*'
                else:
                    status_line = f'🟢 Замена через {days} дн.'
            except (TypeError, ValueError):
                pass

        # Детали
        details = []
        cat_label = cat_names.get(cat, cat)
        details.append(f'📂 {cat_label}')
        if attrs.get('color'):
            details.append(f'🎨 Цвет: {attrs["color"]}')
        if attrs.get('material'):
            details.append(f'🧵 Материал: {attrs["material"]}')
        if attrs.get('description'):
            details.append(f'📝 {attrs["description"]}')
        if attrs.get('style_tags'):
            details.append(f'🏷️ Теги: {", ".join(attrs["style_tags"])}')
        if attrs.get('estimated_price_rub'):
            details.append(f'💰 Оценка: ~{attrs["estimated_price_rub"]} ₽')
        if notes:
            # Убираем служебные строки и Vision-данные (уже показаны в attributes)
            clean_notes = notes.replace('Добавлено через /add_item', '').strip()
            # Убираем строки с цветом, материалом, описанием, ценой (дубли из Vision)
            for prefix in ['Цвет:', 'Материал:', 'Описание:', 'Оценочная цена:']:
                clean_notes = '\n'.join(
                    line for line in clean_notes.split('\n')
                    if not line.strip().startswith(prefix)
                ).strip()
            if clean_notes:
                details.append(f'📋 {clean_notes[:200]}')

        text = f'{header}\n'
        if status_line:
            text += f'{status_line}\n'
        text += '\n'.join(details)
        text += f'\n\nID: `{item_id}`'

        # Формируем кнопки
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = []

        # Кнопка фото если есть
        photo_path = photos.get(item_id)
        has_photo = photo_path and os.path.exists(photo_path)

        if has_photo:
            buttons.append(InlineKeyboardButton('📷 Фото', callback_data=f'item_photo:{item_id}'))

        # Кнопка переноса в Memory Lane
        buttons.append(InlineKeyboardButton('📸 В Memory Lane', callback_data=f'item_to_ml:{item_id}'))

        # Кнопка удаления если замена <30 дней
        if status_line and ('🔴' in status_line or '🟡' in status_line):
            buttons.append(InlineKeyboardButton('🗑 Удалить', callback_data=f'item_delete:{item_id}'))

        kb = InlineKeyboardMarkup([buttons]) if buttons else None

        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)

async def cmd_set_warranty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) != 2:
        await update.message.reply_text("Usage: /set_warranty <item_id> <months>")
        return
    try:
        item_id = int(ctx.args[0])
        months = int(ctx.args[1])
        if item_id <= 0 or months <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Usage: /set_warranty <item_id> <months> (positive integers)")
        return

    conn = get_db()
    try:
        cur = conn.execute("UPDATE items SET warranty_months=? WHERE id=? AND deleted_at IS NULL", (months, item_id))
        if cur.rowcount == 0:
            await update.message.reply_text(f"❌ Item {item_id} not found")
            return
        # Reset warranty_until so update_warranty_until() recomputes it even
        # if it was already set (the helper skips non-NULL rows).
        conn.execute("UPDATE items SET warranty_until=NULL WHERE id=?", (item_id,))
        from warranty_check import update_warranty_until
        update_warranty_until(conn)
        row = conn.execute("SELECT warranty_until FROM items WHERE id=?", (item_id,)).fetchone()
        conn.commit()
        warranty_until = row["warranty_until"] if row and row["warranty_until"] else "N/A"
        await update.message.reply_text(
            f"OK: warranty_months={months}, warranty_until={warranty_until}"
        )
    finally:
        conn.close()

async def cmd_items_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /items_last [N] — список N последних добавленных вещей."""
    limit = 10
    if ctx.args and ctx.args[0].isdigit():
        limit = max(1, min(int(ctx.args[0]), 50))
        
    conn = get_db()
    try:
        rows = conn.execute('''
            SELECT i.id, i.name, i.brand, i.purchase_date, i.purchase_price,
                   COALESCE(c.name, i.category_id) AS category_name
            FROM items i
            LEFT JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL AND i.is_delivery = 0
            ORDER BY i.id DESC
            LIMIT ?
        ''', (limit,)).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text('📭 В инвентаре пока нет вещей.')
        return

    from bot.markdown import esc_md
    lines = [f'🆕 *Последние {len(rows)} добавленных вещей:*', '']
    for r in rows:
        item_id = r[0]
        name = r[1]
        brand = r[2]
        date_str = r[3][:10] if r[3] else '?'
        price = r[4]
        cat = r[5] or '?'
        
        name_str = esc_md(name) if name else '?'
        if brand:
            name_str += f' ({esc_md(brand)})'
            
        price_str = f', {price:.0f} ₽' if price is not None else ''
        lines.append(f'• `#{item_id}` *{name_str}* — {cat}{price_str} ({date_str})')

    await update.message.reply_text('\n'.join(lines), parse_mode='Markdown')

def register_handlers(app: Any, deps: Any = None) -> None:
    from bot.app import _add_command

    if deps is not None:
        configure(shared=getattr(deps, 'shared', None), logger=getattr(deps, 'log', None))
    for name, callback in (
        ('list', cmd_list),
        ('parse', cmd_parse),
        ('add', cmd_add),
        ('add_item', cmd_add_item),
        ('items', cmd_items),
        ('items_full', cmd_items_full),
        ('items_last', cmd_items_last),
        ('warranties', cmd_warranties),
        ('set_warranty', cmd_set_warranty),
    ):
        _add_command(app, deps, name, callback)
