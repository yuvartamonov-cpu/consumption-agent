#!/usr/bin/env python3
"""Send email through Gmail SMTP using app-password credentials from .env."""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

DEFAULT_WORK_EMAIL = "yu.v.artamonov@gmail.com"


def load_env(paths: list[Path]) -> list[Path]:
    loaded: list[Path] = []
    for env_path in paths:
        if not env_path.exists():
            continue
        loaded.append(env_path)
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return loaded


def clean_password(value: str) -> str:
    return value.replace(" ", "").strip().strip('"').strip("'")


def env_paths(cwd: Path) -> list[Path]:
    return [
        cwd / ".env",
        cwd / "consumption_agent" / ".env",
        cwd.parent / "current" / ".env",
    ]


def add_attachment(msg: EmailMessage, path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Attachment not found: {path}")
    guessed_type, _ = mimetypes.guess_type(path.name)
    if guessed_type:
        maintype, subtype = guessed_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"
    msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)


def send_email(
    to: str,
    subject: str,
    body: str,
    attachments: list[Path],
    sender: str | None = None,
) -> None:
    cwd = Path.cwd()
    load_env(env_paths(cwd))

    smtp_user = sender or os.environ.get("GMAIL_USER") or os.environ.get("IMAP_USER")
    smtp_user = smtp_user or "yu.v.artamonov@gmail.com"
    password = clean_password(
        os.environ.get("GMAIL_APP_PASSWORD")
        or os.environ.get("GMAIL_PASSWORD")
        or os.environ.get("IMAP_PASSWORD")
        or ""
    )
    if not password:
        raise RuntimeError("Missing Gmail SMTP password: set GMAIL_APP_PASSWORD or GMAIL_PASSWORD")

    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    for attachment in attachments:
        add_attachment(msg, attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as server:
        server.login(smtp_user, password)
        server.send_message(msg)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send email through Gmail SMTP.")
    parser.add_argument(
        "--to",
        default=None,
        help=(
            "Comma-separated recipient email addresses. "
            f"Defaults to WORK_EMAIL or {DEFAULT_WORK_EMAIL}."
        ),
    )
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", default=None)
    parser.add_argument(
        "--body-file",
        default=None,
        help="Read the message body from a UTF-8 text file. Useful for long messages.",
    )
    parser.add_argument("--attach", action="append", default=[], help="Attachment path. Can be repeated.")
    parser.add_argument("--sender", default=None, help="Override sender email address.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    to = args.to or os.environ.get("WORK_EMAIL") or DEFAULT_WORK_EMAIL
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    else:
        body = args.body
    if body is None:
        raise RuntimeError("Missing email body: pass --body or --body-file")
    attachments = [Path(item).expanduser().resolve() for item in args.attach]
    send_email(to, args.subject, body, attachments, sender=args.sender)
    print("sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
