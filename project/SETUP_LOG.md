# PiCar-V — Setup and Debug Log

Living record of what was installed on the car, how, and what went wrong on the way.
Read this before debugging or changing the car. Newest state first, then history.

## 1. Quick reference

| Item | Value |
|---|---|
| Car | SunFounder PiCar-V (Smart Video Car Kit V2.0, 2017) |
| Board | Raspberry Pi 3 Model B+ Rev 1.3, 1 GB RAM, MAC `b8:27:eb:40:ad:a3` |
| OS | Raspbian 11 Bullseye armhf, image `2025-05-06-raspios-bullseye-armhf.img.xz`, 58 GB card |
| IP | `192.168.178.131` (fixed in FRITZ!Box). `raspberrypi.local` does **not** resolve from the PC |
| SSH | `ssh -i ~/.ssh/picar_ed25519 pi@192.168.178.131` (key on PC: `C:\Users\Frank\.ssh\picar_ed25519`) |
| sudo | passwordless for `pi` |
| Web control | `http://192.168.178.131:8000` new dashboard; classic SunFounder page at `/classic/` (Django dev server, **no login**) |
| Camera stream | `http://192.168.178.131:8080/?action=stream`, snapshot `?action=snapshot` (640x480 JPEG) |
| Service | `picar.service` — `sudo systemctl status picar`, logs `journalctl -u picar -f` |
| Code on Pi | `~/SunFounder_PiCar-V` = clone of `https://github.com/FrankPSch/SunFounder_PiCar-V`, detached at the deployed commit (see `~/picar-data/update-status.json`) |
| Images on PC | `C:\Users\Frank\Downloads\PiCar-images\` (D: is nearly full) |
| Plain README | `D:\a_ClaudeCowork\PiCar\README.txt` (sources, SHA256 of images) |

## 2. Hardware found on the Pi

| Part | How it shows up | Notes |
|---|---|---|
| Servo/PWM driver PCA9685 | I²C `0x40` (+ all-call `0x70`) | ch 0 steering, ch 1 camera pan, ch 2 camera tilt |
| ADC PCF8591 | I²C `0x48` | unused by remote control |
| Motor driver TB6612FNG | via PCA9685 PWM + GPIO | rear wheels; V2 code only |
| Camera | USB `GEMBIRD Generic UVC 1.00 (AppoTech AX2311)`, `/dev/video0` | MJPEG 640x480 / 352x288 / 320x240 / 176x144 / 160x120 |
| Microphone | USB `USB PnP Sound Device`, ALSA card 2 (capture only) | lsusb: TI PCM2902 |
| Audio out | ALSA card 0 headphone jack, card 1 HDMI | no speaker connected yet |
| Power | `vcgencmd get_throttled` = `0x50000` on 2026-11-02 | **under-voltage and throttling have occurred** — weak supply/battery. Expect resets when motors draw current |

Calibration file `remote_control/remote_control/driver/config` (as shipped in repo, not yet calibrated for this car):
`forward_A=0, forward_B=0, turning_offset=-21, pan_offset=-23, tilt_offset=18`.

## 3. What was installed (2026-11-02)

### 3.1 SD card (from Windows, no Imager settings)
Raspberry Pi Imager wrote the image but did **not** offer OS customisation for a "Use custom" file.
Headless setup was done by hand on the boot partition (`bootfs`, mounted as `P:` via admin Disk Management):
- `ssh` — empty file, enables SSH on first boot
- `wpa_supplicant.conf` — `country=DE`, Frank's 2.4 GHz Wi-Fi (LF line endings)
- `userconf.txt` — `pi:<sha512-crypt>`, created by `scratchpad\make_userconf.ps1` (openssl from Git, password never shown)
After first boot, the PC key was added to `~/.ssh/authorized_keys` via `scratchpad\install_picar_key.ps1` (one password login).

### 3.2 On the Pi — deviations from SunFounder's `install_dependencies`
The script was **not** run as-is; its steps were run one by one:

| Step in script | What was done | Result |
|---|---|---|
| `pip3 install django==2.0` | same | works on Python 3.9.2 (no pin change needed) |
| `apt install python3-smbus git` | + `i2c-tools`, `python3-pip` | ok |
| `apt install python3-opencv` | same | OpenCV 4.5.1 |
| clone `sunfounder/SunFounder_PiCar` + `python setup.py install` | only `python3 setup.py install` (no python2 on Bullseye) | `~/SunFounder_PiCar` @ `9890ab7`, module `picar` 1.0.1 |
| copy bundled `mjpg-streamer` to `/usr/local` | copied, then **replaced** (see 3.3) | — |
| append `dtparam=i2c_arm=on` | `sudo raspi-config nonint do_i2c 0` instead | active without reboot |

### 3.3 Camera streamer replaced
The bundled `mjpg-streamer/mjpg_streamer` is a 32-bit binary linked against `libjpeg.so.8`, which Bullseye does not ship
(only `libjpeg62-turbo` / `libjpeg9`). Symlinking would fail the libjpeg runtime version check (62 vs 80).
Fix: built `https://github.com/jacksonliam/mjpg-streamer` (`310b29f`) in `~/mjpg-streamer/mjpg-streamer-experimental`
(apt: `cmake libjpeg62-turbo-dev gcc g++`), copied to the fixed paths `stream.py` calls:
`/usr/local/bin/mjpg_streamer`, `/usr/local/lib/{input_uvc,output_http,input_file}.so`. `/usr/local/www` is the repo's copy.

