"""
Интеграционные mock-тесты для IMAP-сканирования.

Проверяют, что INBOX, Spam и Receipts реально участвуют в сканировании,
что дедупликация по Message-ID работает между папками,
и что ScanMetrics корректно считает folders/messages/deduped/parsed.
"""

import os
import sys
import email
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from imap_folders import (
    ScanMetrics,
    build_message_uid,
    discover_target_mailboxes,
    parse_list_mailbox,
    decode_imap_mailbox,
    normalize_mailbox_name,
)


# ─────────────────────────────────────────────────────────────
# Утилиты для создания mock IMAP
# ─────────────────────────────────────────────────────────────

FAKE_FOLDERS = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasNoChildren) "/" "[Gmail]/Spam"',
    b'(\\HasNoChildren) "/" "Receipts"',
    b'(\\HasNoChildren) "/" "Sent"',
    b'(\\HasNoChildren) "/" "Drafts"',
    b'(\\HasNoChildren) "/" "Trash"',
]

RUSSIAN_FOLDERS = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\Junk) "/" "&BCEEPwQwBDw-"',     # "Спам" в modified UTF-7
    b'(\\HasNoChildren) "/" "&BCcENQQ6BDg-"',  # "Чеки" в modified UTF-7
    b'(\\HasNoChildren) "/" "Sent"',
]


def _make_email_msg(subject: str, from_addr: str, body: str,
                    message_id: str = '') -> bytes:
    """Создаёт минимальное email-сообщение в bytes."""
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = from_addr
    msg['Date'] = 'Sat, 17 May 2026 10:00:00 +0300'
    if message_id:
        msg['Message-ID'] = message_id
    return msg.as_bytes()


def _build_mock_imap(folders: list[bytes],
                     mailbox_messages: dict[str, list[tuple[bytes, str, str, str, str]]]):
    """
    Строит mock IMAP-объект.

    mailbox_messages: {folder_name: [(uid, subject, from, body, message_id), ...]}
    """
    imap = MagicMock()
    imap.list.return_value = ('OK', folders)

    def select_side_effect(folder_quoted, readonly=True):
        name = folder_quoted.strip('"')
        if name in mailbox_messages or name == 'INBOX':
            return ('OK', [b'1'])
        return ('NO', [b'0'])

    imap.select.side_effect = select_side_effect

    def search_side_effect(*args):
        # Определяем текущую папку по последнему вызову select
        last_select = imap.select.call_args
        if not last_select:
            return ('OK', [b''])
        folder_name = last_select[0][0].strip('"')
        msgs = mailbox_messages.get(folder_name, [])
        if not msgs:
            return ('OK', [b''])
        ids = b' '.join(str(i + 1).encode() for i in range(len(msgs)))
        return ('OK', [ids])

    imap.search.side_effect = search_side_effect

    def fetch_side_effect(uid, fmt):
        last_select = imap.select.call_args
        folder_name = last_select[0][0].strip('"')
        msgs = mailbox_messages.get(folder_name, [])
        idx = int(uid if isinstance(uid, int) else uid) - 1
        if idx < 0 or idx >= len(msgs):
            return ('OK', [])
        uid_val, subj, from_addr, body, msg_id = msgs[idx]
        raw = _make_email_msg(subj, from_addr, body, msg_id)
        return ('OK', [(b'1 (RFC822 {%d}' % len(raw), raw)])

    imap.fetch.side_effect = fetch_side_effect

    return imap


# ─────────────────────────────────────────────────────────────
# Тесты: discover_target_mailboxes
# ─────────────────────────────────────────────────────────────

