"""Helpers for selecting relevant IMAP folders across mail providers."""

from __future__ import annotations

import base64
import re
from typing import Iterable, List, Optional

DEFAULT_FOLDER_KEYWORDS = (
    'spam',
    'junk',
    'bulk',
    'спам',
    'нежел',
    'чек',
    'чеки',
    'квитан',
    'receipt',
    'receipts',
    'check',
    'checks',
)


def decode_imap_mailbox(value: str) -> str:
    """Decode an IMAP modified UTF-7 mailbox name when needed."""
    if '&' not in value:
        return value

    result: list[str] = []
    i = 0
    while i < len(value):
        if value[i] != '&':
            result.append(value[i])
            i += 1
            continue

        end = value.find('-', i)
        if end == -1:
            result.append(value[i:])
            break

        chunk = value[i + 1:end]
        if not chunk:
            result.append('&')
        else:
            b64 = chunk.replace(',', '/')
            b64 += '=' * (-len(b64) % 4)
            try:
                decoded = base64.b64decode(b64).decode('utf-16-be')
            except Exception:
                decoded = value[i:end + 1]
            result.append(decoded)
        i = end + 1
    return ''.join(result)


def parse_list_mailbox(raw_line: bytes | str) -> Optional[str]:
    """Extract mailbox name from an IMAP LIST response line."""
    if isinstance(raw_line, bytes):
        line = raw_line.decode('utf-8', errors='replace')
    else:
        line = str(raw_line)
    line = line.strip()
    if not line:
        return None

    match = re.match(r'^\([^)]*\)\s+(?:"[^"]*"|NIL)\s+(.+)$', line)
    if not match:
        return None

    mailbox = match.group(1).strip()
    if mailbox.startswith('"') and mailbox.endswith('"'):
        mailbox = mailbox[1:-1].replace('\"', '"')
    return decode_imap_mailbox(mailbox)


def normalize_mailbox_name(name: str) -> str:
    return re.sub(r'\s+', ' ', name).strip().lower()


def discover_target_mailboxes(imap, extra_keywords: Iterable[str] = ()) -> List[str]:
    """Return folders worth scanning for receipts, debts and fines."""
    keywords = {kw.lower() for kw in DEFAULT_FOLDER_KEYWORDS}
    keywords.update(kw.lower() for kw in extra_keywords)

    selected = ['INBOX']
    try:
        status, data = imap.list()
    except Exception:
        return selected

    if status != 'OK' or not data:
        return selected

    for raw_line in data:
        mailbox = parse_list_mailbox(raw_line)
        if not mailbox:
            continue
        normalized = normalize_mailbox_name(mailbox)
        if normalized == 'inbox':
            if mailbox not in selected:
                selected.append(mailbox)
            continue
        if any(keyword in normalized for keyword in keywords):
            if mailbox not in selected:
                selected.append(mailbox)

    return selected


def build_message_uid(message_id: str | None, account_label: str, mailbox_name: str, uid: bytes | str) -> str:
    """Prefer Message-ID for cross-folder deduplication, fall back to folder UID."""
    normalized = (message_id or '').strip().strip('<>')
    if normalized:
        return f'msgid:{normalized}'
    uid_text = uid.decode() if isinstance(uid, bytes) else str(uid)
    return f'{account_label}:{mailbox_name}:{uid_text}'
