---
name: ship
description: Pre-commit check for this repo — runs the Markdown linter and the docs tests on the current changes, then drafts a commit message. Use when the user types /ship or asks to "ship", "check before committing", or "write a commit message" for their changes.
---

# /ship — lint, test, draft commit message

Run these steps in order. Stop and report at the first step that fails.

## 1. See what changed

```bash
git status --short
git diff --stat HEAD
```

If there are no changes (tracked or untracked), say so and stop.

## 2. Run lint + tests

```bash
bash .claude/skills/ship/scripts/check.sh
```

- By default it only reports lint errors on **lines the user changed**, so the
  README's older style issues don't block a small edit. Pass `--all` only if the
  user asks for a full-file check.
- Lint rules live in `.markdownlint.jsonc` at the repo root.
- Tests: checks that every ```` ``` ```` code fence is closed, and runs
  `npm test` / `pytest` automatically if the project ever adds one.

**If it exits non-zero:** show the failing lines, explain each in one short
line, and offer to fix them. Do not draft the commit message until the check
is green (unless the user explicitly says to skip).

## 3. Draft the commit message

Read the actual diff (`git diff --staged`, or `git diff HEAD` if nothing is
staged) and `git log --oneline -10` to match the repo's style. Then write:

- **Subject:** imperative, ≤ 72 chars, no trailing period, in English
  (matches history, e.g. `Update README.md with date format change`).
- **Body** (only if the change isn't obvious from the subject): blank line,
  then 1–3 short lines on *why*, wrapped at 72 chars.

Show the message in a fenced block, plus the command to use it:

```bash
git commit -F- <<'MSG'
<subject>

<body>
MSG
```

## 4. Do NOT commit or push

This skill only drafts. Commit or push only if the user asks afterwards.
