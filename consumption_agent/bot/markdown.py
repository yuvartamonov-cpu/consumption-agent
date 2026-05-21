"""Markdown helpers for Telegram messages."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from telegram.error import NetworkError, TimedOut


log = logging.getLogger(__name__)
_RETRYABLE_TELEGRAM_ERRORS = (TimedOut, NetworkError)
_TELEGRAM_RETRY_ATTEMPTS = 2
_TELEGRAM_RETRY_DELAY_SECONDS = 1.0


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


def _is_markdown_parse_error(error: Exception) -> bool:
    return 'parse entities' in str(error).lower()


def _is_retryable_telegram_error(error: Exception) -> bool:
    return isinstance(error, _RETRYABLE_TELEGRAM_ERRORS) or 'timed out' in str(error).lower()


async def _call_with_retry(call, *, label: str):
    for attempt in range(_TELEGRAM_RETRY_ATTEMPTS + 1):
        try:
            return await call()
        except Exception as error:
            is_last_attempt = attempt >= _TELEGRAM_RETRY_ATTEMPTS
            if is_last_attempt or not _is_retryable_telegram_error(error):
                raise
            delay = _TELEGRAM_RETRY_DELAY_SECONDS * (attempt + 1)
            log.warning('%s failed (%s), retrying in %.1fs', label, error, delay)
            await asyncio.sleep(delay)


async def safe_edit_markdown_message(message, text: str):
    """Edit with Markdown first, then fallback to plain text on parse errors."""
    try:
        return await _call_with_retry(
            lambda: message.edit_text(text, parse_mode='Markdown'),
            label='telegram markdown edit',
        )
    except Exception as error:
        if not _is_markdown_parse_error(error):
            raise
        log.warning('fallback to plain text after Markdown edit failure: %s', error)
        return await _call_with_retry(
            lambda: message.edit_text(markdown_to_plain_text(text)),
            label='telegram plain-text edit',
        )


async def safe_send_markdown_message(bot, chat_id: int, text: str, *, reply_markup=None):
    """Send with Markdown first, then fallback to plain text on parse errors."""
    try:
        return await _call_with_retry(
            lambda: bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown',
                reply_markup=reply_markup,
            ),
            label='telegram markdown send',
        )
    except Exception as error:
        if not _is_markdown_parse_error(error):
            raise
        log.warning('fallback to plain text after Markdown send failure: %s', error)
        return await _call_with_retry(
            lambda: bot.send_message(
                chat_id=chat_id,
                text=markdown_to_plain_text(text),
                reply_markup=reply_markup,
            ),
            label='telegram plain-text send',
        )
