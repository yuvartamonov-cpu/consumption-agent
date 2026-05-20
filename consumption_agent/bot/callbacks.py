"""Telegram callback query handlers."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.memory_lane import cmd_ml_search


log = logging.getLogger(__name__)


def configure(*, shared: dict[str, Any] | None = None, logger: Any | None = None, **_: Any) -> None:
    global log
    if shared:
        globals().update(shared)
    if logger is not None:
        log = logger


async def credit_paid_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('credit_paid:'):
        return

    try:
        alert_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный alert id', show_alert=True)
        return

    row = get_credit_alert(alert_id)
    if not row:
        await query.answer('⚠️ Алерт не найден', show_alert=True)
        return

    if row['paid_confirmed_at']:
        await query.answer('✅ Уже отмечено как оплачено')
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    changed = confirm_credit_alert_paid(alert_id)
    if not changed:
        await query.answer('⚠️ Не удалось обновить статус', show_alert=True)
        return

    paid_note = '\n\n✅ <b>Отмечено как оплачено вручную</b>'
    base_text = html.escape((query.message.text or '').rstrip())
    new_text = base_text + paid_note
    try:
        await query.edit_message_text(
            new_text,
            parse_mode='HTML',
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await query.answer('✅ Платёж отмечен как оплаченный')

async def fine_paid_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '✅ Оплачено' для штрафов."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('fine_paid:'):
        return

    try:
        fine_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный id', show_alert=True)
        return

    row = get_fine(fine_id)
    if not row:
        await query.answer('⚠️ Штраф не найден', show_alert=True)
        return

    if row['paid_confirmed_at']:
        await query.answer('✅ Уже отмечено как оплачено')
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    changed = confirm_fine_paid(fine_id)
    if not changed:
        await query.answer('⚠️ Не удалось обновить статус', show_alert=True)
        return

    paid_note = '\n\n✅ <b>Отмечено как оплачено</b>'
    base_text = html.escape((query.message.text or '').rstrip())
    new_text = base_text + paid_note
    try:
        await query.edit_message_text(
            new_text,
            parse_mode='HTML',
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    await query.answer('✅ Штраф отмечен как оплаченный')

async def item_replaced_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '✅ Заменено' для напоминаний о замене вещей."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_replaced:'):
        return

    try:
        alert_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный alert id', show_alert=True)
        return

    conn = get_db()
    try:
        # Получаем alert и связанный item
        alert = conn.execute(
            'SELECT item_id FROM alerts WHERE id = ? AND alert_type = ?',
            (alert_id, 'replace_reminder')
        ).fetchone()
        if not alert:
            await query.answer('⚠️ Алерт не найден', show_alert=True)
            return

        item_id = alert['item_id']

        # Помечаем item как заменённый
        mark_item_replaced(conn, item_id)
        # Закрываем алерт
        conn.execute(
            "UPDATE alerts SET status = 'actioned' WHERE id = ?",
            (alert_id,)
        )
        conn.commit()

        # Обновляем сообщение
        replaced_note = '\n\n✅ <b>Заменено</b>'
        base_text = html.escape((query.message.text or '').rstrip())
        new_text = base_text + replaced_note
        try:
            await query.edit_message_text(
                new_text,
                parse_mode='HTML',
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        await query.answer('✅ Отмечено как заменённое')
    except Exception as e:
        log.warning(f'item_replaced_callback failed: {e}')
        await query.answer('⚠️ Ошибка при обновлении', show_alert=True)
    finally:
        conn.close()

async def item_delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '🗑 Удалить' для удаления item из инвентаря."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_delete:'):
        return

    try:
        item_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный item id', show_alert=True)
        return

    conn = get_db()
    try:
        # Soft delete — помечаем deleted_at
        soft_delete_item(conn, item_id)
        conn.commit()

        # Обновляем сообщение
        deleted_note = '\n\n🗑 <b>Удалено из инвентаря</b>'
        base_text = html.escape((query.message.text or '').rstrip())
        new_text = base_text + deleted_note
        try:
            await query.edit_message_text(
                new_text,
                parse_mode='HTML',
                reply_markup=None,
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        await query.answer('🗑 Удалено')
    except Exception as e:
        log.warning(f'item_delete_callback failed: {e}')
        await query.answer('⚠️ Ошибка при удалении', show_alert=True)
    finally:
        conn.close()

async def ml_delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '🗑 Удалить' для Memory Lane записей в /ml_last."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('ml_delete:'):
        return

    try:
        ml_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный id', show_alert=True)
        return

    conn = get_db()
    try:
        # Получаем media_asset_id для удаления
        row = conn.execute(
            'SELECT media_asset_id FROM memory_lane_items WHERE id = ?', (ml_id,)
        ).fetchone()
        if not row:
            await query.answer('⚠️ Запись не найдена', show_alert=True)
            return

        media_asset_id = row[0]

        # Отключаем FK для безопасного удаления, восстанавливаем после
        conn.execute('PRAGMA foreign_keys=OFF')
        # Сначала удаляем зависимости (FOREIGN KEY)
        conn.execute('DELETE FROM ml_reminders WHERE item_id = ?', (ml_id,))
        conn.execute('DELETE FROM ml_watchlist WHERE item_id = ?', (ml_id,))
        # Удаляем запись из memory_lane_items
        conn.execute('DELETE FROM memory_lane_items WHERE id = ?', (ml_id,))
        conn.execute('PRAGMA foreign_keys=ON')

        # Удаляем связанный media_asset (файл + запись в БД)
        if media_asset_id:
            ma_row = conn.execute(
                'SELECT file_path FROM media_assets WHERE id = ?', (media_asset_id,)
            ).fetchone()
            conn.execute('DELETE FROM media_assets WHERE id = ?', (media_asset_id,))
            if ma_row and os.path.exists(ma_row[0]):
                try:
                    os.remove(ma_row[0])
                except Exception:
                    pass

        conn.commit()

        # Обновляем сообщение (убираем фото, меняем подпись)
        try:
            await query.edit_message_caption(
                caption=f'🗑 Запись #{ml_id} удалена',
                reply_markup=None
            )
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

        await query.answer('🗑 Запись удалена')
    except Exception as e:
        log.warning(f'ml_delete_callback failed: {e}')
        await query.answer('⚠️ Ошибка при удалении', show_alert=True)
    finally:
        conn.close()

async def ml_search_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '🔍 Искать' для Memory Lane записей.

    Поиск выполняется асинхронно — результат отправляется в чат отдельным сообщением.
    """
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('ml_search:'):
        return

    try:
        ml_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный id', show_alert=True)
        return

    # Сразу отвечаем что ищем
    await query.answer('🔍 Ищу...')

    # Отправляем временное сообщение
    chat_id = update.effective_chat.id
    temp_msg = await ctx.bot.send_message(
        chat_id=chat_id,
        text=f'🔍 Ищу товар #{ml_id}... Это может занять 10-20 секунд.'
    )

    # Stage 9 active-learning: log this as an explicit user request
    try:
        import ml_clicks
        conn_log = get_db()
        try:
            ml_clicks.log_click(
                conn_log, item_id=ml_id,
                action='search_request', source='button_v1',
            )
        finally:
            conn_log.close()
    except Exception as _e:
        log.warning(f'ml_clicks log_click failed: {_e}')

    try:
        # Удаляем временное сообщение
        await temp_msg.delete()

        # Вызываем cmd_ml_search напрямую с правильными аргументами
        # Используем существующий update, но меняем ctx.args
        ctx.args = [str(ml_id)]
        await cmd_ml_search(update, ctx)
    except Exception as e:
        log.warning(f'ml_search_callback failed: {e}')
        await temp_msg.edit_text(f'⚠️ Ошибка поиска: {str(e)[:100]}')

