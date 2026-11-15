'''
Feature toggles. Every optional feature has one switch, stored in DATA_DIR/features.json
(outside git, so updates never overwrite it). The dashboard flips them at runtime.

FEATURES is the single registry. The JSON file is checked against it at startup:
unknown names are reported and dropped, missing names get their default.

Switch single_config_file: switches and settings in one file, DATA_DIR/picar.json {"version", "features", "settings"},
filled from features.json and settings.json when it is switched on (both stay, for switching back). The calibration
stays its own file: the SunFounder driver library reads it directly.
Switch data_files_as_pi: the service runs as root, so its files belong to root and pi (SSH, the PC tools) cannot
change them without sudo; with it on, every file written through write_json() / give_to_pi(), and once all of
DATA_DIR, belong to the owner of DATA_DIR (pi). The twin writes the owners down instead (sim/hardware.py).
'''
import json
import os
import threading

from .sim import hardware

DATA_DIR = os.environ.get('PICAR_DATA', '/home/pi/picar-data')
FEATURES_FILE = os.path.join(DATA_DIR, 'features.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'picar.json')
CONFIG_VERSION = 1
CAR_OWNER = 'pi'

FEATURES = {
    # safety and system
    'watchdog':           (True,  'Stop the motors when no drive command arrives for 0.6 s'),
    'stuck_detection':    (True,  'Stop when the camera image stops changing while driving (obstacle?)'),
    'telemetry':          (True,  'Sample Pi health every 5 s for the dashboard'),
    'power_warning':      (True,  'Warn on Pi under-voltage (vcgencmd get_throttled)'),
    'auto_update':        (False, 'systemd timer pulls the release branch and restarts'),
    # camera follow (only one of the three is on at a time)
    'ball_tracking':      (False, 'Camera follows a coloured ball in integer tick steps'),
    'face_tracking':      (False, 'Camera follows a face ("stares at you", Haar cascade)'),
    'animal_tracking':    (False, 'Camera follows a dog, cat or person (MobileNet-SSD, ~1-2 fps)'),
    'tracking_drive':     (False, 'Bull fight: car drives after the followed target'),
    # vision extras
    'qr_reader':          (False, 'Read QR codes in the camera image'),
    'markers':            (False, 'Detect ArUco room markers (4x4, ids 0-49)'),
    'treasure_hunt':      (False, 'Game: car names a colour, show it an object of that colour'),
    'room_navigation':    (False, 'EXPERIMENTAL: drive to a named room marker, camera only'),
    # media
    'record_audio':       (False, 'Record microphone sound next to each video'),
    'timelapse':          (False, 'Save a photo every N seconds (settings: timelapse_seconds)'),
    # microphone
    'sound_level':        (False, 'Measure the microphone level for the dashboard'),
    'clap_stop':          (False, 'Double clap stops the motors'),
    'noise_alarm':        (False, 'Loud noise -> photo + event (+ push if enabled)'),
    'stop_word':          (False, 'Saying "stop" stops the motors (Vosk, offline)'),
    'voice_commands':     (False, 'Spoken commands like "links", "kamera hoch" (Vosk, offline)'),
    'claude_voice':       (False, 'Saying "hey Claude" starts a conversation'),
    'pause_voice_commands': (False, 'No spoken commands while talking to Claude (stop stays live); "danke claude" ends the talk'),
    'dashboard_pause_switch': (False, 'Show the pause_voice_commands switch next to the voice switches in the dashboard'),
    # output
    'voice_prompts':      (False, 'Speak events with espeak-ng (needs a speaker)'),
    'push_notifications': (False, 'Send alarms to ntfy.sh (needs ntfy_topic in settings.json)'),
    'natural_voice':      (True,  'Speak with the natural Piper voice recorded on the PC (tools/prerender_voice.py), else espeak-ng'),
    # data (architecture backlog 2026-11-15: each switch is the new way, off = the old way)
    'single_config_file': (False, 'Switches and settings in one file picar.json (with a version) instead of features.json + settings.json'),
    'data_files_as_pi':   (False, 'Data files in picar-data belong to pi, not root, so SSH and the PC tools can change them'),
    'disk_warning':       (False, 'Warn on the dashboard and to Claude when the SD card is 80 % full, with the biggest folders'),
    'detector_registry':  (False, 'Camera detectors run from the registry (one module each in detectors/) instead of the old fixed order'),
    'dashboard_calibration': (False, 'Calibrate the camera and the steering on the dashboard (the old page /cali/ stays for the rear wheels)'),
    'state_sections':     (False, 'Dashboard polls read the slow parts of the car state (files, folders) at most every 3 s: less CPU on the Pi'),
    'service_list':       (False, 'Show the car\'s long-running loops (watchdog, microphone, vision, ...) with their state on the dashboard'),
}
TRACKING_FEATURES = ('ball_tracking', 'face_tracking', 'animal_tracking')

_lock = threading.Lock()
_listeners = []
_state = {}


def _read(path, empty):
    try:
        with open(path) as source:
            return json.load(source)
    except (OSError, ValueError):
        return empty


def give_to_pi(path):
    '''With data_files_as_pi on: path belongs to the owner of DATA_DIR (pi) from now on.'''
    if not _state.get('data_files_as_pi'):
        return
    if hardware.ACTIVE:
        hardware.chown(path, CAR_OWNER)
    elif hasattr(os, 'geteuid') and os.geteuid() == 0:
        owner = os.stat(DATA_DIR)
        os.chown(path, owner.st_uid, owner.st_gid)


def write_json(path, data):
    '''Atomic: a cut-off write never leaves half a file.'''
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as target:
        json.dump(data, target, indent=2, sort_keys=True)
    os.replace(path + '.tmp', path)
    give_to_pi(path)


def config_section(name):
    '''A section of picar.json ({} if there is none yet).'''
    return dict(_read(CONFIG_FILE, {}).get(name, {}))


def save_config_section(name, values):
    config = _read(CONFIG_FILE, {})
    config.update({'version': CONFIG_VERSION, name: values})
    write_json(CONFIG_FILE, config)


def _load():
    config_features = _read(CONFIG_FILE, {}).get('features', {})
    stored = config_features if config_features.get('single_config_file') else _read(FEATURES_FILE, {})
    unknown_names = sorted(set(stored) - set(FEATURES))
    if unknown_names:
        print('features: ignoring unknown names in %s: %s' % (FEATURES_FILE, unknown_names))
    return {name: bool(stored.get(name, default)) for name, (default, _) in FEATURES.items()}


def _save():
    if _state.get('single_config_file') or os.path.exists(CONFIG_FILE):
        save_config_section('features', dict(_state))      # switched off: picar.json says so, features.json is used again
    if not _state.get('single_config_file'):
        write_json(FEATURES_FILE, _state)


def _single_config_switched(name, is_on):
    '''On the first time: the settings go into picar.json too (settings.json stays). Off: they go back to settings.json.'''
    settings_file = os.path.join(DATA_DIR, 'settings.json')
    if name == 'single_config_file' and is_on and not config_section('settings'):
        save_config_section('settings', _read(settings_file, {}))
    elif name == 'single_config_file' and not is_on and config_section('settings'):
        write_json(settings_file, config_section('settings'))


def _all_files_to_pi(name, is_on):
    if name == 'data_files_as_pi' and is_on:
        for folder, folders, files in os.walk(DATA_DIR):
            for item in folders + files:
                give_to_pi(os.path.join(folder, item))


def enabled(name):
    return _state[name]


def set_enabled(name, is_on):
    if name not in FEATURES:
        raise KeyError('unknown feature %r' % name)
    with _lock:
        _state[name] = bool(is_on)
        if is_on and name in TRACKING_FEATURES:
            for other in TRACKING_FEATURES:
                if other != name:
                    _state[other] = False
        _save()
    print('features: %s -> %s' % (name, 'on' if is_on else 'off'))
    for listener in _listeners:
        listener(name, bool(is_on))


def on_change(listener):
    _listeners.append(listener)


def describe():
    return [{'name': name, 'on': _state[name], 'help': help_text}
            for name, (_, help_text) in FEATURES.items()]


_state.update(_load())
_save()
on_change(_single_config_switched)
on_change(_all_files_to_pi)
assert set(_state) == set(FEATURES), 'feature registry and state diverged'
assert set(TRACKING_FEATURES) <= set(FEATURES), 'TRACKING_FEATURES names a feature missing from FEATURES'
