'''
Event log shown on the dashboard, plus optional phone push and voice prompts.
- Push (feature push_notifications): POST to https://ntfy.sh/<topic>. Nothing is sent until the owner
  puts {"ntfy_topic": "..."} into DATA_DIR/settings.json.
- Voice prompts (feature voice_prompts): espeak-ng on the Pi's audio output (needs a speaker).
'''
import collections
import json
import os
import subprocess
import threading
import time
import urllib.request

from . import features

SETTINGS_FILE = os.path.join(features.DATA_DIR, 'settings.json')
log = collections.deque(maxlen=200)
_speech_lock = threading.Lock()


def settings():
    try:
        with open(SETTINGS_FILE) as settings_file:
            return json.load(settings_file)
    except (OSError, ValueError):
        return {}


EDITABLE_SETTINGS = {'timelapse_seconds': int, 'ntfy_topic': str, 'mic_device': str}


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
        threading.Thread(target=_say, args=(say,), daemon=True).start()
    return entry


def _push(text):
    topic = settings().get('ntfy_topic')
    if not topic:
        log.append({'time': time.strftime('%H:%M:%S'), 'source': 'push', 'text': 'no ntfy_topic in settings.json - not sent'})
        return
    try:
        urllib.request.urlopen(urllib.request.Request('https://ntfy.sh/' + topic, data=text.encode('utf-8'),
                                                      headers={'Title': 'PiCar'}), timeout=10)
    except OSError as error:
        print('event: push failed: %s' % error)


def _say(text):
    with _speech_lock:
        try:
            subprocess.run(['espeak-ng', '-v', 'en', '-s', '150', text], timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as error:
            print('event: voice prompt failed: %s' % error)


def recent(count=40):
    return list(log)[-count:]