async def ml_watch_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки «🔔 Следить за ценой» — добавляет топ-3 товара в watchlist."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return

    data = query.data or ''
    if not data.startswith('ml_watch:'):
        return
    try:
        ml_id = int(data.split(':', 1)[1])
    except ValueError:
        return

    result = (ctx.user_data or {}).get(f'ml_result_{ml_id}')
    if not result:
        await query.answer('⚠️ Результат поиска устарел, запустите /ml_search заново',
                           show_alert=True)
        return

    chat_id = update.effective_chat.id
    import ml_watchlist as mw
    conn = get_db()
    try:
        mw.ensure_watchlist_schema(conn)
        groups = result.get('canonical_groups', [])
        added = []
        for g in groups[:3]:
            url = g.get('url')
            price = g.get('price_min')
            if not url or not price:
                continue
            watch_id = mw.add_to_watchlist(
                conn,
                item_id=ml_id,
                product_url=url,
                product_title=(g.get('title') or g.get('name') or '')[:200],
                store=g.get('store') or '',
                source=g.get('source') or '',
                initial_price=int(price),
                chat_id=chat_id,
            )
            added.append((watch_id, g.get('store'), int(price)))
    finally:
        conn.close()

    if not added:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text='⚠️ В результатах нет товаров с ценой — нечего отслеживать.\n'
                 'Попробуйте на других категориях (одежда, техника).'
        )
        return

    lines = [f'🔔 <b>Добавлено в watchlist:</b> {len(added)} товаров']
    for wid, store, price in added:
        lines.append(f'  · {html.escape(store or "?")}: {price:,} ₽ (#{wid})'.replace(',', ' '))
    lines.append('')
    lines.append('Я проверю цены завтра в 10:00 и сообщу, если упадут на 10%+.')
    lines.append('Команды: /ml_watch — список, /ml_unwatch &lt;id&gt; — убрать.')

    await ctx.bot.send_message(
        chat_id=chat_id,
        text='\n'.join(lines),
        parse_mode='HTML',
        disable_web_page_preview=True,
    )