class TestDiscoverMailboxes:

    def test_inbox_spam_receipts_selected(self):
        """INBOX, Spam и Receipts должны быть выбраны."""
        imap = MagicMock()
        imap.list.return_value = ('OK', FAKE_FOLDERS)
        result = discover_target_mailboxes(imap, account_label='test')
        assert 'INBOX' in result
        assert '[Gmail]/Spam' in result
        assert 'Receipts' in result

    def test_sent_drafts_trash_excluded(self):
        """Sent, Drafts, Trash НЕ должны попасть в выборку."""
        imap = MagicMock()
        imap.list.return_value = ('OK', FAKE_FOLDERS)
        result = discover_target_mailboxes(imap, account_label='test')
        assert 'Sent' not in result
        assert 'Drafts' not in result
        assert 'Trash' not in result

    def test_list_failure_returns_inbox(self):
        """При ошибке LIST возвращаем хотя бы INBOX."""
        imap = MagicMock()
        imap.list.side_effect = Exception('network error')
        result = discover_target_mailboxes(imap, account_label='test')
        assert result == ['INBOX']

    def test_extra_keywords_add_folders(self):
        """extra_keywords позволяют добавлять кастомные папки."""
        folders = FAKE_FOLDERS + [b'(\\HasNoChildren) "/" "Promotions"']
        imap = MagicMock()
        imap.list.return_value = ('OK', folders)
        result = discover_target_mailboxes(imap, extra_keywords=['promotions'],
                                           account_label='test')
        assert 'Promotions' in result

    def test_no_duplicates(self):
        """Если INBOX упомянут дважды — не дублируется."""
        folders = [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "INBOX"',
        ]
        imap = MagicMock()
        imap.list.return_value = ('OK', folders)
        result = discover_target_mailboxes(imap, account_label='test')
        assert result.count('INBOX') == 1


# ─────────────────────────────────────────────────────────────
# Тесты: ScanMetrics
# ─────────────────────────────────────────────────────────────

class TestScanMetrics:

    def test_empty_metrics(self):
        m = ScanMetrics(scanner='test', account='acc')
        assert m.folders_scanned == 0
        assert m.messages_seen == 0

    def test_record_folder_accumulates(self):
        m = ScanMetrics(scanner='test', account='acc')
        m.record_folder('INBOX', seen=10, deduped=2, parsed=5)
        m.record_folder('Spam', seen=3, deduped=0, parsed=1)
        assert m.folders_scanned == 2
        assert m.messages_seen == 13
        assert m.messages_deduped == 2
        assert m.messages_parsed == 6

    def test_record_folder_with_error(self):
        m = ScanMetrics(scanner='test', account='acc')
        m.record_folder('Spam', error='SELECT failed')
        assert m.folders_scanned == 1
        assert m.folders_failed == 1
        assert m.errors == 1
        assert m.folder_details[0]['error'] == 'SELECT failed'

    def test_start_stop_timing(self):
        m = ScanMetrics(scanner='test', account='acc').start()
        m.stop()
        assert m.elapsed_s >= 0

    def test_summary_line_format(self):
        m = ScanMetrics(scanner='cheque', account='Gmail')
        m.record_folder('INBOX', seen=5, parsed=2)
        line = m.summary_line()
        assert 'scanner=cheque' in line
        assert 'account=Gmail' in line
        assert 'msgs_seen=5' in line
        assert 'parsed=2' in line

    def test_log_summary_calls_logger(self):
        m = ScanMetrics(scanner='test', account='acc')
        m.record_folder('INBOX', seen=3, parsed=1)
        logger = MagicMock()
        m.log_summary(logger)
        assert logger.info.call_count >= 2  # summary + 1 folder detail


# ─────────────────────────────────────────────────────────────
# Тесты: build_message_uid и дедупликация
# ─────────────────────────────────────────────────────────────

class TestBuildMessageUid:

    def test_prefers_message_id(self):
        """Если есть Message-ID, uid строится на его основе."""
        uid = build_message_uid('<abc@example.com>', 'gmail', 'INBOX', b'42')
        assert uid == 'msgid:abc@example.com'

    def test_fallback_to_folder_uid(self):
        """Без Message-ID — uid из account:folder:num."""
        uid = build_message_uid('', 'gmail', 'INBOX', b'42')
        assert uid == 'gmail:INBOX:42'

    def test_cross_folder_dedup(self):
        """Одинаковый Message-ID в разных папках → одинаковый UID."""
        uid_inbox = build_message_uid('<dup@test>', 'gmail', 'INBOX', b'1')
        uid_spam = build_message_uid('<dup@test>', 'gmail', 'Spam', b'99')
        assert uid_inbox == uid_spam


# ─────────────────────────────────────────────────────────────
# Тесты: parse_list_mailbox
# ─────────────────────────────────────────────────────────────

class TestParseListMailbox:

    def test_standard_format(self):
        assert parse_list_mailbox(b'(\\HasNoChildren) "/" "INBOX"') == 'INBOX'

    def test_quoted_name(self):
        assert parse_list_mailbox(b'(\\HasNoChildren) "/" "My Folder"') == 'My Folder'

    def test_nil_delimiter(self):
        assert parse_list_mailbox(b'(\\Noselect) NIL ""') == ''

    def test_empty_line(self):
        assert parse_list_mailbox(b'') is None
        assert parse_list_mailbox(b'  ') is None


