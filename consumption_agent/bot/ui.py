"""Small Telegram UI helpers shared by split handlers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def callback_data(prefix: str, *parts: Any) -> str:
    return ':'.join([prefix, *(str(part) for part in parts)])


def button(text: str, prefix: str, *parts: Any) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=callback_data(prefix, *parts))


def keyboard(rows: Sequence[Iterable[InlineKeyboardButton]] | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    materialized = []
    for row in rows:
        buttons = list(row)
        if buttons:
            materialized.append(buttons)
    return InlineKeyboardMarkup(materialized) if materialized else None
