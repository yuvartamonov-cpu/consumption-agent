import asyncio
import os
import sys

from telegram.error import TimedOut

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import bot.markdown as md


class _RetryMessage:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def edit_text(self, text, parse_mode=None):
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise TimedOut('simulated timeout')
        return {'text': text, 'parse_mode': parse_mode}


class _RetryBot:
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def send_message(self, *, chat_id, text, parse_mode=None, reply_markup=None):
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise TimedOut('simulated timeout')
        return {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': parse_mode,
            'reply_markup': reply_markup,
        }


def test_safe_edit_markdown_message_retries_on_timeout(monkeypatch):
    monkeypatch.setattr(md, '_TELEGRAM_RETRY_DELAY_SECONDS', 0)
    message = _RetryMessage(fail_times=2)

    result = asyncio.run(md.safe_edit_markdown_message(message, '*ok*'))

    assert message.calls == 3
    assert result['parse_mode'] == 'Markdown'


def test_safe_send_markdown_message_retries_on_timeout(monkeypatch):
    monkeypatch.setattr(md, '_TELEGRAM_RETRY_DELAY_SECONDS', 0)
    bot = _RetryBot(fail_times=2)

    result = asyncio.run(md.safe_send_markdown_message(bot, 123, '*ok*'))

    assert bot.calls == 3
    assert result['chat_id'] == 123
    assert result['parse_mode'] == 'Markdown'
