"""Helpers for selecting relevant IMAP folders across mail providers.

Provides folder discovery, cross-folder deduplication keys, and unified
scan metrics for daily_cheque_scan, credit_monitor, and fines_bot.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

log = logging.getLogger(__name__)

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


def discover_target_mailboxes(imap, extra_keywords: Iterable[str] = (),
                              *, account_label: str = '') -> List[str]:
    """Return folders worth scanning for receipts, debts and fines."""
    keywords = {kw.lower() for kw in DEFAULT_FOLDER_KEYWORDS}
    keywords.update(kw.lower() for kw in extra_keywords)

    selected = ['INBOX']
    all_folders: list[str] = []
    try:
        status, data = imap.list()
    except Exception as exc:
        log.warning('[IMAP] %s: LIST failed (%s), falling back to INBOX', account_label, exc)
        return selected

    if status != 'OK' or not data:
        log.warning('[IMAP] %s: LIST returned %s, falling back to INBOX', account_label, status)
        return selected

    for raw_line in data:
        mailbox = parse_list_mailbox(raw_line)
        if not mailbox:
            continue
        all_folders.append(mailbox)
        normalized = normalize_mailbox_name(mailbox)
        if normalized == 'inbox':
            if mailbox not in selected:
                selected.append(mailbox)
            continue
        if any(keyword in normalized for keyword in keywords):
            if mailbox not in selected:
                selected.append(mailbox)

    log.info('[IMAP] %s: discovered %d folders, selected %d: %s',
             account_label or '?', len(all_folders), len(selected),
             ', '.join(selected))
    return selected


@dataclass
class ScanMetrics:
    """Accumulates per-scan statistics for observability."""

    scanner: str = ''
    account: str = ''
    folders_scanned: int = 0
    folders_failed: int = 0
    messages_seen: int = 0
    messages_deduped: int = 0
    messages_parsed: int = 0
    errors: int = 0
    elapsed_s: float = 0.0
    folder_details: list = field(default_factory=list)
    _start: float = field(default=0.0, repr=False)

    def start(self) -> 'ScanMetrics':
        self._start = time.monotonic()
        return self

    def stop(self) -> 'ScanMetrics':
        if self._start:
            self.elapsed_s = round(time.monotonic() - self._start, 2)
        return self

    def record_folder(self, name: str, *, seen: int = 0, deduped: int = 0,
                      parsed: int = 0, error: str | None = None) -> None:
        self.folders_scanned += 1
        self.messages_seen += seen
        self.messages_deduped += deduped
        self.messages_parsed += parsed
        if error:
            self.folders_failed += 1
            self.errors += 1
        detail = {'folder': name, 'seen': seen, 'deduped': deduped, 'parsed': parsed}
        if error:
            detail['error'] = error
        self.folder_details.append(detail)

    def summary_line(self) -> str:
        parts = [
            f'scanner={self.scanner}',
            f'account={self.account}',
            f'folders={self.folders_scanned}',
            f'msgs_seen={self.messages_seen}',
            f'deduped={self.messages_deduped}',
            f'parsed={self.messages_parsed}',
        ]
        if self.errors:
            parts.append(f'errors={self.errors}')
        parts.append(f'elapsed={self.elapsed_s}s')
        return ' | '.join(parts)

    def log_summary(self, logger: logging.Logger | None = None) -> None:
        _log = logger or log
        _log.info(f'[SCAN] {self.summary_line()}')
        for d in self.folder_details:
            status = f'seen={d["seen"]} dedup={d["deduped"]} parsed={d["parsed"]}'
            if d.get('error'):
                status += f' ERROR={d["error"]}'
            _log.info(f'  └ {d["folder"]}: {status}')


def build_message_uid(message_id: str | None, account_label: str, mailbox_name: str, uid: bytes | str) -> str:
    """Prefer Message-ID for cross-folder deduplication, fall back to folder UID."""
    normalized = (message_id or '').strip().strip('<>')
    if normalized:
        return f'msgid:{normalized}'
    uid_text = uid.decode() if isinstance(uid, bytes) else str(uid)
    return f'{account_label}:{mailbox_name}:{uid_text}'