async def ml_unwatch_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Callback кнопки «Больше не следить» в уведомлении о падении."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ''
    if not data.startswith('ml_unwatch:'):
        return
    try:
        watch_id = int(data.split(':', 1)[1])
    except ValueError:
        return
    import ml_watchlist as mw
    conn = get_db()
    try:
        mw.remove_from_watchlist(conn, watch_id)
    finally:
        conn.close()
    await query.answer(f'❌ Watch #{watch_id} убран', show_alert=False)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

async def ml_page_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Продолжить вывод' — пагинация результатов /ml_search."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    # Формат: ml_page:<item_id>:<page_index>
    parts = data.split(':')
    if len(parts) != 3:
        return
    try:
        ml_id = int(parts[1])
        page_idx = int(parts[2])
    except ValueError:
        return

    page_key = f'ml_pages_{ml_id}'
    pages = (ctx.user_data or {}).get(page_key, [])

    if page_idx - 1 >= len(pages) or page_idx < 1:
        await query.answer('⚠️ Страницы закончились', show_alert=True)
        return

    page_text = pages[page_idx - 1]
    chat_id = update.effective_chat.id

    # Кнопка для следующей страницы (если есть ещё)
    reply_markup = None
    remaining = len(pages) - page_idx
    if remaining > 0:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f'📄 Продолжить вывод ({remaining} ещё)',
                callback_data=f'ml_page:{ml_id}:{page_idx + 1}'
            )
        ]])

    try:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=page_text,
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception as e:
        log.warning(f'ml_page_callback failed: {e}')
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f'⚠️ Ошибка вывода: {str(e)[:200]}'
        )

async def ml_remind_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '⏰ Напомнить' — предлагает выбрать когда напомнить."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('ml_remind:'):
        return

    try:
        ml_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный id', show_alert=True)
        return

    # Сохраняем ID товара в контексте
    ctx.user_data['ml_remind_id'] = ml_id

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📅 Через 7 дней', callback_data='ml_remind_set:7'),
            InlineKeyboardButton('📅 Через 30 дней', callback_data='ml_remind_set:30'),
        ],
        [
            InlineKeyboardButton('📅 Через 3 месяца', callback_data='ml_remind_set:90'),
            InlineKeyboardButton('📅 Через 6 месяцев', callback_data='ml_remind_set:180'),
        ],
        [
            InlineKeyboardButton('❌ Не напоминать', callback_data='ml_remind_set:0'),
        ]
    ])

    await query.message.reply_text(
        '⏰ Когда напомнить об этом товаре?',
        reply_markup=kb
    )

