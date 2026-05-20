"""Telegram application wiring helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable


@dataclass
class HandlerDeps:
    add_authorized_handler: Callable[[Any, Any], None]
    get_db: Callable[..., Any] | None = None
    docs_dir: str | Path | None = None
    log: Any | None = None
    shared: Mapping[str, Any] | None = None


def _add_command(app: Any, deps: HandlerDeps, name: str, callback: Callable[..., Any]) -> None:
    from telegram.ext import CommandHandler

    deps.add_authorized_handler(app, CommandHandler(name, callback))


def register_basic_handlers(app: Any, deps: HandlerDeps) -> None:
    from bot.handlers import finance, help as help_handlers

    help_handlers.register_handlers(app, deps)
    finance.register_handlers(app, deps)


def register_command_handlers(app: Any, deps: HandlerDeps) -> None:
    from bot.handlers import carsharing, finance, help as help_handlers, items, memory_lane, photos

    for module in (help_handlers, finance, carsharing, items, memory_lane, photos):
        module.register_handlers(app, deps)


def register_callback_handlers(app: Any, deps: HandlerDeps) -> None:
    from bot import callbacks

    callbacks.register_handlers(app, deps)
