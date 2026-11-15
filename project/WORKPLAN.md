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

Architecture backlog (below): all 13 items built on 2026-11-15, each behind a switch that is off, tested on the digital twin
(tools/twin.py) after every step, committed locally; waiting for Frank's push. Then Frank switches them on one by one (table below).
Later: a real learning session while driving (S.learning.session driving with Frank's voice), and the open requirements in
project/generated/REQUIREMENTS.md that need the real car or Frank's ear.

### Switching the new ways on (one at a time; each switch off = the old way)

| # | Switch | Where | What to check after switching on |
|---|---|---|---|
| 1 | `car_ssh_module` | PC: `voice\pc_switches.json` `{"car_ssh_module": true}`, then restart the voice server | a push: car update, deploy check 6 of 6 |
| 2 | `voice_folder_by_kind` | PC: same file, restart the voice server and launcher | `voice\data`, `voice\logs`, `voice\generated` exist; the old files stay until you remove them |
| 3 | `single_config_file` | dashboard switch | `picar-data/picar.json`; switches and settings survive a restart |
| 4 | `data_files_as_pi` | dashboard switch | `ls -l /home/pi/picar-data`: everything pi |
| 5 | `disk_warning` | dashboard switch | over 80 % full: warning with the biggest folders (the car is at 9 %) |
| 6 | `voice_tool_registry` | PC: `voice\pc_switches.json`, restart the voice server | Claude voice: "wie ist der Projektstatus", a feature switch |
| 7 | `service_list` | dashboard switch | section "Services": six loops running |
| 8 | `state_sections` | dashboard switch | dashboard as before; CPU load of the Pi a little lower |
| 9 | `detector_registry` | dashboard switch | ball, face, QR, markers as before |
| 10 | `dashboard_calibration` | dashboard switch | section "Calibration": camera and steering (rear wheels stay on /cali/) |

Items without a switch (tests and code layout only): wait-for checks and sim tests as unittest (tests/sim/), feature_builder
in three (sim_runner.py, build_checks.py), car paths only in pc_settings. Kept until Frank says they can go: tools/retired/
(old sim scripts, feature_builder before the split), the old SunFounder pages.

## Architecture backlog (agreed with Frank in an ask loop, 2026-11-15; built the same day behind switches, see above)

Main goal Frank picked: **data and logs in order**. Each item is one push, same behaviour, tests green between steps; a plan for Frank's OK before each.
Order = suggested sequence (the earlier ones make the later ones smaller).

| # | Area | Item | What changes | Measured on 2026-11-15 |
|---|---|---|---|---|
| 1 | PC | **One `car_ssh.py`** | `run`, `run_script(bytes)`, `copy_tar`, `is_on`; key, host, timeouts only in `pc_settings`; a test fails if another module calls ssh | ssh built in 6 places (car_facts, car_tunnel ×2, deploy_check, prerender_voice ×2, release) |
| 2 | Data | **PC voice folder by kind** | `voice\data` (wishes, recordings, results: Frank's, back up), `voice\logs`, `voice\generated` (rebuildable: status, catalog, coverage), `voice\runtime` (env, simenv, models, piper, builds, repo, tmp); paths only in `pc_settings.py`; leftover `simdata_*` cleaned | everything flat in `voice\`, 3 leftover simdata folders, scripts gpu.py / measure.py among data |
| 3 | Data | **Car data written as pi** | data files created with owner pi (or the service runs as pi with gpio/audio groups); one fix of the existing files | features.json, settings.json, db.sqlite3, media/ owned by root |
| 4 | Config | **One config file per side** | car: one JSON with sections features / settings / calibration, written atomically, with a schema version; PC: `pc_settings.py` the only source, copies replaced (e.g. `voice_check.CAR_DATA_DIR`) and checked by a test | car: features.json + settings.json + plain `calibration` file |
| 5 | Data | **Disk warning, no deleting** | dashboard and Claude voice warn when the car's disk passes a threshold; nothing is deleted automatically | picar-data 585 MB (media 367, models 182, voice_cache 33), journal 224 MB, 51 GB free |
| 6 | Tests | **Wait-for, not sleep** | every timing check waits for a condition with a timeout; a check forbids a bare sleep before a check | 2 flaky sim checks fixed 2026-11-15 (0859e64) |
| 7 | Tests | **Sim checks as unittest** | sim_test*.py become unittest cases named after requirement ids, so evidence and failures line up | sim suites are scripts with check() |
| 8 | PC | **One voice tool registry** | `voice_tools.py`: name, description, schema, handler, side-effect level (read / car / build / deploy); claude_chat and voice_server both read it; a test checks every tool is complete | schemas in claude_chat.py, handlers in voice_server._run_tool |
| 9 | PC | **feature_builder in three** | `sim_runner.py` (sim car, suites, retries; also run_tests), `build_checks.py` (rules, phrase meanings, deploy summary), `feature_builder.py` (wish state machine only) | 396 lines, 6 jobs |
| 10 | Car | **One service list** | one startup module starts and stops the services in a fixed order; each thread reports alive / last error in /api/state and the dashboard | 15 threads started in 10 modules, singletons built on import |
| 11 | Car | **/api/state in sections** | a registry of state sections; car and tracker every poll, telemetry / media / rooms / settings on change or every few seconds | one answer from 9 modules on every poll |
| 12 | Car | **Detector registry** | one small module per detector (switch, `detect(frame)`); tracker.py keeps the camera loop and dispatch; new vision wishes add a file | tracker.py 306 lines: 6 detectors + stuck + treasure + follow |
| 13 | Car | **One web UI** | calibration becomes a dashboard section; the old SunFounder pages (views.py home/run/cali) are commented out after a check | two UIs |

Not chosen (Frank, 2026-11-15): automatic deleting of old data (warn only); push instead of poll; keeping both UIs; faster parallel push tests.

## Open decisions for Frank

- Voice builds run `claude -p` with the voice server's environment, ANTHROPIC_API_KEY included, so they are billed to the API
  account (about 0.50 USD per build round on 2026-11-15), not to the Claude Code plan. Keep, or run builds under the plan login?
- The repo is on GitHub: CLAUDE.md and project/*.md are published with every push (no secrets, but paths and local IPs).

## Done log

| Date | What |
|---|---|
| 2026-11-15 | Architecture backlog, all 13 items behind switches (off): digital twin first (`3ee4738`: sim sensors, microphone, speaker, file owners, car-like picar-data, fake SSH car), then `afe2b44` tests/sim, `1e42b89` car_ssh + voice folder, `d9e820a` one config / files as pi / disk warning, `4cb7ab7` tool registry + builder split, `1416824` services / state sections / detectors / calibration. Twin finds: empty voice cache reported "natural" (fixed); the sim ball path depended on the clock (53-74 of 74 found, also before the changes; now starts with the sim) |
| 2026-11-15 | Phase 1: CLAUDE.md, integration lock, measured car facts, wishes for the chat (`e5c9cda`) |
| 2026-11-15 | Phases 2-4: requirements = tests with gap report, post-deploy check on the real car, Claude voice read tools, deploy summary with phrase-meaning gate, chat deploy news, version tags, rollback by voice, documents in project/ |
| 2026-11-15 | Natural voice (Piper recordings on the car), all settings in the dashboard, wish list (`440b884` and before) |
| 2026-11-14/15 | Voice stack refactor, learning sessions, voice check (see SETUP_LOG.md) |

## Known gotchas

- Windows: text given to `ssh ... sh -s` must be bytes (CRLF breaks the car's shell); `\n` inside heredoc-written Python became real newlines (use the Edit tool).
- The sim tests (tests/sim/) have timing checks (ball tracking, navigation): one clean run of three counts; waits are for conditions.
- Car switches are read when a module starts using them; PC switches (`voice\pc_switches.json`) when a PC tool starts.
- The voice server runs the code of this checkout: a rebase here changes what the next voice server start loads.