### 3.4 Auto-start
`/etc/systemd/system/picar.service` (copy: `D:\a_ClaudeCowork\PiCar\picar.service`), enabled.
Runs as root in `/home/pi/SunFounder_PiCar-V/remote_control`:
`python3 manage.py runserver --noreload 0.0.0.0:8000`, `Restart=on-failure`.
On start, `views.py` centres steering + camera, stops rear wheels, and starts mjpg_streamer (port 8080) as a child process.
Verified by reboot: HTTP 200 on :8000 about 35 s after `systemctl reboot`.

### 3.5 Power monitor — PREPARED, NOT DEPLOYED (car was off, 2026-11-02)
Files on PC: `D:\a_ClaudeCowork\PiCar\pi\picar_power_monitor.py`, `pi\picar-power.service`.
- Battery voltage is **not measurable** with the stock hardware: the Robot HATS shows it only on 2 LEDs
  (both on > 7.9 V, one 7.4–7.9 V, none < 7.4 V — SunFounder docs, components page). It is not wired to the PCF8591.
- Today: warns on Pi 5V-rail under-voltage via `vcgencmd get_throttled` (bit 0 now, bit 16 since boot).
- Ready for real battery voltage: fit a divider battery+ → 10 kΩ → A0 → 4.7 kΩ → GND on the HAT's analog port A0
  (8.4 V → 2.69 V < 3.3 V ref), then set `BATTERY_DIVIDER_RATIO = 3.128` (calibrate against a multimeter). 8-bit ADC ≈ 0.04 V steps.
- Output: `/run/picar/power.json` every 5 s; `WARNING:` lines in `journalctl -u picar-power` on state changes.
- Deploy: `scp` both files; `sudo install -m 755 picar_power_monitor.py /usr/local/bin/`;
  `sudo install -m 644 picar-power.service /etc/systemd/system/`; `sudo systemctl enable --now picar-power`.
- Not yet: warning on the web page, auto-stop of motors at critical voltage (needs the F2 control API, single owner of the PCA9685).

### 3.6 Dashboard release (commit `e476874` on fork master, 2026-11-02) — PUSHED, NOT YET ON THE CAR
Code: `D:\a_ClaudeCowork\PiCar\code\SunFounder_PiCar-V` (remotes: `origin` = FrankPSch fork, `upstream` = sunfounder).

| File (in `remote_control/remote_control/`) | Role |
|---|---|
| `car.py` | single owner of motors/servos; camera in integer PWM ticks rel. to centre (1 tick ≈ 0.407°, ±221 ticks); `cam_step` 1–100; watchdog 0.6 s |
| `api.py` | `/` dashboard, `/api/state`, `/api/history?minutes=N`, `POST /api/cmd {"cmd":…}`, `/sim/stream` |
| `tracker.py` | colour ball tracking on the Pi, frames from `127.0.0.1:8080/?action=snapshot`, moves camera `cam_step` ticks per frame |
| `telemetry.py` | Pi health every 5 s, 24 h ring buffer |
| `features.py` | toggles: watchdog, telemetry, power_warning, ball_tracking, tracking_drive, auto_update |
| `sim/` | `PICAR_SIM=1`: fake `picar` package + simulated camera world with a moving red ball |
| `views.py` | classic SunFounder page, now at `/classic/`, shares hardware via `car.py` |

Mutable data now in `/home/pi/picar-data/` (outside git): `features.json`, `calibration` (seeded from `driver/config`),
`db.sqlite3`, `update-status.json`. Calibration made on the classic `/cali/` page is written there.

