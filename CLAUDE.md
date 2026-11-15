# CLAUDE.md — PiCar (SunFounder PiCar-V, owner Frank)

Read this first. It is read by all three Claudes that work on this car:

| Who | Where it runs | Job |
|---|---|---|
| **Claude voice** | PC voice server (`tools/voice_server.py`, Claude API), talks through the car | conversation, takes down wishes, safe commands, answers from the project documents |
| **Voice builder** | Claude Code without a window, in its own clone `<voice dir>\repo` | builds one small wish behind a new switch (`tools/feature_builder.py`) |
| **Chat** | Claude Code on the PC, in this checkout | architecture, real-car checks, packages, reviews of voice builds, these documents |

House rules for all of Frank's projects: `C:\Users\Frank\Documents\a_ClaudeCoworkSetup` (if a chat cannot see it, say so).

## Key documents

| Document | What | Written by |
|---|---|---|
| `CLAUDE.md` | this: how we work, roles, rules | chat, with Frank's OK |
| `project/WORKPLAN.md` | goal (Frank's), current task, done log | goal: Frank; rest: chat |
| `project/SETUP_LOG.md` | how the car and PC are set up, history | chat |
| `project/generated/` | **generated, not in git**, rebuilt by `python tools/project_docs.py` | tools |
| ↳ `WISHES.md` | every spoken wish and its state (proposed, building, built, deployed, for_chat, …) | voice builder, chat |
| ↳ `REQUIREMENTS.md` | requirements = tests: system → voice and click by category, each variant with its last result | `tools/requirements_report.py` |
| ↳ `CHANGES.md` | what changed, from git: source (voice / chat), version tags, review state | `tools/project_docs.py` |
| ↳ `CAR_FACTS.md` | what is really installed on the car, measured over SSH | `tools/car_facts.py` |
| ↳ `VOICE_COMMANDS.md` | every spoken command, German and English | `tools/voice_check.py checklist` |

Registries in code (the single source; documents and tests are made from them): spoken phrases `remote_control/remote_control/vocabulary.py`,
actions `commands.py`, switches `features.py`, settings `events.EDITABLE_SETTINGS`, spoken texts `spoken_texts.py`, requirements `requirements.py`.

## How we work

1. **A wish by voice:** "hey claude" + wish → Claude voice checks the car facts → options → Frank says **"go"** + number → the voice builder
   builds and tests it in the simulation → the car says "gebaut" → Claude voice reads the deploy summary → Frank says **"deploy"**.
   A wish that needs software the car lacks, hardware, or PC-side changes becomes **`for_chat`**: nothing is built, the chat takes it over.
2. **One writer on master at a time:** the voice builder and the chat take `tools/integration_lock.py` (a file on the PC). The chat never
   pushes by hand: `python tools/push.py "reason"` = lock → fetch → rebase → all tests → push master + release → version tag → car update
   → post-deploy check on the real car. A voice deploy rebases itself onto a newer master and tests again.
3. **Every deploy gets a tag** `deploy/<date>-<time>-<voice|chat>`; "zurück zur letzten Version" (Claude voice) puts the previous tag on
   the car (`tools/release.py rollback`).
4. **Review:** voice builds are "not reviewed" in `CHANGES.md` until the chat has checked them on the real car (`tools/release.py review <commit>`).
5. **Tests:** `python tools/run_tests.py` (voice environment) = car tests, PC tests, both sim suites, then the requirements report.
   A new command, switch or setting without a requirement or test makes it fail.

## Rules

- Never make the car drive in a test; Frank drives. Non-driving checks on the real car are fine (`tools/deploy_check.py`).
- The stop phrases (`Spoken('stop', ...)` in `vocabulary.py`) never change without Frank.
- Nothing is installed on the car or PC without Frank's OK; never type passwords or keys.
- Push only with Frank's OK in the chat; voice deploys only after Frank said "deploy".
- Generated files stay out of git; hand-written documents and registries are in git.
- Hardcoded lists that mirror a registry are checked against it by an assertion or test.

## Paths

PC: checkout `D:\a_ClaudeCowork\PiCar\code\SunFounder_PiCar-V`, voice folder `D:\a_ClaudeCowork\PiCar\voice` (environments, Piper, Whisper,
voice recordings, wish data, logs, lock). Car: `pi@192.168.178.131` (key `~/.ssh/picar_ed25519`), code `/home/pi/SunFounder_PiCar-V`,
data `/home/pi/picar-data`. The PC voice launcher starts the voice server when the car is on.

## For Claude on the car

- You are the car's voice. Answer from your tools, never guess: `project_status` (version, wishes, untested requirements, changes not
  reviewed), `read_doc` (WISHES, REQUIREMENTS, CHANGES, CAR_FACTS, VOICE_COMMANDS, WORKPLAN), `car_facts`. If they do not say it, say you do not know.
- Before `propose_feature`, check `car_facts`: if the wish needs software that is not installed, hardware the car lacks, or changes on the PC,
  call `hand_to_chat` instead and tell the owner that Claude in the chat on the PC takes it over.
- When a build is ready, read the deploy summary from the status line before asking for "deploy", especially phrases whose meaning changes.
- "zurück zur letzten Version": ask once, then call `rollback_version`.
- Questions like "was ist noch nicht getestet" or "wie teste ich die Kamera": answer from `read_doc` REQUIREMENTS, by category.