# ─────────────────────────────────────────────────────────────
# Тесты: decode_imap_mailbox (modified UTF-7)
# ─────────────────────────────────────────────────────────────

class TestDecodeImapMailbox:

    def test_plain_ascii(self):
        assert decode_imap_mailbox('INBOX') == 'INBOX'

    def test_ampersand_escape(self):
        assert decode_imap_mailbox('Tom &- Jerry') == 'Tom & Jerry'


# ─────────────────────────────────────────────────────────────
# Интеграционный тест: полный mock-проход по 3 папкам
# ─────────────────────────────────────────────────────────────

class TestFullScanSimulation:
    """Проверяет, что сканер обходит INBOX + Spam + Receipts
    и что дедупликация между папками работает."""

    def test_three_folders_scanned_with_dedup(self):
        """Одно письмо с одинаковым Message-ID в INBOX и Spam → считается один раз."""
        mailbox_messages = {
            'INBOX': [
                (b'1', 'Чек Ozon', 'ozon@ozon.ru', 'Заказ 123', '<ozon-123@ozon.ru>'),
                (b'2', 'Чек Wildberries', 'wb@wb.ru', 'Заказ 456', '<wb-456@wb.ru>'),
            ],
            '[Gmail]/Spam': [
                # Дубликат первого письма
                (b'1', 'Чек Ozon', 'ozon@ozon.ru', 'Заказ 123', '<ozon-123@ozon.ru>'),
            ],
            'Receipts': [
                (b'1', 'Чек DNS', 'dns@dns.ru', 'Покупка 789', '<dns-789@dns.ru>'),
            ],
        }

        imap = _build_mock_imap(FAKE_FOLDERS, mailbox_messages)
        folders = discover_target_mailboxes(imap, account_label='test-gmail')

        # Убеждаемся, что выбраны 3 целевые папки
        assert 'INBOX' in folders
        assert '[Gmail]/Spam' in folders
        assert 'Receipts' in folders

        # Имитация прохода сканера с метриками
        metrics = ScanMetrics(scanner='test', account='test-gmail').start()
        seen_ids = set()
        unique_messages = []

        for folder in folders:
            msgs = mailbox_messages.get(folder, [])
            folder_seen = len(msgs)
            folder_deduped = 0
            folder_parsed = 0

            for uid_val, subj, from_addr, body, msg_id in msgs:
                dedup_key = build_message_uid(msg_id, 'test-gmail', folder, uid_val)
                if dedup_key in seen_ids:
                    folder_deduped += 1
                    continue
                seen_ids.add(dedup_key)
                unique_messages.append(subj)
                folder_parsed += 1

            metrics.record_folder(folder, seen=folder_seen,
                                  deduped=folder_deduped, parsed=folder_parsed)

        metrics.stop()

        # 3 уникальных письма из 4 (1 дубликат)
        assert len(unique_messages) == 3
        assert 'Чек Ozon' in unique_messages
        assert 'Чек Wildberries' in unique_messages
        assert 'Чек DNS' in unique_messages

        # Метрики
        assert metrics.folders_scanned == 3
        assert metrics.messages_seen == 4
        assert metrics.messages_deduped == 1
        assert metrics.messages_parsed == 3

    def test_empty_folders_counted(self):
        """Пустые папки тоже считаются в folders_scanned."""
        mailbox_messages = {
            'INBOX': [],
            '[Gmail]/Spam': [],
            'Receipts': [],
        }
        imap = _build_mock_imap(FAKE_FOLDERS, mailbox_messages)
        folders = discover_target_mailboxes(imap, account_label='test')
        metrics = ScanMetrics(scanner='test', account='test').start()

        for folder in folders:
            msgs = mailbox_messages.get(folder, [])
            metrics.record_folder(folder, seen=len(msgs))

        metrics.stop()

        assert metrics.folders_scanned == 3
        assert metrics.messages_seen == 0
        assert metrics.messages_parsed == 0

    def test_folder_select_failure_recorded(self):
        """Ошибка SELECT записывается в метрики."""
        metrics = ScanMetrics(scanner='test', account='test')
        metrics.record_folder('BadFolder', error='SELECT failed: NO')
        metrics.record_folder('INBOX', seen=5, parsed=2)

        assert metrics.folders_scanned == 2
        assert metrics.folders_failed == 1
        assert metrics.errors == 1
        assert metrics.messages_seen == 5
        assert metrics.messages_parsed == 2