async def ml_remind_set_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора срока напоминания."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    data = query.data or ''
    if not data.startswith('ml_remind_set:'):
        return

    try:
        days = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Ошибка', show_alert=True)
        return

    ml_id = ctx.user_data.get('ml_remind_id')
    if not ml_id:
        await query.answer('⚠️ Товар не найден', show_alert=True)
        return

    if days == 0:
        await query.edit_message_text('❌ Напоминание отменено')
        return

    try:
        from ml_search import set_reminder
        if set_reminder(ml_id, days=days):
            await query.edit_message_text(f'✅ Напомню через {days} дней')
        else:
            await query.answer('⚠️ Ошибка сохранения', show_alert=True)
    except Exception as e:
        log.warning(f'ml_remind_set_callback failed: {e}')
        await query.answer('⚠️ Ошибка', show_alert=True)

async def vision_confirm_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '✅ Подтвердить' — товар уже в БД, просим доп. информацию."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    pending = ctx.user_data.get('vision_pending')
    if not pending:
        await query.answer('⚠️ Данные не найдены', show_alert=True)
        return

    # Убираем кнопки и обновляем сообщение
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await query.edit_message_text(
        query.message.text + '\n\n✅ Добавлено в инвентарь',
        reply_markup=None
    )
    await query.answer('✅ Сохранено')

    # Запрашиваем дополнительную информацию через ForceReply
    from telegram import ForceReply
    await ctx.bot.send_message(
        chat_id=update.effective_chat.id,
        text='📝 Введите дополнительную информацию о товаре (бренд, размер, материал):',
        reply_markup=ForceReply(selective=True),
        reply_to_message_id=query.message.message_id
    )
    # Сохраняем item_id для обработки ответа
    ctx.user_data['vision_awaiting_notes'] = pending.get('item_id')

async def vision_reject_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '❌ Отклонить' — удаляет товар из БД и фото."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    pending = ctx.user_data.pop('vision_pending', None)
    if not pending:
        await query.answer('⚠️ Данные не найдены', show_alert=True)
        return

    item_id = pending.get('item_id')
    asset_id = pending.get('asset_id')
    receipt_path = pending.get('receipt_path')

    conn = get_db()
    try:
        # Удаляем фото из media_assets
        if asset_id:
            delete_media_asset(conn, asset_id)

        # Удаляем связь item_photos
        if item_id:
            unlink_item_photos(conn, item_id)
            soft_delete_item(conn, item_id)

        conn.commit()

        # Удаляем временный файл
        if receipt_path and os.path.exists(receipt_path):
            try:
                os.remove(receipt_path)
            except Exception:
                pass

        # Убираем кнопки и обновляем сообщение
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        await query.edit_message_text(
            query.message.text + '\n\n❌ Отклонено и удалено',
            reply_markup=None
        )
        await query.answer('❌ Удалено')
    except Exception as e:
        log.warning(f'vision_reject_callback failed: {e}')
        await query.answer('⚠️ Ошибка при удалении', show_alert=True)
    finally:
        conn.close()

async def dedup_delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '🗑 Удалить дубли' — помечает дубли как deleted."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('dedup_delete:'):
        return

    ids_str = data.split(':', 1)[1]
    try:
        ids = [int(x) for x in ids_str.split(',') if x.strip().isdigit()]
    except ValueError:
        await query.answer('⚠️ Некорректные id', show_alert=True)
        return

    if not ids:
        return

    conn = get_db()
    try:
        from purchase_dedup import build_duplicate_hidden_note

        for pid in ids:
            conn.execute(
                '''
                UPDATE purchases
                SET deleted_at = datetime("now"),
                    notes = ?
                WHERE id = ? AND deleted_at IS NULL
                ''',
                (
                    build_duplicate_hidden_note(
                        conn.execute('SELECT notes FROM purchases WHERE id = ?', (pid,)).fetchone()[0]
                    ),
                    pid,
                ),
            )
        conn.commit()
        log.info(f'dedup_delete: deleted {len(ids)} purchases: {ids}')
        await query.edit_message_text(
            query.message.text + '\n\n🗑 Помечены как дубли',
            reply_markup=None,
            parse_mode='Markdown',
        )
        await query.answer(f'✅ {len(ids)} записей помечены дублями')
    except Exception as e:
        log.warning(f'dedup_delete_callback failed: {e}')
        await query.answer('⚠️ Ошибка', show_alert=True)
    finally:
        conn.close()

