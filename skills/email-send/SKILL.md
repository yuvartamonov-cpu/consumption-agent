---
name: email-send
description: Send outbound email with optional attachments from the local workspace through Gmail SMTP using app passwords from .env. Use when the user asks to send a file, README, report, plan, or plain message to an email address.
---

# Email Send

Use this skill when the user asks to send an outbound email. This is an external action, so state the recipient, subject, and attachments before sending when the request is not already explicit.

## Non-Negotiable Default

For outbound mail to the user's working email, **do not use the Gmail API connector first**. It repeatedly fails in this environment with `ACCESS_TOKEN_SCOPE_INSUFFICIENT`.

Use local SMTP through:

```text
skills/email-send/scripts/send_email_smtp.py
```

The working email alias is:

```text
yu.v.artamonov@gmail.com
```

If the user says "рабочая почта", "work email", or "мне на почту" without giving another address, send to `yu.v.artamonov@gmail.com`.

## Default Channel

Prefer `scripts/send_email_smtp.py` for reliable local delivery. It reads `.env` from:

1. current working directory
2. `consumption_agent/.env`
3. parent `current/.env`

Supported sender variables:

- `GMAIL_USER` or `IMAP_USER` for the sender address
- `GMAIL_APP_PASSWORD`, `GMAIL_PASSWORD`, or `IMAP_PASSWORD` for SMTP auth

Always strip quotes and spaces from Gmail app passwords before login. Never print password values.

## Python Command

On Windows, plain `python` can be unavailable or point to a launcher that only prints `Python`. If that happens, use the bundled runtime:

```powershell
& 'C:\Users\Yuri Artamonov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  skills/email-send/scripts/send_email_smtp.py `
  --to "yu.v.artamonov@gmail.com" `
  --subject "Subject" `
  --body "Message body"
```

## Send A File

```powershell
& 'C:\Users\Yuri Artamonov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  skills/email-send/scripts/send_email_smtp.py `
  --to "yu.v.artamonov@gmail.com" `
  --subject "README.md — consumption_agent" `
  --body "Здравствуйте,`n`nВо вложении файл.`n" `
  --attach "consumption_agent/README.md"
```

## Send Plain Text

```powershell
& 'C:\Users\Yuri Artamonov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  skills/email-send/scripts/send_email_smtp.py `
  --to "yu.v.artamonov@gmail.com" `
  --subject "Subject" `
  --body "Message body"
```

## Send Long Text

For long Russian text, avoid very long PowerShell command arguments. Write the body to a temporary `.txt` file inside the workspace, then send it with `--body-file`:

```powershell
& 'C:\Users\Yuri Artamonov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  skills/email-send/scripts/send_email_smtp.py `
  --to "yu.v.artamonov@gmail.com" `
  --subject "Subject" `
  --body-file "tmp_email_body.txt"
```

## Safety

- Do not send secrets, `.env`, credentials, private keys, or token files unless the user explicitly asks and confirms the risk.
- Verify every attachment path exists before sending.
- If Gmail API connector lacks send scope, use this SMTP script immediately.
- If SMTP credentials are missing, report which variable names are missing, not their values.
- Success is the script printing `sent`.
