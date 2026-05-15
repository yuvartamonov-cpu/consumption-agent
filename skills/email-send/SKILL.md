---
name: email-send
description: Send outbound email with optional attachments from the local workspace through Gmail SMTP using app passwords from .env. Use when the user asks to send a file, README, report, plan, or plain message to an email address.
---

# Email Send

Use this skill when the user asks to send an outbound email. This is an external action, so state the recipient, subject, and attachments before sending when the request is not already explicit.

## Default Channel

Prefer `scripts/send_email_smtp.py` for reliable local delivery. It reads `.env` from:

1. current working directory
2. `consumption_agent/.env`
3. parent `current/.env`

Supported sender variables:

- `GMAIL_USER` or `IMAP_USER` for the sender address
- `GMAIL_APP_PASSWORD`, `GMAIL_PASSWORD`, or `IMAP_PASSWORD` for SMTP auth

Always strip quotes and spaces from Gmail app passwords before login. Never print password values.

## Send A File

```powershell
python skills/email-send/scripts/send_email_smtp.py `
  --to "recipient@example.com" `
  --subject "README.md — consumption_agent" `
  --body "Здравствуйте,`n`nВо вложении файл.`n" `
  --attach "consumption_agent/README.md"
```

## Send Plain Text

```powershell
python skills/email-send/scripts/send_email_smtp.py `
  --to "recipient@example.com" `
  --subject "Subject" `
  --body "Message body"
```

## Safety

- Do not send secrets, `.env`, credentials, private keys, or token files unless the user explicitly asks and confirms the risk.
- Verify every attachment path exists before sending.
- If Gmail API connector lacks send scope, fall back to this SMTP script.
- If SMTP credentials are missing, report which variable names are missing, not their values.