Keys: W/S drive (hold), A/D full lock (hold), Q/E steer ±1° (Shift ±10), R/F speed ±1 (Shift ±10),
arrows camera 1 step (Shift 10 steps), `,`/`.` step −/+1 tick (Shift −/+10), C centre, Space STOP, T tracking.
Mouse: all buttons; arrows repeat while held; Shift-click = 10 steps. Commands send step *counts*; the car multiplies
by its own `cam_step` (fixed a stale-step bug found in UI test).

Simulation test on the PC (Python 3.9 Anaconda venv, Django 2.0, opencv-headless 4.5.5, numpy < 2):
22/22 API checks pass (integer steps, clamps, watchdog stop + keepalive, STOP, toggles persist across restart,
tracking, teach colour). Tracking vs step size in sim (ball ≤ ~87 px/s, 1 tick = 2 px): step 1–2 lags, **step 5 holds the
ball centred (median offset 5 %)**, step 10 fast but overshoots. UI checked at 800×600, 1366×768, phone 375×812; no console errors.
Test scripts in the repo: `tools/sim_test.py [base_url]`, `tools/sim_track.py` (commit `7d69fda`).

Update mechanism: `tools/deploy.sh [branch]` (PC) → `git push` → SSH `sudo tools/picar-update.sh <branch>` on the Pi
(fetch, `checkout --detach`, restart, wait for `/api/state`, roll back on failure, writes `update-status.json`).
`auto_update` toggle enables `picar-update.timer` (every 10 min + 2 min after boot, follows branch `release` — **not created yet**).

