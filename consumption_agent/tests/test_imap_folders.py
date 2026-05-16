import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from imap_folders import discover_target_mailboxes, parse_list_mailbox


class FakeImap:
    def list(self):
        return (
            'OK',
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "[Gmail]/Spam"',
                b'(\\HasNoChildren) "/" "Receipts"',
                b'(\\HasNoChildren) "/" "Sent"',
            ],
        )


def test_parse_list_mailbox_extracts_name():
    assert parse_list_mailbox(b'(\\HasNoChildren) "/" "Receipts"') == 'Receipts'


def test_discover_target_mailboxes_includes_spam_and_receipts():
    assert discover_target_mailboxes(FakeImap()) == ['INBOX', '[Gmail]/Spam', 'Receipts']
