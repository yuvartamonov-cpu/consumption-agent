"""Memory Lane Telegram handlers."""

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


def _format_top_link_button(group: dict[str, Any], index: int) -> InlineKeyboardButton | None:
    """Build a compact URL button for a top search result."""
    url = (group.get('url') or '').strip()
    if not url:
        return None
    store = (group.get('store') or group.get('source') or 'Ссылка').strip()
    price = group.get('price_min')
    price_text = ''
    if isinstance(price, (int, float)) and price > 0:
        price_text = f" · {int(price):,} ₽".replace(',', ' ')
    text = f"🔗 {index}. {store[:20]}{price_text}"
    return InlineKeyboardButton(text[:64], url=url)


def _build_ml_search_keyboard(
    result: dict[str, Any],
    ml_id: int,
    *,
    remaining_pages: int = 0,
) -> InlineKeyboardMarkup | None:
    """Compose inline keyboard for /ml_search results."""
    groups = result.get('canonical_groups', []) or []
    buttons: list[list[InlineKeyboardButton]] = []

    top_buttons: list[InlineKeyboardButton] = []
    for idx, group in enumerate(groups[:3], start=1):
        button = _format_top_link_button(group, idx)
        if button is not None:
            top_buttons.append(button)
    for button in top_buttons:
        buttons.append([button])

    if remaining_pages > 0:
        buttons.append([InlineKeyboardButton(
            f'📄 Продолжить вывод ({remaining_pages} ещё)',
            callback_data=f'ml_page:{ml_id}:1'
        )])

    has_priced = any(g.get('price_min') for g in groups[:3])
    if has_priced:
        buttons.append([InlineKeyboardButton(
            '🔔 Следить за ценой (топ-3)',
            callback_data=f'ml_watch:{ml_id}'
        )])

    return InlineKeyboardMarkup(buttons) if buttons else None


def configure(*, shared: dict[str, Any] | None = None, logger: Any | None = None, **_: Any) -> None:
    global log
    if shared:
        globals().update(shared)
    if logger is not None:
        log = logger


async def cmd_topic_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Установить тему для ключевого слова: /topic_set <слово> <тема>"""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
        return

    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text('Использование: /topic_set <слово> <тема>\nНапример: /topic_set кофемолка бытовая техника')
        return

    keyword = ctx.args[0].lower()
    topic = ' '.join(ctx.args[1:]).lower()

    conn = get_db()
    try:
        is_new = _ml.set_topic_rule(conn, keyword, topic)
        conn.commit()
    except Exception as e:
        await update.message.reply_text(f'\u274c Ошибка: {e}')
        return
    finally:
        conn.close()

    if is_new:
        await update.message.reply_text(f'\u2705 Добавлено правило: «{keyword}» \u2192 «{topic}»')
    else:
        await update.message.reply_text(f'\u2705 Обновлено правило: «{keyword}» \u2192 «{topic}»')

async def cmd_topic_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать все правила тем: /topic_list [тема]"""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
        return

    topic_filter = ' '.join(ctx.args).lower() if ctx.args else None

    conn = get_db()
    try:
        rules = _ml.list_topic_rules(conn, topic_filter)
    finally:
        conn.close()

    if not rules:
        if topic_filter:
            await update.message.reply_text(f'Правил для темы «{topic_filter}» не найдено.')
        else:
            await update.message.reply_text('Правил пока нет. Добавьте /topic_set <слово> <тема>')
        return

    # Группируем по темам
    groups = {}
    for r in rules:
        t = r['topic']
        if t not in groups:
            groups[t] = []
        icon = '\U0001f3f7' if r['source'] == 'user' else ''
        groups[t].append(f"{icon}{r['keyword']} ({r['usage_count']})")

    lines = [f'\U0001f9f9 Правила тем ({len(rules)}):']
    for topic in sorted(groups.keys()):
        kws = ', '.join(groups[topic])
        lines.append(f'\n\U0001f539 {topic}: {kws}')

    # Разбиваем на части если длинно
    full = '\n'.join(lines)
    if len(full) > 4000:
        for chunk in [full[i:i+4000] for i in range(0, len(full), 4000)]:
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(full)