**Bootstrap on the car — DONE 2026-11-02 21:28 (Pi time):** `git pull` → `7d69fda`; `~/picar-data/` created;
calibration copied **from the Pi's locally modified `driver/config`** (someone had calibrated: `turning_offset=17,
pan_offset=-5, tilt_offset=-4`, was -21/-23/18), then the repo file reset so the work tree is clean;
`picar-update.{service,timer}` installed (timer disabled); `manage.py migrate` run against `~/picar-data/db.sqlite3`.

**On-car results 2026-11-02:**
- Telemetry, all real: 35.9 °C, `throttled=0x0` (charged batteries), 800 MHz, RAM 27 %, SD 54.8 GB free,
  Wi-Fi `af-net` −46 dBm 24 MBit/s, camera + streamer, mic "USB PnP Sound Device", PCA9685 + PCF8591 on I²C.
- Git tile was empty: the service runs as root, repo owned by pi → "dubious ownership"; Bullseye git 2.30 ignores
  `-c safe.directory` without HOME. Fix `6cf2f85`: run git via `runuser -u pi`.
- `tools/deploy.sh` round trip OK (`811efa5`, `6cf2f85`, `22b2fd8`). **Rollback verified:** a deliberately broken commit on a
  temporary branch was not healthy within 90 s → car went back to the previous commit, deploy reported FAILED; branch deleted.
- Camera self-check (`tools/cam_step_check.py`, phase correlation on snapshots): directions correct (+pan → scene right,
  +tilt → scene down), ≈3–4 px per tick at 640 px. **Servo dead band:** 1-tick steps move only every 2nd time (~8 px jumps);
  2-tick steps move every time (~0.8°). Default `cam_step` now 2 (`22b2fd8`), 1 still selectable.
- Tracker on the Pi 3: ≈ 8 fps at load ≈ 0.5. Not yet tested with a real ball (room dark, no ball in view).
- NOT tested on the car: driving (motors), steering, tracking_drive, the sim_test.py drive/watchdog checks
  (do not run sim_test.py against the car on a table — it drives at up to 100 %).

### 3.7 Feature round 2 (commit `954d9fb`, 2026-11-02) — PiCar-X-style features, PUSHED
Source for the selection: SunFounder PiCar-X docs (python projects list). Taken over (no extra sensors needed):
colour detection (+purple), face detection / "stares at you", QR codes, photo + video recording, bull fight
(= `tracking_drive`), treasure hunt, Vosk offline voice control, voice prompts (espeak). Not possible without sensors:
ultrasonic avoidance, line following, cliff detection. Excluded by owner: LLM/ChatGPT/Ollama, gamepad, Claude speaking.

| Module | Features (toggle names) |
|---|---|
| `tracker.py` (vision loop) | `ball_tracking`, `face_tracking` (Haar), `animal_tracking` (MobileNet-SSD dog/cat/person), `tracking_drive`, `qr_reader`, `markers`, `treasure_hunt`, `stuck_detection` |
| `rooms.py` | marker names → rooms (`~/picar-data/rooms.json`), current room, transitions = room graph, `room_navigation` (EXPERIMENTAL) |
| `media.py` | photo, video `.mjpg` to `~/picar-data/media`, `record_audio` (.wav), `timelapse` (`timelapse_seconds`), `/media/<name>` |
| `audio.py` | one `arecord` capture: `sound_level`, `clap_stop`, `noise_alarm`, `stop_word`, `voice_commands` |
| `events.py` | event log, `push_notifications` (ntfy — needs `ntfy_topic` in `~/picar-data/settings.json`), `voice_prompts` |

Design notes: the three camera-follow modes are mutually exclusive (enabling one disables the others); treasure hunt
overrides them; navigation owns the camera while active. Registry assertions in `features.py`/`tracker.py`.
Stuck detection is skipped in sim (no motion model). Mic device default `plughw:CARD=Device,DEV=0` (setting `mic_device`).
Vosk grammar is English and fixed (`audio.VOICE_COMMANDS`); "stop"/"halt" act on partial results.

Extras need a one-time `tools/install_extras.sh` on the Pi (opencv-data, espeak-ng, vosk 0.3.45 armv7l wheel,
vosk-model-small-en-us-0.15 41 MB, MobileNet-SSD chuanqi305 23 MB → `~/picar-data/models`). URLs checked 200 OK.
PC tools: `tools/record_stream.py` (stream → PC file), `tools/drive_table.py` (speed/steer tables via ArUco; untested).

Sim tests: `tools/sim_test_features.py` 24/24 PASS (exclusivity, missing-model reporting, photo/download/traversal 404,
video, timelapse, QR read, marker 3 → room "kitchen", goto refused when off, scan → approach at speed 25, cancel stops,
treasure colour + score with the red sim ball, mic error reported), `tools/sim_test.py` 22/22 PASS (regression).

On the car (2026-11-05, on the desk, never driven): deployed `954d9fb`, `install_extras.sh` OK, then `b9a7165` (mic fix).
| Check | Result |
|---|---|
| Vision loop speed | ball 8.3 fps, face 7.8, QR on 4.5, markers 8.3, animal 0.7 fps (1.2–1.8 s per frame); no errors |
| Models on the Pi with sample images | Haar found the face in lena.jpg; SSD found dog 0.86, bicycle, car in dog416.png and person 0.9 in lena.jpg |
| Vosk with espeak-ng speech files | stop, forward, camera left, follow ball, photo all correct, ~0.4 s; other speech → `[unk]` |
| Media | photo, .mjpg + .wav recording (5 s, 2 MB video), timelapse: all written |
| Microphone | **Bug fixed:** capture volume boots at 0 % (peak 55 of 32767). `audio.py` now sets 100 % before each capture; room level then about −62 dBFS |

NOT tested on the car: live recognition through the camera (no ball/face/dog in view), clap, noise alarm, spoken words
through the real mic, `tracking_drive`, `room_navigation`, stuck detection, voice prompts, driving.
`CLAP_PEAK`/`NOISE_DBFS` thresholds were chosen without a real measurement.

Remote update: `release` branch at `49577d2`, `auto_update` ON since 2026-11-06 (timer enabled, every 10 min). First update
from `release` (b9a7165 → 49577d2) OK, started by hand via `picar-update.service`, the same unit the timer starts. Timer then fired on its own at 02:05:17: result "up-to-date".
Timer-driven update verified with a real new version: `76633b6` pushed to `release` at 02:27, installed by the timer alone at 02:35:44.
Gotcha: with `auto_update` on, a deploy of `master` alone gets reverted to `release` by the timer. Push both: `git push origin master master:release`.

**Voice, 2026-11-06:**
- False alarm: owner did not say "halt" (logged at 02:04:26, 02:05:12, room −63 dBFS). The fixed word list maps noise to its nearest word. Now a word needs `MIN_SPEECH_DBFS` −45 and `MIN_WORD_CONF` 0.8; rejected words in `audio.rejected` / `rejected_count`. Starting values, measured only with the old mic's room level.
- Hardware change: **Jabra SPEAK 510 USB** replaces the old USB mic (ALSA card `USB`, playback + 16 kHz capture, mic and speaker work at the same time). `events.audio_device()` finds the first USB card; setting `mic_device` overrides it. Old mic's gain fix applies only to card `Device`.
- German + English: `vosk-model-small-de-0.15` in `~/picar-data/models` (92 MB, in `install_extras.sh`), one recogniser per language, `audio.VOICE_PHRASES` (startup assertion against `VOICE_COMMANDS`). Grammar must be sent with `ensure_ascii=False`, or umlaut words are silently dropped.
- Load: python about 106 % of one core with both recognisers + vision, `speech_load` 0.05, 443 MB memory available.
- Owner's live speech on the car (02:36-02:38): rückwärts, los, stopp, stop, links, left, right, straight, camera up recognised. German "bereit" fired on English "straight"/"right", replaced by "startklar" (`release`, not confirmed installed/spoken yet).
- Stuck detection fired for real while driving ("image did not change for 1.5 s") at 02:37:15 and 02:37:25.
- Voice prompts: played through the Jabra; words are ignored while the car speaks. Not confirmed audible by owner yet.

**German steering list, `d9c60f4` (on the car):** `voice_languages` setting, default `de` only (speech_load 0.02); `de,en` re-enables English, applied when the word list next rebuilds. `links`, `rechts`, `geradeaus` act on the partial result, without confidence check, and also fire inside a sentence. Words that drive still need the final result. espeak test of the list: 22 of 28 correct or nearly so; `vorwärts` and `kamera hoch` came back as `[unk]`, `stop` as `stopp`, `links`/`geradeaus`/`bild` with a leading `[unk]`. `kamera oben`/`kamera unten` added untested. Not yet tested with the owner's voice. Stop stays live while the car speaks if it is driving.
`claude_voice` (ON): "hey claude"/"hallo claude" only logs an event and `audio.wake_time`; no conversation behind it yet.
Plan for Claude conversation: PC does Whisper + Claude API + Piper (PC: GTX 1070 8 GB, i7-6850K, 64 GB, D: 77 GB free, C: 12 GB free). In a conversation, sentence words must not steer: exact single phrases only.

**Claude conversation, feasibility 2026-11-06 (nothing built yet):** Vosk free dictation on the Pi 3 with the car software running takes 1.4-2.9 x the sentence length per language (espeak test sentence; 296 MB peak), so two languages in parallel are not real time. Wrong-language text still scored conf 0.71-0.81, so confidence alone is a weak language picker. "claude", "hey", "hallo", "goodbye", "tschüss" are in the vocabularies. Installing the `anthropic` package and Piper was blocked by the session's safety check pending the owner's explicit approval; not installed. `https://api.anthropic.com` is reachable from the car.
**Claude conversation, built 2026-11-06 (`2df4193` on the car, `ee4ec3c` on master + release):**
- Car: `remote_control/conversation.py` (toggle `claude_voice`). "hey claude" -> rising tone -> sentences go to the PC, answer plays on the Jabra; "hey claude, wie geht es dir" in one breath also works (10 s rolling audio). Falling tone after 60 s without speech; low tone if the PC cannot be reached. Status: `audio.conversation`, dashboard mic line, event log.
- Steering during a conversation: exact single list words still steer and are not sent to Claude; words inside a sentence do nothing; no instant partial steering; `stop` stays live while driving, also inside a sentence. While the car speaks, parked: all words muted (echo). Claude can talk and read the status, it cannot move the car.
- PC: `tools/voice_server.py`, start with `tools/voice_server.bat`. Everything else in `D:\a_ClaudeCowork\PiCar\voice` (not in git): `env` venv (faster-whisper 1.2.1, anthropic 0.125.0), `models` (Whisper small, medium), `piper` + 2 voices, `cublas` (NVIDIA `nvidia-cublas-cu12` 12.9.2.10 from PyPI, needed for the GPU: without `cublas64_12.dll` the first transcribe errors and a second one hangs forever).
- Connection: server on PC loopback only + `ssh -R 8766`, so no firewall rule and nobody else on the network can use the API key.
- Settings (PC environment variables): `ANTHROPIC_API_KEY`, `CLAUDE_VOICE_MODEL` (default `claude-opus-5-5`; `echo` = repeats the sentence, no key needed), `CLAUDE_VOICE_EFFORT` (default low), `WHISPER_SIZE` (default small).
- Measured, GTX 1070: Whisper small 0.5-0.8 s, medium 1.5 s for a 2.5-4.5 s sentence (processor: 6.6 s). Piper kept loaded 0.23 s per sentence, restarted each time 1.2 s. Car -> PC -> audio ready on the car: 0.75-0.82 s in echo mode (plus Claude's own time).
- Tested: logic against the running server (`scratchpad/stub4.py`), `tools/sim_test_features.py` all pass, real audio round trip car -> tunnel -> PC -> Jabra with `tools/voice_check.py` (speaker exit code 0; Piper test speech, not a human voice). **Not tested yet:** the Claude API (no key on this PC), the owner saying "hey claude" to the car, audibility, `SPEECH_DBFS` -42 with real voices (room -68 dBFS).

### 3.8 Features by voice (2026-11-14, `f1acff0` on the car, paths moved to D: in `61f4fa6`, pushed)
- Flow: "hey claude" + feature wish → Claude repeats it and offers numbered options → owner says **"go"** (e.g. "Go, Option eins") → `tools/feature_builder.py` runs `claude -p` (Opus, Read/Edit/Write only, no Bash/web) in a git worktree on branch `voice/<time>-<slug>` from `origin/master` → checks → both sim suites (one clean run of three counts) → commit → car says "gebaut, sag deploy" → owner says **"deploy"** → push master + release, `tools/deploy.sh release` → car restarts and announces it. New switch is always OFF; "schalte … ein/aus" toggles any switch by voice (`set_feature` frame), features that drive need one spoken confirmation.
- Gates: the server, not Claude, checks the owner's own words for "go"/"deploy" (`feature_builder.GO`/`DEPLOY`); if Whisper missed it, a second German pass primed with the expected word runs. Piper test: 19/20 before the `goom` fix.
- Checks before commit: protected files unchanged (`deploy.sh`, `picar-update.sh`, `systemd/`, `voice_server.*`, `feature_builder.py`), existing switches and `STOP_WORDS` unchanged, exactly one new switch, default False, used in code. Two fix rounds if a check fails.
- During a conversation "go"/"los" don't drive (they confirm builds); "vorwärts" still drives.
- Car: `conversation.py` sends `X-Car-Features`, polls `GET /news` every 10 s while idle to speak build news.
- PC paths: builder clone `D:\a_ClaudeCowork\PiCar\voice\repo` (never the edited checkout), worktrees + logs `voice\builds`, sim venv `voice\simenv` (Anaconda 3.9.7, Django 2.0.13, opencv-contrib-python-headless 4.5.5.64 for ArUco, numpy 1.26.4; needs `C:\ProgramData\Anaconda3\Library\bin` on PATH), speech files `voice\tmp`. Env `PICAR_WORK_DIR` overrides.
- Billing: `claude -p` inherits `ANTHROPIC_API_KEY` from the voice server, so builds bill the API account. Cost per build not measured.
- 2026-11-14 D: was full (0 MB); work ran from `C:\Users\Frank\PiCar_work`, now in `old_voice_tests_2026-11-14\PiCar_work_C` (with the test leftovers; safe to delete). The Claude app virtualizes `AppData\Local` (`Packages\Claude_*\LocalCache`): don't put files there that other programs must see.
- Tested: builder flow with a stand-in builder against a bare test origin (gates, 4 scenarios, deploy), voice server tool loop with a fake Claude, car news + voice switching in sim, "go"/"deploy" via Piper → Whisper. **Not tested:** real `claude -p` build, real Claude API with these tools, owner's voice, deploy step on the real car.

**Voice server only while the car is on (2026-11-14, `72111ce`, committed, not pushed yet):**
- `tools/voice_launcher.py`, started with Windows by a shortcut that `tools/install_voice_launcher.bat` creates (double-click once in Explorer: the Claude app redirects its own writes to `AppData\Roaming`, so it cannot make the shortcut itself). No window; log `voice\launcher.log`, server output `voice\server.log`.
- Car answers on SSH → it starts the voice server (ready after about 12–16 s). Car gone 2 min → it stops it (`POST /shutdown`, refused while a feature build or deploy runs).
- By voice (switch `claude_voice`): "Claude starten" / "start claude" → rising tone when ready; "Claude beenden" / "Claude stoppen" / "stop claude" → falling tone, stays off until the next car start or "Claude starten". Low tone: PC not reachable or build running. The car reaches the launcher through its own tunnel, `127.0.0.1:8767`.
- Tested: from the car over the real tunnels (status, stop, stays off, start), auto stop with the car unreachable, the phrases in the sim with stand-in servers (9 checks), both sim suites, windowless via `pythonw`. Phrases recognised by the car's Vosk at confidence 1.0 with Piper's voice. **Not tested:** the owner's voice, the shortcut at a real Windows logon.
- Found: `ANTHROPIC_API_KEY` is not set as a user variable (checked 2026-11-14), so Claude answers fail ("Could not resolve authentication method").

### 3.9 Refactor: one command table, modular voice stack (2026-11-14, `ccb1c56`..)
- **New command = one line** in `remote_control/remote_control/commands.py`: `SPOKEN` (phrases de/en → an `ACTIONS` entry + arguments, kind COMMAND/STOP/WAKE/SYSTEM, `fast`), and if the car cannot do it yet one `Action`. Web API, word recogniser (grammar), "Hilfe"/"help" (read out by group) and Claude (`run_command`, only actions with `moves=False`) all read the table. Add the phrase's expected effect to `tests/test_commands.py`, or its table check fails.
- Car modules: `alsa` (USB devices, mic gain), `speaker` (one sound at a time, 48 kHz, tones), `voice_protocol` (ports, headers, frames — shared with the PC), `voice_settings` (thresholds, languages), `voice_link` (all calls to the PC), `word_recognizer` (Vosk; reports phrase words missing from the vocabulary as `audio.missing_words`), `listeners` (clap, noise), `audio` (capture only), `conversation`, `events` (log only), `api` (web + "Ich bin bereit" on start).
- PC modules: `pc_settings`, `speech_to_text` (Whisper), `text_to_speech` (Piper, now locked per process), `claude_chat` (history, tools), `voice_server` (HTTP), `voice_launcher`, `car_tunnel`, `feature_builder` (checks the stop phrases in commands.py).
- **Update 2026-11-15 (`3817dac`):** the phrase table moved to `vocabulary.py` (no hardware: `SPOKEN`, `EXPLANATIONS`, grammar), loudness to `speech_level.py`; `commands.py` keeps `ACTIONS`. A new spoken command = one `Spoken` line + one `EXPLANATIONS` entry in `vocabulary.py`. Removed German "stop" (next to "stopp") and English "centre" (next to "center"): two spellings of one sound split Vosk's confidence to 0.5–0.65, below 0.8, so the car never accepted them.
- **Learning (`d455656`, no sudo):** say **"Befehle lernen"** or run `python3 tools/voice_check.py learn [--group drive]` on the car. The running service (`learning.py`) says each phrase + what it does, beeps, records 3 s, answers "verstanden"/"zu leise"/"falsch verstanden: …" (3 tries). Meanwhile only stop and "Lernen beenden" act. Ends: "Lernen beenden", Ctrl-C, last phrase, or 3 quiet tries. Every try kept: `/home/pi/picar-data/voice_check/<parked|driving>/<language>/<phrase>.<n>.wav` + `results.jsonl`. `replay` re-checks them (car, or PC: copy to `voice
ecordings` → also `tests/pc/test_voice_check.py`). `checklist` → `PiCar_Voice_Commands.md`.
- Tests: `python tools/run_tests.py` (voice env) = 37 car tests (sub-cases for every phrase in both languages and every web command, the voice rules) + 15 PC tests (echo mode) + both sim suites (one clean run of three). All green before and after the refactor.

To ship: `git push origin master:release`; the car restarts the software when it picks it up, so don't push while it drives.
`tools/car_check.py` = the non-driving check used above.
Camera note: `cam_set` args are `pan`/`tilt`; with tilt +90 the camera looks at the monitors on the desk.
Sim world now contains ArUco marker 3 and a QR code "picar-sim-qr".

## 4. Verification commands (run from the PC, Git Bash)

```bash
S="ssh -i $HOME/.ssh/picar_ed25519 -o BatchMode=yes pi@192.168.178.131"
$S 'systemctl is-active picar; pgrep -a mjpg_streamer; vcgencmd get_throttled; vcgencmd measure_temp'
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.178.131:8000/
curl -s -o snap.jpg -w "%{http_code} %{size_download}\n" "http://192.168.178.131:8080/?action=snapshot"
$S 'sudo i2cdetect -y 1'          # expect 40, 48, 70
$S 'journalctl -u picar -n 50 --no-pager -o cat'
```

## 5. HTTP control API (from `views.py`)

All `GET http://192.168.178.131:8000/run/?…` — synchronous, returns the page HTML.

| Query | Effect |
|---|---|
| `action=forward` / `backward` / `stop` | rear wheels at current `SPEED`; keeps running until `stop` (**no watchdog**) |
| `speed=<0..100>` | integer speed; applied at once if moving |
| `action=fwleft` / `fwright` / `fwstraight` / `fwready` | steering presets |
| `action=fwturn:<angle>` | steering to an integer angle (servo degrees, 90 ≈ straight before calibration offset) |
| `action=camleft` / `camright` (±40) / `camup` / `camdown` (±20) / `camready` | camera pan/tilt steps |
| `/cali/?action=…` | calibration (`fwcali`, `fwcalileft`, `camcali…`, `bwcali…`, `…ok` writes `driver/config`) |

`camera.Camera.to_position(pan, tilt)` exists in Python but is **not** exposed over HTTP.

## 6. Known gotchas

- **Web buttons are touch-only.** Arrow buttons use `ontouchstart`; mouse clicks do nothing. On a PC use keys W/A/S/D, arrow keys (camera), 1–5 (speed).
- **No dead-man stop.** `forward` keeps driving if the browser/network drops before `stop`.
- **Tracked runtime files** in the repo: `remote_control/remote_control/driver/config` (calibration), `remote_control/db.sqlite3`, `ball_track/config`. Calibrating makes the Pi's work tree dirty → `git pull` fails or clobbers calibration. Fix in the fork before the first update.
- Django warns `1 unapplied migration (auth)` — harmless; not applied on purpose (would change tracked `db.sqlite3`).
- `pkill -f "manage.py runserver"` over SSH kills the SSH command itself (pattern matches its own command line). Use `pkill -f "[m]anage.py"` or `systemctl`.
- Windows assigns the drive letter to the Linux partition of a freshly flashed card and none to `bootfs`; always **Cancel** the "format disk" prompt. Assigning a letter needs admin.
- The Claude Code terminal panel cancels commands that wait for interactive input (Ctrl-C after 2 s). Use `Start-Process powershell -File <script>` to open a separate window for password prompts.
- Image choice: Stretch 2017 does not boot on this 3B+; Bookworm+ breaks `sudo pip3 install` (PEP 668); the 32-bit mjpg binaries rule out arm64.
- Bullseye is past Debian LTS (ended Aug 2026, from memory) — keep the car off the internet, no FRITZ!Box port forwarding.

## 7. History

| When (local) | What |
|---|---|
| 2026-11-02 | Handover from cloud session read (artifact "PiCar-V Handover"); copy in `PiCar-V_Handover_2026-11-02.md` |
| 2026-11-02 | Images downloaded, SHA256 verified (see README.txt) |
| 2026-11-02 | Card flashed (Imager, no settings), boot files added by hand, first boot, key installed |
| 2026-11-02 | Dependencies installed, I²C on, server verified (HTTP 200), `picar.service` enabled, reboot-tested |
| 2026-11-02 | mjpg-streamer rebuilt from source; camera stream + snapshot verified |
| 2026-11-02 | Found: controls touch-only on web page; under-voltage flag `0x50000` |
| 2026-11-02 | Power monitor prepared (not deployed). Dashboard/API/tracking/telemetry/toggles/update built, sim-tested, pushed as `e476874` |
| 2026-11-02 | Dashboard installed on the car; telemetry/deploy/rollback/camera steps verified; now running `22b2fd8` |
| 2026-11-14 | Features by voice built and pushed (`f1acff0`); car auto-updated at 16:47. Voice work dir moved from C: back to D: |
| 2026-11-15 | Refactor + voice check pushed (`3817dac`, 7 commits), car updated at 06:33. Vosk 0.3.45 + car models on the PC (`voiceosk_models`) for `tests/pc/test_voice_check.py` |
| 2026-11-15 | Learning in the car service pushed (`d455656`), car updated; real session with the owner's voice still to do |
| 2026-11-15 | First learning session (06:50-06:57, German, parked): 39 tries, 32 of 33 phrases understood; "vorwärts" 0/3 (spoken late/quietly). Recordings copied to `voice
ecordings`, now a PC regression test (passes) |
| 2026-11-15 | `811819d` pushed, car updated: voice server crashed when the launcher started it (ssl DLLs missing from PATH since the refactor) - fixed in `pc_settings`; noise word before a phrase ("oben claude starten") now ignored except for driving phrases; "kamera mitte" added |
| 2026-11-15 | Voice wishes built and deployed by voice: pause_voice_commands (de8c588), dashboard_pause_switch (0f869c5), mbrola_voice (d8210fd), mbrola_check (f84098a). Local, not pushed: camera_center reply, wish list (now project/generated/WISHES.md), UTF-8 server log, natural voice (397 Piper recordings, 31 MB, already copied to the car's `picar-data/voice_cache`), all settings in the dashboard, deploy.sh wait fix |
| 2026-11-15 | Moved into the repo as project/SETUP_LOG.md (was D:\a_ClaudeCowork\PiCar\PiCar_Setup_Log.md). Collaboration phases 1-4: see CLAUDE.md and project/WORKPLAN.md |
