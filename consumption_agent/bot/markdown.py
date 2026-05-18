"""Markdown helpers for Telegram messages."""

from __future__ import annotations

import logging
import re
from typing import Any


log = logging.getLogger(__name__)


def esc_md(text: Any):
    """Escape Markdown V1 special characters for Telegram."""
    if not text:
        return text
    for ch in ('\\', '`', '*', '_', '[', ']', '(', ')'):
        text = str(text).replace(ch, '\\' + ch)
    return text


def markdown_to_plain_text(text: str | None) -> str:
    """Remove Markdown V1 markup for plain-text fallback sends."""
    if not text:
        return ''
    plain = re.sub(r'\\([\\`*_\[\]()])', r'\1', str(text))
    plain = re.sub(r'(?m)^_(.*)_$', r'\1', plain)
    return plain.replace('*', '').replace('`', '')


async def safe_edit_markdown_message(message, text: str):
    """Edit with Markdown first, then fallback to plain text on parse errors."""
    try:
        return await message.edit_text(text, parse_mode='Markdown')
    except Exception as e:
        if 'parse entities' not in str(e).lower():
            raise
        log.warning('fallback to plain text after Markdown edit failure: %s', e)
        return await message.edit_text(markdown_to_plain_text(text))


async def safe_send_markdown_message(bot, chat_id: int, text: str, *, reply_markup=None):
    """Send with Markdown first, then fallback to plain text on parse errors."""
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='Markdown',
            reply_markup=reply_markup,
        )
    except Exception as e:
        if 'parse entities' not in str(e).lower():
            raise
        log.warning('fallback to plain text after Markdown send failure: %s', e)
        return await bot.send_message(
            chat_id=chat_id,
            text=markdown_to_plain_text(text),
            reply_markup=reply_markup,
        )