async def cmd_ml_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать последние N записей Memory Lane (по умолчанию 10)."""
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
        return

    n = 10
    if ctx.args:
        try:
            n = max(1, min(50, int(ctx.args[0])))
        except ValueError:
            await update.message.reply_text('Usage: /ml_last [N=10]')
            return

    conn = get_db()
    try:
        rows = _ml.list_recent(conn, n=n)
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text(
            'Memory Lane пуст. Отправь фото с подписью «нравится» или #хэштегом, '
            'чтобы добавить запись.'
        )
        return

    lines = [f'🧠 Последние {len(rows)} записей:']
    for r in rows:
        cap = (r['caption'] or '').strip().replace('\n', ' ')
        if len(cap) > 60:
            cap = cap[:57] + '…'
        try:
            tags = json.loads(r['style_tags'] or '[]')
        except (TypeError, ValueError):
            tags = []
        tag_str = ' '.join(f'#{t}' for t in tags) if tags else ''
        topic = r['topic'] or '—'
        date = (r['created_at'] or '')[:10]
        has_photo = '📷' if r['media_asset_id'] else ''
        name = r['name'] or ''
        desc = (r['description'] or '')[:40] if r['description'] else ''
        name_part = f' {name}' if name else ''
        desc_part = f' — {desc}…' if desc else ''
        lines.append(f'#{r["id"]:>3}{has_photo} {date} [{topic}]{name_part}{desc_part}'.rstrip())
    await update.message.reply_text('\n'.join(lines))

    # Отправляем фото для записей, у которых есть media_asset_id
    conn2 = get_db()
    for r in rows:
        media_asset_id = r['media_asset_id']
        if not media_asset_id:
            continue
        try:
            row = conn2.execute(
                'SELECT file_path FROM media_assets WHERE id = ?', (media_asset_id,)
            ).fetchone()
            if not row or not os.path.exists(row[0]):
                continue
            caption_lines = [f'🧠 ML #{r["id"]}']
            if r['name']:
                caption_lines.append(f'📌 {r["name"]}')
            if r['description']:
                caption_lines.append(r['description'])
            if r['caption']:
                cap = r['caption'].strip()
                if cap != (r['name'] or '') and not cap.startswith('#'):
                    caption_lines.append(cap)
            if r['topic']:
                caption_lines.append(f'📂 {r["topic"]}')
            caption_lines.append(f'🕒 {str(r["created_at"])[:10]}')
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton('🔍 Искать', callback_data=f'ml_search:{r["id"]}'),
                    InlineKeyboardButton('⏰ Напомнить', callback_data=f'ml_remind:{r["id"]}'),
                ],
                [
                    InlineKeyboardButton('🗑 Удалить', callback_data=f'ml_delete:{r["id"]}'),
                ]
            ])
            with open(row[0], 'rb') as fh:
                await update.message.reply_photo(
                    photo=fh.read(),
                    caption='\n'.join(caption_lines),
                    reply_markup=kb
                )
        except Exception as e:
            log.warning(f'ml_last: failed to send photo for ml_id={r["id"]}: {e}')
    conn2.close()

async def cmd_ml_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ml_search <id> — поиск товара из Memory Lane по новому pipeline.

    Использует ml_search_v2: attribute extraction → query expansion →
    canonicalization → anomaly flag → inventory collision → taste re-rank.
    """
    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return
    args = ctx.args if hasattr(ctx, 'args') else []
    if not args:
        await update.message.reply_text(
            'Usage: /ml_search <id>\nПодсказка: /ml_last покажет доступные id.'
        )
        return
    try:
        ml_id = int(args[0])
    except ValueError:
        await update.message.reply_text('⚠️ id должен быть числом')
        return

    # Определяем chat_id для ответа (работает и из команды, и из callback)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    temp = await ctx.bot.send_message(
        chat_id=chat_id,
        text=f'🔍 Запускаю pipeline v2 для #{ml_id}... 5–15 сек.'
    )
    try:
        import ml_search_v2
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        conn = get_db()
        try:
            result = await ml_search_v2.search_ml_item_v2(conn, ml_id)
        finally:
            conn.close()
        pages = ml_search_v2.format_search_pages(result)
        await ctx.bot.delete_message(chat_id=chat_id, message_id=temp.message_id)

        # Сохраняем результат в user_data для watchlist (нужен URL и цена)
        ctx.user_data[f'ml_result_{ml_id}'] = result

        if len(pages) > 1:
            page_key = f'ml_pages_{ml_id}'
            ctx.user_data[page_key] = pages[1:]
        reply_markup = _build_ml_search_keyboard(
            result,
            ml_id,
            remaining_pages=max(0, len(pages) - 1),
        )

        await ctx.bot.send_message(
            chat_id=chat_id,
            text=pages[0],
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception as e:
        log.warning(f'cmd_ml_search failed: {e}')
        await ctx.bot.edit_message_text(
            chat_id=chat_id,
            message_id=temp.message_id,
            text=f'⚠️ Ошибка: {str(e)[:200]}'
        )

async def cmd_ml_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ml_watch — показать активный watchlist."""
    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return
    import ml_watchlist as mw
    conn = get_db()
    try:
        mw.ensure_watchlist_schema(conn)
        rows = mw.list_watchlist(conn)
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text(
            '🔔 Watchlist пуст.\n'
            'Запустите /ml_search <id>, нажмите «Следить за ценой» в результатах.'
        )
        return

    lines = [f'🔔 <b>Watchlist:</b> {len(rows)} активных']
    for r in rows:
        title = html.escape((r.get('product_title') or '?')[:60])
        store = html.escape(r.get('store') or '?')
        ip = r.get('initial_price') or 0
        lp = r.get('last_price') or 0
        change = ''
        if ip and lp and lp != ip:
            pct = round((ip - lp) / ip * 100.0, 1)
            change = f'  ({pct:+.1f}%)'
        lines.append(f"<b>#{r['id']}</b> · {store} · {ip:,} ₽{change}".replace(',', ' '))
        lines.append(f'   {title}')
        if r.get('last_checked_at'):
            lines.append(f'   проверено: {r["last_checked_at"][:16]}')
    lines.append('')
    lines.append('Убрать: <code>/ml_unwatch &lt;id&gt;</code>')

    await update.message.reply_text(
        '\n'.join(lines),
        parse_mode='HTML',
        disable_web_page_preview=True,
    )

async def cmd_ml_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ml_unwatch <id> — убрать товар из watchlist."""
    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return
    args = ctx.args if hasattr(ctx, 'args') else []
    if not args:
        await update.message.reply_text('Usage: /ml_unwatch <watch_id>\n'
                                        'Посмотреть id: /ml_watch')
        return
    try:
        watch_id = int(args[0])
    except ValueError:
        await update.message.reply_text('⚠️ id должен быть числом')
        return

    import ml_watchlist as mw
    conn = get_db()
    try:
        ok = mw.remove_from_watchlist(conn, watch_id)
    finally:
        conn.close()

    if ok:
        await update.message.reply_text(f'✅ Watch #{watch_id} убран из watchlist')
    else:
        await update.message.reply_text(f'⚠️ Watch #{watch_id} не найден или уже убран')

async def cmd_ml_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ml_stats — CTR по источникам для активного обучения (Stage 9)."""
    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return
    try:
        import ml_bandit
        import ml_clicks
        conn = get_db()
        try:
            ml_clicks.ensure_clicks_schema(conn)
            stats = ml_clicks.ctr_per_source(conn, since_days=30)
            recent = ml_clicks.recent_events(conn, limit=8)
            # Refresh bandit from latest click data, then snapshot
            ml_bandit.update_from_clicks(conn, lookback_days=30)
            bandit_state = ml_bandit.snapshot(conn)
        finally:
            conn.close()

        if not stats and not bandit_state:
            await update.message.reply_text(
                '📊 ml_stats: пока нет данных.\n'
                'Используйте /ml_search <id> и кликайте по ссылкам — '
                'CTR и bandit появятся тут.'
            )
            return

        lines = ['📊 <b>ml_stats</b> (за 30 дней)\n']

        if stats:
            lines.append('<b>CTR по источникам:</b>')
            for src, s in sorted(stats.items(), key=lambda x: -x[1]['ctr']):
                ctr_pct = round(s['ctr'] * 100, 1)
                lines.append(
                    f"  • <b>{src}</b>: {s['clicks']}/{s['impressions']} "
                    f"({ctr_pct}%)"
                )

        if bandit_state:
            lines.append('\n<b>Bandit p̂ (категория → источник):</b>')
            # Show top 8 by p_mean
            for r in bandit_state[:8]:
                cat = r['category'] or '—'
                src = r['source']
                p = round(r['p_mean'] * 100, 1)
                lines.append(
                    f"  • {cat} → {src}: {p}% "
                    f"(α={r['alpha']:.1f} β={r['beta']:.1f})"
                )

        # Source matcher: tier × geo × CTR (learned source routing quality)
        try:
            import ml_source_matcher as _sm
            conn2 = get_db()
            try:
                sm_stats = _sm.source_stats(conn2, since_days=30)
            finally:
                conn2.close()
            if sm_stats:
                lines.append('\n<b>Source matcher (tier × geo):</b>')
                for s in sm_stats[:10]:
                    ctr_pct = round(s['ctr'] * 100, 1)
                    lines.append(
                        f"  • <b>{s['tier']}</b>/{s['geo']}: "
                        f"{s['clicks']}/{s['total']} ({ctr_pct}%)"
                    )
        except Exception as e:
            log.warning(f'ml_stats source_matcher block failed: {e}')

        if recent:
            lines.append('\n<b>Последние события:</b>')
            for e in recent[:5]:
                kind = '👀' if e['kind'] == 'impression' else '🖱'
                act = e.get('action') or ''
                src = e.get('source') or '—'
                lines.append(f"  {kind} #{e['item_id']} · {src} · {act} · {e['ts'][:16]}")
        await update.message.reply_text('\n'.join(lines), parse_mode='HTML')
    except Exception as e:
        log.warning(f'cmd_ml_stats failed: {e}')
        await update.message.reply_text(f'⚠️ Ошибка: {str(e)[:200]}')


def _parse_find_args(args: list[str]) -> tuple[str, dict[str, str]]:
    """Split CLI-style args into (query, {topic,brand,color}).

    Supports ``--topic X`` / ``--brand Y`` / ``--color Z`` flags anywhere;
    everything else becomes the free-text query.
    """
    flags: dict[str, str] = {}
    query_parts: list[str] = []
    i = 0
    known = {'--topic': 'topic', '--brand': 'brand', '--color': 'color'}
    while i < len(args):
        tok = args[i]
        if tok in known and i + 1 < len(args):
            flags[known[tok]] = args[i + 1]
            i += 2
            continue
        query_parts.append(tok)
        i += 1
    return ' '.join(query_parts).strip(), flags


async def cmd_ml_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ml_find <query> [--topic T] [--brand B] [--color C] — текстовый поиск."""
    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
        return

    args = ctx.args if hasattr(ctx, 'args') else []
    query, flags = _parse_find_args(args)
    if not query and not flags:
        await update.message.reply_text(
            'Usage: /ml_find <запрос> [--topic тема] [--brand бренд] [--color цвет]\n'
            'Например: /ml_find пальто --color чёрный'
        )
        return

    conn = get_db()
    try:
        rows = _ml.search_items(
            conn, query,
            topic=flags.get('topic'),
            brand=flags.get('brand'),
            color=flags.get('color'),
            limit=20,
        )
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text(f'🔍 Ничего не найдено по запросу «{query or "—"}».')
        return

    lines = [f'🔍 Найдено: {len(rows)}']
    buttons = []
    row_buf = []
    for r in rows:
        cap = (r['caption'] or '').strip().replace('\n', ' ')
        if len(cap) > 50:
            cap = cap[:47] + '…'
        topic = r['topic'] or '—'
        d = (r['created_at'] or '')[:10]
        name = r['name'] or ''
        name_part = f' {name}' if name else ''
        lines.append(f'#{r["id"]:>3} {d} [{topic}]{name_part} — {cap}'.rstrip())
        row_buf.append(InlineKeyboardButton(f'🔍 #{r["id"]}', callback_data=f'ml_search:{r["id"]}'))
        if len(row_buf) == 3:
            buttons.append(row_buf)
            row_buf = []
    if row_buf:
        buttons.append(row_buf)

    text = '\n'.join(lines)
    if len(text) > 4000:
        text = text[:3997] + '…'
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def cmd_ml_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ml_profile [тема] — профиль вкуса по агрегации Memory Lane (без LLM)."""
    if update.effective_chat and update.effective_chat.id not in ALLOWED_CHAT_IDS:
        return
    try:
        import memory_lane as _ml
    except ImportError:
        await update.message.reply_text('Memory Lane модуль не найден.')
        return

    topic = ' '.join(ctx.args).strip().lower() if ctx.args else None

    conn = get_db()
    try:
        profile = _ml.build_profile(conn, topic)
    finally:
        conn.close()

    if not profile['count']:
        if topic:
            await update.message.reply_text(f'🧠 По теме «{topic}» пока нет записей.')
        else:
            await update.message.reply_text('🧠 Memory Lane пуст — профиль вкуса недоступен.')
        return

    header = f'🧠 Профиль вкуса{" · " + topic if topic else ""} ({profile["count"]} записей)'
    lines = [header]

    def _fmt(title: str, pairs: list) -> None:
        if pairs:
            body = ', '.join(f'{name} ({cnt})' for name, cnt in pairs)
            lines.append(f'\n{title}: {body}')

    _fmt('👍 Нравится', profile['liked'])
    _fmt('👎 Не нравится', profile['disliked'])
    _fmt('🏷️ Бренды', profile['brands'])
    _fmt('🎨 Цвета', profile['colors'])
    _fmt('🧵 Материалы', profile['materials'])
    _fmt('🏆 Стиль-теги', profile['style_tags'])

    if profile['examples']:
        lines.append('\n📋 Последние:')
        for ex in profile['examples']:
            name_part = f' {ex["name"]}' if ex['name'] else ''
            cap = ex['caption']
            if len(cap) > 40:
                cap = cap[:37] + '…'
            lines.append(f'  #{ex["id"]} {ex["created_at"]}{name_part} — {cap}'.rstrip())

    await update.message.reply_text('\n'.join(lines))


def register_handlers(app: Any, deps: Any = None) -> None:
    from bot.app import _add_command

    if deps is not None:
        configure(shared=getattr(deps, 'shared', None), logger=getattr(deps, 'log', None))
    for name, callback in (
        ('topic_set', cmd_topic_set),
        ('topic_list', cmd_topic_list),
        ('ml_last', cmd_ml_last),
        ('ml_find', cmd_ml_find),
        ('ml_profile', cmd_ml_profile),
        ('ml_search', cmd_ml_search),
        ('ml_stats', cmd_ml_stats),
        ('ml_watch', cmd_ml_watch),
        ('ml_unwatch', cmd_ml_unwatch),
    ):
        _add_command(app, deps, name, callback)