async def dedup_keep_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '✅ Оставить' — убирает кнопки, оставляет записи как есть."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    try:
        await query.edit_message_text(
            query.message.text + '\n\n✅ Оставлено',
            reply_markup=None,
            parse_mode='Markdown',
        )
        await query.answer('✅ Записи оставлены')
    except Exception as e:
        log.warning(f'dedup_keep_callback failed: {e}')
        await query.answer('⚠️ Ошибка', show_alert=True)

async def item_photo_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки '📷 Фото' — отправляет фото товара."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        await query.answer('❌ Доступ запрещён', show_alert=True)
        return

    data = query.data or ''
    if not data.startswith('item_photo:'):
        return

    try:
        item_id = int(data.split(':', 1)[1])
    except ValueError:
        await query.answer('⚠️ Некорректный item id', show_alert=True)
        return

    # Загружаем фото из БД
    conn = get_db()
    try:
        photo_path = get_item_photo_path(conn, item_id)
        if not photo_path:
            await query.answer('📭 Фото не найдено', show_alert=True)
            return

        if not os.path.exists(photo_path):
            await query.answer('📭 Фото не найдено', show_alert=True)
            return

        # Отправляем фото ответом на сообщение
        with open(photo_path, 'rb') as f:
            await ctx.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=f.read(),
                caption=f'📷 Фото товара ID: {item_id}'
            )
        await query.answer('📷 Фото отправлено')
    except Exception as e:
        log.warning(f'item_photo_callback failed: {e}')
        await query.answer('⚠️ Ошибка при отправке фото', show_alert=True)
    finally:
        conn.close()


def register_handlers(app: Any, deps: Any = None) -> None:
    from telegram.ext import CallbackQueryHandler

    if deps is not None:
        configure(shared=getattr(deps, 'shared', None), logger=getattr(deps, 'log', None))

    add = deps.add_authorized_handler
    add(app, CallbackQueryHandler(credit_paid_callback, pattern=r'^credit_paid:\d+$'))
    add(app, CallbackQueryHandler(fine_paid_callback, pattern=r'^fine_paid:\d+$'))
    add(app, CallbackQueryHandler(item_replaced_callback, pattern=r'^item_replaced:\d+$'))
    add(app, CallbackQueryHandler(item_delete_callback, pattern=r'^item_delete:\d+$'))
    add(app, CallbackQueryHandler(item_photo_callback, pattern=r'^item_photo:\d+$'))
    add(app, CallbackQueryHandler(ml_delete_callback, pattern=r'^ml_delete:\d+$'))
    add(app, CallbackQueryHandler(ml_search_callback, pattern=r'^ml_search:\d+$'))
    add(app, CallbackQueryHandler(ml_page_callback, pattern=r'^ml_page:\d+:\d+$'))
    add(app, CallbackQueryHandler(ml_watch_callback, pattern=r'^ml_watch:\d+$'))
    add(app, CallbackQueryHandler(ml_unwatch_callback, pattern=r'^ml_unwatch:\d+$'))
    add(app, CallbackQueryHandler(ml_remind_callback, pattern=r'^ml_remind:\d+$'))
    add(app, CallbackQueryHandler(ml_remind_set_callback, pattern=r'^ml_remind_set:\d+$'))
    add(app, CallbackQueryHandler(vision_confirm_callback, pattern=r'^vision_confirm$'))
    add(app, CallbackQueryHandler(vision_reject_callback, pattern=r'^vision_reject$'))
    
    # AI Categorization 
    from bot.handlers.items_add import handle_addcat_callback
    add(app, CallbackQueryHandler(lambda u, c: handle_addcat_callback(u, c, deps.get_db), pattern=r'^addcat_'))

    add(app, CallbackQueryHandler(dedup_delete_callback, pattern=r'^dedup_delete:'))
    add(app, CallbackQueryHandler(dedup_keep_callback, pattern=r'^dedup_keep:'))
