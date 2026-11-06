'''
Event log shown on the dashboard, plus optional phone push and voice prompts.
- Push (feature push_notifications): POST to https://ntfy.sh/<topic>. Nothing is sent until the owner
  puts {"ntfy_topic": "..."} into DATA_DIR/settings.json.
- Voice prompts (feature voice_prompts): espeak-ng on the first USB speaker (e.g. Jabra), else the Pi's default output.
'''
import collections
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

from . import features

SETTINGS_FILE = os.path.join(features.DATA_DIR, 'settings.json')
log = collections.deque(maxlen=200)
_speech_lock = threading.Lock()
speaking = threading.Event()
INTERNAL_AUDIO_CARDS = ('Headphones', 'vc4hdmi')
PROMPT_ECHO_S = 0.5
SPEAKER_DRAIN_S = 120


def settings():
    try:
        with open(SETTINGS_FILE) as settings_file:
            return json.load(settings_file)
    except (OSError, ValueError):
        return {}


EDITABLE_SETTINGS = {'timelapse_seconds': int, 'ntfy_topic': str, 'mic_device': str, 'voice_languages': str}


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
        speaking.set()
        try:
            speaker = audio_device('aplay')
            voice = subprocess.run(['espeak-ng', '-v', 'en', '-s', '150', '--stdout', text], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
            subprocess.run(['aplay', '-q'] + (['-D', speaker] if speaker else []), input=voice.stdout, stderr=subprocess.DEVNULL, timeout=15)
            time.sleep(PROMPT_ECHO_S)
        except (OSError, subprocess.SubprocessError) as error:
            print('event: voice prompt failed: %s' % error)
        finally:
            speaking.clear()


def open_speaker(sample_rate):
    '''Raw 16-bit mono audio for write_speaker. Holds the speech lock and marks the car as speaking until close_speaker.'''
    _speech_lock.acquire()
    speaking.set()
    speaker = audio_device('aplay')
    try:
        return subprocess.Popen(['aplay', '-q', '-t', 'raw', '-f', 'S16_LE', '-c', '1', '-r', str(sample_rate)] + (['-D', speaker] if speaker else []),
                                stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as error:
        print('event: no speaker: %s' % error)
        return None


def write_speaker(player, audio):
    if player is not None and player.poll() is None:
        try:
            player.stdin.write(audio)
            player.stdin.flush()
        except OSError as error:
            print('event: speaker failed: %s' % error)


def close_speaker(player):
    try:
        if player is not None:
            try:
                player.stdin.close()
            except OSError:
                pass
            player.wait(timeout=SPEAKER_DRAIN_S)
            time.sleep(PROMPT_ECHO_S)
    except subprocess.TimeoutExpired:
        player.kill()
    finally:
        speaking.clear()
        _speech_lock.release()


def audio_device(alsa_list_tool):
    '''First USB audio device: alsa_list_tool 'arecord' = microphone, 'aplay' = speaker. None if there is none.'''
    try:
        listing = subprocess.run([alsa_list_tool, '-l'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for card, device in re.findall(r'^card \d+: (\w+) \[.*\], device (\d+):', listing, re.MULTILINE):
        if card not in INTERNAL_AUDIO_CARDS:
            return 'plughw:CARD=%s,DEV=%s' % (card, device)
    return None


def recent(count=40):
    return list(log)[-count:]
