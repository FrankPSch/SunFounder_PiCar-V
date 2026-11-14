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

from . import features, speaker

SETTINGS_FILE = os.path.join(features.DATA_DIR, 'settings.json')
EDITABLE_SETTINGS = {'timelapse_seconds': int, 'ntfy_topic': str, 'mic_device': str, 'voice_languages': str}
LOG_LENGTH = 200
PUSH_TIMEOUT_S = 10
log = collections.deque(maxlen=LOG_LENGTH)


def settings():
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
    os.makedirs(features.DATA_DIR, exist_ok=True)
    with open(SETTINGS_FILE, 'w') as settings_file:
        json.dump(current, settings_file, indent=2, sort_keys=True)


def add(source, text, push=False, say=None):
    entry = {'time': time.strftime('%H:%M:%S'), 'source': source, 'text': text}
    log.append(entry)
    print('event: [%s] %s' % (source, text))
    if push and features.enabled('push_notifications'):
        threading.Thread(target=_push, args=(text,), daemon=True).start()
    if say and features.enabled('voice_prompts'):
        speaker.say_later(say)
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
