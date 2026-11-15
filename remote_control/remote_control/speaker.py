'''The car's speaker: one sound at a time (speech lock), and the flag `speaking` that mutes the word recogniser
meanwhile (the car must not obey its own voice).
  say(text, language)      blocking; say_later() in a thread. Switch natural_voice: the recordings of the PC's Piper
                           voice (voice_cache.py, made by tools/prerender_voice.py), else espeak-ng
  play_tones(Tone.START)   short beeps
  open_stream(rate)        raw 16-bit mono audio from the PC, written in pieces, then close()
The Jabra SPEAK 510 plays 16 kHz audio 3x too fast while its mic records (2 s played in 0.77 s, measured 2026-11-14),
48 kHz at the right speed: everything goes out at PLAYBACK_RATE.'''
import audioop
import enum
import math
import os
import struct
import subprocess
import threading
import io
import time
import wave

from . import alsa, features, voice_cache
from .sim import hardware

PLAYBACK_RATE = 48000
SAMPLE_WIDTH = 2
ECHO_S = 0.5                # the room still rings after the speaker stops
DRAIN_TIMEOUT_S = 120
PLAY_MARGIN_S = 10          # the player may take this much longer than the audio lasts
ESPEAK_WORDS_PER_MINUTE = '150'
TONE_NOTE_S = 0.12
TONE_AMPLITUDE = 9000
CACHE_DIR = os.path.join(features.DATA_DIR, 'voice_cache')
CLIP_PAUSE_S = 0.12         # between stitched recordings ("31" "von" "33")

speaking = threading.Event()
_lock = threading.Lock()


class Tone(enum.Enum):
    START = (660, 880)       # listening / ready
    END = (880, 660)         # stopped listening / off
    FAILED = (220, 220)      # PC not reachable, refused


def _aplay(rate):
    device = alsa.usb_device('aplay')
    return ['aplay', '-q', '-t', 'raw', '-f', 'S16_LE', '-c', '1', '-r', str(rate)] + (['-D', device] if device else [])


def _play(audio):
    '''Raw PLAYBACK_RATE audio, blocking for as long as it lasts.'''
    seconds = len(audio) / float(SAMPLE_WIDTH * PLAYBACK_RATE)
    subprocess.run(_aplay(PLAYBACK_RATE), input=audio, stderr=subprocess.DEVNULL, timeout=seconds + PLAY_MARGIN_S)


def _recorded(clips):
    '''The recordings in a row at PLAYBACK_RATE, a short pause between them.'''
    pause = bytes(1) * (int(CLIP_PAUSE_S * PLAYBACK_RATE) * SAMPLE_WIDTH)
    pieces = []
    for clip in clips:
        with wave.open(clip, 'rb') as recording:
            audio, rate = recording.readframes(recording.getnframes()), recording.getframerate()
        pieces.append(audioop.ratecv(audio, SAMPLE_WIDTH, 1, rate, PLAYBACK_RATE, None)[0])
    return pause.join(pieces)


def _espeak(text, language):
    wav = subprocess.run(['espeak-ng', '-v', language, '-s', ESPEAK_WORDS_PER_MINUTE, '--stdout', text],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=PLAY_MARGIN_S).stdout
    with wave.open(io.BytesIO(wav), 'rb') as voice:
        audio, rate = voice.readframes(voice.getnframes()), voice.getframerate()
    return audioop.ratecv(audio, SAMPLE_WIDTH, 1, rate, PLAYBACK_RATE, None)[0]


def natural_clips(text, language):
    '''The recordings that say text, or None (switch off, or not all of it recorded).'''
    return voice_cache.clips(CACHE_DIR, language, text) if features.enabled('natural_voice') else None


def say(text, language='en'):
    with _lock:
        speaking.set()
        try:
            clips = natural_clips(text, language)
            if hardware.ACTIVE:
                hardware.spoken(text, language, 'natural' if clips else 'espeak')
            else:
                _play(_recorded(clips) if clips else _espeak(text, language))
            time.sleep(ECHO_S)
        except (OSError, subprocess.SubprocessError, wave.Error, EOFError) as error:
            print('speaker: say failed: %s' % error)
        finally:
            speaking.clear()


def describe():
    recordings = {language: len(os.listdir(os.path.join(CACHE_DIR, language))) for language in ('de', 'en')
                  if os.path.isdir(os.path.join(CACHE_DIR, language))}
    voice = 'natural' if features.enabled('natural_voice') and any(recordings.values()) else 'espeak'  # empty folders: espeak
    return {'voice': voice, 'recordings': recordings}


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
