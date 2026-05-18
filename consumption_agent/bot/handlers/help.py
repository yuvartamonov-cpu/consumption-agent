"""Basic help/start Telegram handlers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_docs_root: str | os.PathLike[str] | None = None


def configure(*, docs_dir: str | os.PathLike[str] | None = None) -> None:
    global _docs_root
    if docs_dir is not None:
        _docs_root = docs_dir


def _docs_dir() -> str:
    if _docs_root is not None:
        return str(_docs_root)
    return str(Path(__file__).resolve().parents[2] / 'docs')


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🛒 Привет, это Consumption Agent.\n'
        'Для списка команд: /help'
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Вывод справки: /help — список, /help <команда> — подробно."""
    if not ctx.args:
        await update.message.reply_text(
            '🛒 Consumption Agent\n\n'
            'Команды:\n'
            '/list — инвентарь по категориям\n'
            '/alerts — алерты (гарантии, сроки)\n'
            '/find_car 3ч 80км — подбор тарифа каршеринга\n'
            '/last_drives — последние поездки каршеринга (все провайдеры)\n'
            '/debts — проверка кредитов и займов по почтам + SMS\n'
            '/fines — неоплаченные штрафы\n'
            '/dayexp — расходы за сегодня с расшифровкой\n'
            '/monthexp — расходы за месяц с расшифровкой по дням\n'
            '/warranties — отчёт по гарантиям\n'
            '/add <название> [<цена>] [<категория>] — добавить товар\n'
            '/add_photo — загрузить фото чека (OCR)\n'
            '/check — статистика\n'
            '/add_item <название> [| бренд X] [| замена N мес] — добавить вещь в инвентарь\n'
            '/items [all|категория] — инвентарь вещей по категориям\n'
            '/items_full [all|категория] — с полной инфой (фото, атрибуты)\n'
            '/ml_last [N] — последние записи Memory Lane\n'
            '/ml_search <id> — найти товар\n'
            '/ml_stats — CTR по источникам (active learning)\n'
            '/ml_watch — watchlist цен\n'
            '/ml_unwatch <id> — убрать из watchlist\n'
            '/topic_set <слово> <тема> — задать тему для слова\n'
            '/topic_list [тема] — показать все правила тем\n'
            '/help — это сообщение\n\n'
            'Подробнее: /help <команда>\n'
            'Например: /help ml_search'
        )
        return

    cmd = ctx.args[0].lower().lstrip('/')
    import os
    help_path = os.path.join(_docs_dir(), 'bot_commands.md')
    if not os.path.exists(help_path):
        await update.message.reply_text('Файл справки не найден.')
        return

    # Простой парсер: ищем заголовок ### `/command`, собираем текст до следующего ### или ##
    found = []
    capture = False
    capture_depth = 0
    with open(help_path, encoding='utf-8') as f:
        for line in f:
            stripped = line.rstrip()
            if stripped.startswith('### `/'):
                c = stripped.replace('### ', '').replace('`', '')
                c_name = c.split()[0].lstrip('/').lower() if c.split() else ''
                if c_name == cmd:
                    capture = True
                    capture_depth = 0
                    continue
                elif capture:
                    break  # дошли до следующей команды
            if capture:
                if stripped.startswith('## '):
                    break
                if stripped.startswith('---'):
                    capture_depth += 1
                    if capture_depth >= 2:
                        break
                found.append(stripped)

    if not found:
        await update.message.reply_text(f'Описание для /{cmd} не найдено в bot_commands.md.')
        return

    text = f'📘 /{cmd}\n\n' + '\n'.join(found).strip()
    # Telegram 4096
    if len(text) > 4000:
        text = text[:3997] + '...'
    await update.message.reply_text(text)


def register_handlers(app: Any, deps: Any = None) -> None:
    from bot.app import _add_command

    if deps is not None:
        configure(docs_dir=getattr(deps, 'docs_dir', None))
    _add_command(app, deps, 'start', start)
    _add_command(app, deps, 'help', cmd_help)
