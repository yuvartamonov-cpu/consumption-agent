#!/usr/bin/env python3
"""Compatibility wrapper for the email-send skill.

This file exists so older agent notes that mention `send_email.py` still use the
supported SMTP implementation instead of stale hardcoded credentials.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "skills" / "email-send" / "scripts" / "send_email_smtp.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("send_email_smtp", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load email-send script: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
