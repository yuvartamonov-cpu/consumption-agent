# Security Model — Consumption Agent

## Current state

- ✅ **Telegram whitelist** — only chat_id=1477860192
- ✅ **.env** — secrets stored outside git
- ✅ **.gitignore** — excludes binary artifacts, .venv, data/, media/
- ✅ **Local SQLite** (WAL mode) — no cloud storage of sensitive data
- ✅ **Systemd autorestart** — consumption-bot.service (Restart=always)
- ✅ **App passwords** — Gmail, Yandex, Mail.ru use application-specific passwords
- ✅ **allowInsecureAuth=false** — OpenClaw Gateway

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Telegram token leak | Rotation checklist, never paste in chat, .env only |
| Email access | App passwords, per-mailbox credentials, IMAP read-only |
| DB loss | Daily .backup, integrity_check, restore test |
| DB leak | Encrypted backups, privacy router for cloud data |
| LLM data leak | Privacy router: local/cloud/anonymous before sending to models |
| Unauthorized actions | Governance layer: proposals → approvals → dry-run → execute |
| Multi-agent code corruption | PR-only workflow, protected main, tests, code review |
| VPN dependency | CLI fallback, decouple core from Telegram |

## Privacy router

Data sensitivity levels when sending to cloud LLMs:

- **local_only:** bank notifications, full receipts with PII, tokens/passwords, documents
- **cloud_allowed_after_redaction:** anonymized product names, categories, style features
- **cloud_allowed:** public product descriptions, spec comparisons, similar item search

## Secret hygiene

- `.env` never committed
- `.env.example` in repo (placeholder values)
- Pre-commit hook: scan for secrets before push
- Regular token rotation
- No secrets in logs, error messages, or Telegram responses
