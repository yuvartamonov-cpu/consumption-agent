# Agent Rules — Consumption Agent

## Which agents operate here

| Agent | Role |
|-------|------|
| **Клодюр (Claude)** | Main AI assistant via OpenClaw. Runtime diagnostics, manual tasks, architecture decisions |
| **Paperclip AI** | CEO-level code agent (WSL). Sees the full repo, implements tasks |
| **GitHub Actions (future)** | CI pipeline: tests, linting, compile-check |
| **Code Review AI** | Reviews diffs before merge |

## Task workflow

1. **Issue** → describes goal, context, constraints, acceptance criteria
2. **Branch** → `feature/*` or `fix/*` from `dev`
3. **Implementation** → code + tests
4. **Pull Request** → diff + description
5. **AI Code Review** → automated check
6. **Manual Review** → CEO approves
7. **Merge** → to `dev`, then `main` when stable
8. **Tag** → versioned release

## Task template

```markdown
# Task

## Goal
What needs to be built/fixed.

## Context
Related files, modules, DB tables.

## Constraints
What must NOT be changed.

## Acceptance criteria
Measurable conditions for completion.

## Tests
What tests to add/update.

## Security notes
What to check: data exposure, secrets, permissions.
```

## Rules for all agents

1. **No secrets in output.** Never paste tokens, passwords, API keys in chat.
2. **Core-first.** Logic lives in backend modules, not Telegram commands.
3. **Governance before action.** No external action without proposal → approval → audit.
4. **Explain recommendations.** Every suggestion must answer: what, why, based on what data.
5. **Dry-run by default.** Until Phase E, all external actions are simulated.
6. **PR-only changes to `main`.** Direct pushes only to `dev` / feature branches.
7. **Tests accompany code.** No untested logic merge.
8. **Ask before spending.** No financial actions without explicit confirmation.
