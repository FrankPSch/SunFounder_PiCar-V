# PiCar work plan

## Goal (Frank's, only Frank changes it)

From Frank's requests on 2026-11-15, in his words:
- "we have to create a version control where you and the changes made via voice can see and have to add whatever is changed"
- "we have to have a full list of everything that needs to be tested which is basically the list of requirements in variants,
  starting with the system level such as remote update, up to all voice and click commands by category such as camera.
  this list must also be accessible in claude voice via PC or here in the chat"
- "we must have something similar to a claude.md which summarizes the way we work and lists the key documents so that a new
  chat finds the way or voice chat as well"

## Current task (chat)

Collaboration of Claude voice, voice builder and chat, phases 1-4 (CLAUDE.md "How we work"): built 2026-11-15, waiting for
Frank's push. Then: a real learning session while driving (S.learning.session driving with Frank's voice), and the open
requirements in project/generated/REQUIREMENTS.md that need the real car or Frank's ear.

## Open decisions for Frank

- Voice builds run `claude -p` with the voice server's environment, ANTHROPIC_API_KEY included, so they are billed to the API
  account (about 0.50 USD per build round on 2026-11-15), not to the Claude Code plan. Keep, or run builds under the plan login?
- The repo is on GitHub: CLAUDE.md and project/*.md are published with every push (no secrets, but paths and local IPs).

## Done log

| Date | What |
|---|---|
| 2026-11-15 | Phase 1: CLAUDE.md, integration lock, measured car facts, wishes for the chat (`e5c9cda`) |
| 2026-11-15 | Phases 2-4: requirements = tests with gap report, post-deploy check on the real car, Claude voice read tools, deploy summary with phrase-meaning gate, chat deploy news, version tags, rollback by voice, documents in project/ |
| 2026-11-15 | Natural voice (Piper recordings on the car), all settings in the dashboard, wish list (`440b884` and before) |
| 2026-11-14/15 | Voice stack refactor, learning sessions, voice check (see SETUP_LOG.md) |

## Known gotchas

- Windows: text given to `ssh ... sh -s` must be bytes (CRLF breaks the car's shell); `\n` inside heredoc-written Python became real newlines (use the Edit tool).
- The sim suites have timing checks (ball tracking, navigation): one clean run of three counts.
- The voice server runs the code of this checkout: a rebase here changes what the next voice server start loads.
