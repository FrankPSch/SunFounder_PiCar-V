'''
Feature toggles. Every optional feature has one switch, stored in DATA_DIR/features.json
(outside git, so updates never overwrite it). The dashboard flips them at runtime.

FEATURES is the single registry. The JSON file is checked against it at startup:
unknown names are reported and dropped, missing names get their default.
'''
import json
import os
import threading

DATA_DIR = os.environ.get('PICAR_DATA', '/home/pi/picar-data')
FEATURES_FILE = os.path.join(DATA_DIR, 'features.json')

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
    'voice_commands':     (False, 'Spoken commands like "forward", "camera left" (Vosk, offline)'),
    # output
    'voice_prompts':      (False, 'Speak events with espeak-ng (needs a speaker)'),
    'push_notifications': (False, 'Send alarms to ntfy.sh (needs ntfy_topic in settings.json)'),
}
TRACKING_FEATURES = ('ball_tracking', 'face_tracking', 'animal_tracking')

_lock = threading.Lock()
_listeners = []
_state = {}


def _load():
    stored = {}
    if os.path.exists(FEATURES_FILE):
        with open(FEATURES_FILE) as features_file:
            stored = json.load(features_file)
    unknown_names = sorted(set(stored) - set(FEATURES))
    if unknown_names:
        print('features: ignoring unknown names in %s: %s' % (FEATURES_FILE, unknown_names))
    return {name: bool(stored.get(name, default)) for name, (default, _) in FEATURES.items()}


def _save():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FEATURES_FILE + '.tmp', 'w') as features_file:
        json.dump(_state, features_file, indent=2, sort_keys=True)
    os.replace(FEATURES_FILE + '.tmp', FEATURES_FILE)


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
assert set(_state) == set(FEATURES), 'feature registry and state diverged'
assert set(TRACKING_FEATURES) <= set(FEATURES), 'TRACKING_FEATURES names a feature missing from FEATURES'
