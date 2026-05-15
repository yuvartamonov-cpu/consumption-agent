"""Access control helpers for Telegram handlers."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any


log = logging.getLogger(__name__)


def parse_allowed_chat_ids(value: str | None) -> set[int]:
    ids: set[int] = set()
    if not value:
        return ids
    for part in re.split(r"[,;]", value):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            log.warning("Invalid Telegram chat id ignored: %r", part)
    return ids


def load_owner_chat_id(env: Mapping[str, str] | None = None) -> int:
    env = env or os.environ
    owner_default = env.get("OWNER_CHAT_ID_DEFAULT", "1477860192")
    return int(env.get("OWNER_CHAT_ID", owner_default))


def load_allowed_chat_ids(
    owner_chat_id: int,
    env: Mapping[str, str] | None = None,
) -> set[int]:
    env = env or os.environ
    allowed = parse_allowed_chat_ids(
        env.get("TELEGRAM_ALLOWED_CHAT_IDS") or env.get("ALLOWED_CHAT_IDS")
    )
    if not allowed and owner_chat_id:
        allowed = {owner_chat_id}
    return allowed


async def deny_access(update: Any) -> None:
    msg = getattr(update, "message", None)
    query = getattr(update, "callback_query", None)
    if msg is not None:
        await msg.reply_text("⛔ Доступ запрещён.")
    elif query is not None:
        await query.answer("Доступ запрещён", show_alert=True)


def register_authorized_handler(app: Any, handler: Any, allowed_chat_ids: set[int]) -> None:
    """Register a Telegram handler with a per-chat access guard."""
    original_callback = handler.callback

    async def wrapped_callback(update: Any, ctx: Any) -> Any:
        chat = getattr(update, "effective_chat", None)
        if chat is None or chat.id not in allowed_chat_ids:
            await deny_access(update)
            return None
        return await original_callback(update, ctx)

    handler.callback = wrapped_callback
    app.add_handler(handler)
