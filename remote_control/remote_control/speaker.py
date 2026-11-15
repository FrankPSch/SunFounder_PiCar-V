'''The car's speaker: one sound at a time (speech lock), and the flag `speaking` that mutes the word recogniser
meanwhile (the car must not obey its own voice).
  say(text, language)      espeak-ng, blocking; say_later() in a thread
  play_tones(Tone.START)   short beeps
  open_stream(rate)        raw 16-bit mono audio from the PC, written in pieces, then close()
The Jabra SPEAK 510 plays 16 kHz audio 3x too fast while its mic records (2 s played in 0.77 s, measured 2026-11-14),
48 kHz at the right speed: everything goes out at PLAYBACK_RATE.
Feature mbrola_voice: say() uses an MBROLA voice (softer, e.g. mb-de5) when it is installed (tools/install_extras.sh),
its 16 kHz output resampled to PLAYBACK_RATE; without the voice files, plain espeak-ng as before.'''
import audioop
import enum
import math
import os
import struct
import subprocess
import threading
import time

from . import alsa, features

PLAYBACK_RATE = 48000
SAMPLE_WIDTH = 2
ECHO_S = 0.5                # the room still rings after the speaker stops
DRAIN_TIMEOUT_S = 120
SAY_TIMEOUT_S = 15
ESPEAK_WORDS_PER_MINUTE = '150'
TONE_NOTE_S = 0.12
TONE_AMPLITUDE = 9000
MBROLA_DIR = '/usr/share/mbrola'
MBROLA_VOICES = {'de': ('de5', 'de7'), 'en': ('en1',)}  # first installed one wins
WAV_HEADER_BYTES = 44                                    # espeak-ng --stdout: plain 44-byte header, rate at bytes 24-27

speaking = threading.Event()
_lock = threading.Lock()


class Tone(enum.Enum):
    START = (660, 880)       # listening / ready
    END = (880, 660)         # stopped listening / off
    FAILED = (220, 220)      # PC not reachable, refused


def _aplay(rate):
    device = alsa.usb_device('aplay')
    return ['aplay', '-q', '-t', 'raw', '-f', 'S16_LE', '-c', '1', '-r', str(rate)] + (['-D', device] if device else [])


def mbrola_voice(language):
    '''espeak-ng name of the installed MBROLA voice for this language, None when mbrola_voice is off or none is installed.'''
    if not features.enabled('mbrola_voice'):
        return None
    return next(('mb-' + name for name in MBROLA_VOICES.get(language, ()) if os.path.isfile(os.path.join(MBROLA_DIR, name, name))), None)


def describe():
    if not features.enabled('mbrola_voice'):
        return {'mbrola': 'off', 'voices': {}}
    voices = {language: mbrola_voice(language) for language in MBROLA_VOICES}
    installed = {language: name for language, name in voices.items() if name}
    return {'mbrola': 'ok' if installed else 'missing in %s (tools/install_extras.sh)' % MBROLA_DIR, 'voices': installed}


def _say_mbrola(voice_name, text):
    '''True when the MBROLA voice spoke; False lets say() fall back to plain espeak-ng.'''
    wav = subprocess.run(['espeak-ng', '-v', voice_name, '-s', ESPEAK_WORDS_PER_MINUTE, '--stdout', text],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=SAY_TIMEOUT_S).stdout
    if len(wav) <= WAV_HEADER_BYTES:
        print('speaker: %s gave no audio, plain espeak-ng instead' % voice_name)
        return False
    wav_rate = struct.unpack('<I', wav[24:28])[0]
    samples = wav[WAV_HEADER_BYTES:]
    audio, _ = audioop.ratecv(samples[:len(samples) - len(samples) % SAMPLE_WIDTH], SAMPLE_WIDTH, 1, wav_rate, PLAYBACK_RATE, None)
    subprocess.run(_aplay(PLAYBACK_RATE), input=audio, stderr=subprocess.DEVNULL, timeout=SAY_TIMEOUT_S)
    return True


def say(text, language='en'):
    with _lock:
        speaking.set()
        try:
            voice_name = mbrola_voice(language)
            if voice_name and _say_mbrola(voice_name, text):
                time.sleep(ECHO_S)
                return
            voice = subprocess.run(['espeak-ng', '-v', language, '-s', ESPEAK_WORDS_PER_MINUTE, '--stdout', text],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=SAY_TIMEOUT_S)
            device = alsa.usb_device('aplay')
            subprocess.run(['aplay', '-q'] + (['-D', device] if device else []), input=voice.stdout, stderr=subprocess.DEVNULL, timeout=SAY_TIMEOUT_S)
            time.sleep(ECHO_S)
        except (OSError, subprocess.SubprocessError) as error:
            print('speaker: say failed: %s' % error)
        finally:
            speaking.clear()


def say_later(text, language='en'):
    threading.Thread(target=say, args=(text, language), name='say', daemon=True).start()


def _tone_audio(tone):
    samples = []
    for frequency in tone.value:
        for index in range(int(TONE_NOTE_S * PLAYBACK_RATE)):
            samples.append(int(TONE_AMPLITUDE * math.sin(2 * math.pi * frequency * index / PLAYBACK_RATE)))
    return struct.pack('<%dh' % len(samples), *samples)


def play_tones(*tones):
    stream = open_stream(PLAYBACK_RATE)
    try:
        for tone in tones:
            stream.write(_tone_audio(tone))
    finally:
        stream.close()


def play_tones_later(*tones):
    threading.Thread(target=play_tones, args=tones, name='tones', daemon=True).start()


class Stream(object):
    '''Holds the speaker from open_stream until close: converts each piece from its rate to PLAYBACK_RATE.'''
    def __init__(self, rate):
        self._rate, self._convert_state = rate, None
        try:
            self._player = subprocess.Popen(_aplay(PLAYBACK_RATE), stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as error:
            print('speaker: none: %s' % error)
            self._player = None

    def write(self, audio):
        if self._player is None or self._player.poll() is not None:
            return
        if self._rate != PLAYBACK_RATE:
            audio, self._convert_state = audioop.ratecv(audio, SAMPLE_WIDTH, 1, self._rate, PLAYBACK_RATE, self._convert_state)
        try:
            self._player.stdin.write(audio)
            self._player.stdin.flush()
        except OSError as error:
            print('speaker: failed: %s' % error)

    def close(self):
        try:
            if self._player is not None:
                try:
                    self._player.stdin.close()
                except OSError:
                    pass
                self._player.wait(timeout=DRAIN_TIMEOUT_S)
                time.sleep(ECHO_S)
        except subprocess.TimeoutExpired:
            self._player.kill()
        finally:
            speaking.clear()
            _lock.release()


def open_stream(rate):
    _lock.acquire()
    speaking.set()
    return Stream(rate)
