'''
Event log shown on the dashboard, the owner's settings file, plus optional phone push and voice prompts.
- Push (feature push_notifications): POST to https://ntfy.sh/<topic>. Nothing is sent until the owner
  puts {"ntfy_topic": "..."} into DATA_DIR/settings.json.
- Voice prompts (feature voice_prompts): add(..., say=text) speaks it (speaker.py).
'''
import collections
import json
import os
import threading
import time
import urllib.request

from . import features, speaker, spoken_texts

SETTINGS_FILE = os.path.join(features.DATA_DIR, 'settings.json')
EDITABLE_SETTINGS = {'timelapse_seconds': int, 'ntfy_topic': str, 'mic_device': str, 'voice_languages': str}
SETTING_HELP = {'timelapse_seconds': 'seconds between timelapse photos',
                'ntfy_topic': 'ntfy.sh topic for push_notifications',
                'mic_device': 'ALSA microphone, e.g. plughw:CARD=USB,DEV=0; empty = first USB microphone',
                'voice_languages': 'de, en or de,en: the languages the car listens for and Claude may answer in'}
assert set(SETTING_HELP) == set(EDITABLE_SETTINGS), 'SETTING_HELP and EDITABLE_SETTINGS differ'
LOG_LENGTH = 200
PUSH_TIMEOUT_S = 10
log = collections.deque(maxlen=LOG_LENGTH)


def settings():
    if features.enabled('single_config_file'):
        return features.config_section('settings')
    try:
        with open(SETTINGS_FILE) as settings_file:
            return json.load(settings_file)
    except (OSError, ValueError):
        return {}


def update_setting(name, value):
    if name not in EDITABLE_SETTINGS:
        raise KeyError('unknown setting %r' % name)
    current = settings()
    current[name] = EDITABLE_SETTINGS[name](value)
    if features.enabled('single_config_file'):
        features.save_config_section('settings', current)
    else:
        features.write_json(SETTINGS_FILE, current)


def add(source, text, push=False, say=None):
    entry = {'time': time.strftime('%H:%M:%S'), 'source': source, 'text': text}
    log.append(entry)
    print('event: [%s] %s' % (source, text))
    if push and features.enabled('push_notifications'):
        threading.Thread(target=_push, args=(text,), daemon=True).start()
    if say and features.enabled('voice_prompts'):
        speaker.say_later(say, spoken_texts.EVENT_LANGUAGE)
    return entry


def _push(text):
    topic = settings().get('ntfy_topic')
    if not topic:
        log.append({'time': time.strftime('%H:%M:%S'), 'source': 'push', 'text': 'no ntfy_topic in settings.json - not sent'})
        return
    try:
        urllib.request.urlopen(urllib.request.Request('https://ntfy.sh/' + topic, data=text.encode('utf-8'),
                                                      headers={'Title': 'PiCar'}), timeout=PUSH_TIMEOUT_S)
    except OSError as error:
        print('event: push failed: %s' % error)


def recent(count=40):
    return list(log)[-count:]
