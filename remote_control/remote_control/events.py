'''
Event log shown on the dashboard, plus optional phone push and voice prompts.
- Push (feature push_notifications): POST to https://ntfy.sh/<topic>. Nothing is sent until the owner
  puts {"ntfy_topic": "..."} into DATA_DIR/settings.json.
- Voice prompts (feature voice_prompts): espeak-ng on the first USB speaker (e.g. Jabra), else the Pi's default output.
'''
import audioop
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
# The Jabra SPEAK 510 plays 16 kHz audio 3x too fast while its mic records (2 s played in 0.77 s, measured
# 2026-11-14), 48 kHz at the right speed: write_speaker converts to this rate.
PLAYBACK_RATE = 48000


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


def _say(text, language='en'):
    with _speech_lock:
        speaking.set()
        try:
            speaker = audio_device('aplay')
            voice = subprocess.run(['espeak-ng', '-v', language, '-s', '150', '--stdout', text], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
            subprocess.run(['aplay', '-q'] + (['-D', speaker] if speaker else []), input=voice.stdout, stderr=subprocess.DEVNULL, timeout=15)
            time.sleep(PROMPT_ECHO_S)
        except (OSError, subprocess.SubprocessError) as error:
            print('event: voice prompt failed: %s' % error)
        finally:
            speaking.clear()


class _Player(object):
    '''aplay at PLAYBACK_RATE, fed audio converted from the caller's rate.'''
    def __init__(self, process, sample_rate):
        self.process, self.sample_rate, self.convert_state = process, sample_rate, None
        self.stdin, self.poll, self.wait, self.kill = process.stdin, process.poll, process.wait, process.kill


def open_speaker(sample_rate):
    '''Raw 16-bit mono audio for write_speaker. Holds the speech lock and marks the car as speaking until close_speaker.'''
    _speech_lock.acquire()
    speaking.set()
    speaker = audio_device('aplay')
    try:
        process = subprocess.Popen(['aplay', '-q', '-t', 'raw', '-f', 'S16_LE', '-c', '1', '-r', str(PLAYBACK_RATE)] + (['-D', speaker] if speaker else []),
                                   stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return _Player(process, sample_rate)
    except OSError as error:
        print('event: no speaker: %s' % error)
        return None


def write_speaker(player, audio):
    if player is not None and player.poll() is None:
        try:
            if player.sample_rate != PLAYBACK_RATE:
                audio, player.convert_state = audioop.ratecv(audio, 2, 1, player.sample_rate, PLAYBACK_RATE, player.convert_state)
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


# Every start of the car software (boot, restart, update) says so; the software loads this module once at startup.
STARTUP_MESSAGE = 'Ich bin bereit.'
if not os.environ.get('PICAR_SIM'):
    threading.Thread(target=_say, args=(STARTUP_MESSAGE, 'de'), name='startup-message', daemon=True).start()
