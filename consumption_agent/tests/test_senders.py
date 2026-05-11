"""Tests for FINANCIAL_SENDERS consistency."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from consumption_agent_full_030526 import FINANCIAL_SENDERS


def test_no_duplicate_ids():
    """Each (id, mailbox) pair must be unique — duplicate IDs cause source collisions."""
    ids = [s['id'] for s in FINANCIAL_SENDERS]
    assert len(ids) == len(set(ids)), (
        f"Duplicate sender IDs found: "
        f"{[id for id in set(ids) if ids.count(id) > 1]}"
    )


def test_mailbox_field_values():
    """mailbox field must be one of the supported values."""
    valid = {'gmail', 'yandex', 'zorea', 'neutrinon'}
    for s in FINANCIAL_SENDERS:
        mb = s.get('mailbox', 'gmail')
        assert mb in valid, f"sender '{s['id']}' has unknown mailbox '{mb}'"


def test_required_yandex_senders():
    """Core financial senders must be present in the yandex mailbox profile."""
    expected = {'yandex_market_ya', 'yandex_lavka_ya', 'yandex_eda_ya', 'yandex_taxi_ya', 'yandex_drive', 'ofd_yandex_ya'}
    yandex_ids = {s['id'] for s in FINANCIAL_SENDERS if s.get('mailbox') == 'yandex'}
    missing = expected - yandex_ids
    assert not missing, f"Missing yandex senders: {missing}"
