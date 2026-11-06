'''
Claude conversation, the car's half (the PC's half is tools/voice_server.py, reached through its SSH tunnel).
"hey claude" -> rising tone -> every sentence goes to the PC, its spoken answer plays on the car's speaker.
Listening ends after IDLE_END_S without speech (falling tone). "hey claude, wie geht es dir" works in one breath.
Steering stays on the car (audio.py): exact single phrases still steer and are not sent; words inside a sentence do not steer.
'''
import collections
import http.client
import json
import math
import struct
import threading
import time

from . import events, features, telemetry
from .car import car

VOICE_SERVER = ('127.0.0.1', 8766)
HEALTH_TIMEOUT_S = 2
ANSWER_TIMEOUT_S = 30
IDLE_END_S = 60
SAMPLE_RATE = 16000
SPEECH_DBFS = -42           # a chunk this loud counts as speech
CHUNK_S = 0.1
PRE_ROLL_CHUNKS = 3         # the chunks before the level rose: the start of the first word
END_SILENCE_CHUNKS = 7
MIN_SPEECH_CHUNKS = 3
MAX_SENTENCE_S = 20
ROLLING_S = 10              # audio kept for "hey claude" and the rest of the sentence in one breath
WAKE_MARGIN_S = 1.0
SHORT_S = 1.6               # a list word is shorter; a longer utterance is a sentence
COMMAND_GRACE_S = 0.5       # the word recogniser may decide just after the end of speech
TONE_START = (660, 880)
TONE_END = (880, 660)
TONE_FAILED = (220, 220)
TONE_NOTE_S = 0.12


def _tone(frequencies):
    samples = []
    for frequency in frequencies:
        for index in range(int(TONE_NOTE_S * SAMPLE_RATE)):
            samples.append(int(9000 * math.sin(2 * math.pi * frequency * index / SAMPLE_RATE)))
    return struct.pack('<%dh' % len(samples), *samples)


def _play(*sounds):
    speaker = events.open_speaker(SAMPLE_RATE)
    try:
        for sound in sounds:
            events.write_speaker(speaker, sound)
    finally:
        events.close_speaker(speaker)


def _car_status():
    status = dict(car.state())
    status.update({name: value for name, value in telemetry.latest.items() if isinstance(value, (int, float, str))})
    return json.dumps(status)[:4000]


class Conversation(object):
    def __init__(self):
        self.status = {'state': 'off', 'transcript': None, 'reply': None, 'error': None, 'language': None}
        self._until = 0.0
        self._busy = False
        self._new = True
        self._discard = False
        self._rolling = collections.deque(maxlen=int(ROLLING_S / CHUNK_S))
        self._sentence = None
        self._speech_chunks = 0
        self._silent_chunks = 0
        self._last_sentence_s = 0.0

    def active(self):
        return time.time() < self._until or self._busy

    def speech_seconds(self):
        return (len(self._sentence) / (2.0 * SAMPLE_RATE)) if self._sentence is not None else self._last_sentence_s

    def discard_sentence(self):
        '''The word recogniser used this utterance as a command: it is not for Claude.'''
        self._discard = True

    def start(self, sentence_seconds=None):
        '''Wake phrase heard. sentence_seconds: it came with more words, spoken in that time.'''
        self._discard = not sentence_seconds
        if self.active():
            self._until = time.time() + IDLE_END_S
            return
        self._new, self._until = True, time.time() + IDLE_END_S
        self.status.update(state='listening', transcript=None, reply=None, error=None)
        events.add('voice', 'conversation started')
        if sentence_seconds:
            chunks = list(self._rolling)[-int((sentence_seconds + WAKE_MARGIN_S) / CHUNK_S):]
            self._send_sentence(b''.join(chunks), sentence_seconds)
        else:
            threading.Thread(target=self._open, daemon=True).start()

    def _open(self):
        try:
            connection = http.client.HTTPConnection(*VOICE_SERVER, timeout=HEALTH_TIMEOUT_S)
            connection.request('GET', '/health')
            connection.getresponse().read()
            _play(_tone(TONE_START))
        except OSError as error:
            self._failed('voice server not reachable (%s)' % error)

    def _failed(self, text):
        events.add('voice', text)
        self._until = 0.0
        self.status.update(state='off', error=text)
        _play(_tone(TONE_FAILED))

    def end(self):
        self._until = 0.0
        self.status['state'] = 'off'
        events.add('voice', 'conversation ended')
        threading.Thread(target=_play, args=(_tone(TONE_END),), daemon=True).start()

    def feed(self, chunk, level):
        '''Every microphone chunk, from the capture thread: never blocks.'''
        self._rolling.append(chunk)
        if self.status['state'] == 'off':
            return
        if not features.enabled('claude_voice') or (time.time() >= self._until and not self._busy and self._sentence is None):
            self._sentence = None
            self.end()
            return
        if self._busy or events.speaking.is_set():
            self._sentence = None
            return
        if self._sentence is None:
            if level < SPEECH_DBFS:
                return
            pre_roll = list(self._rolling)[-PRE_ROLL_CHUNKS:]
            self._sentence, self._speech_chunks, self._silent_chunks, self._discard = bytearray(b''.join(pre_roll)), 0, 0, False
            return
        self._sentence += chunk
        if level >= SPEECH_DBFS:
            self._speech_chunks, self._silent_chunks = self._speech_chunks + 1, 0
        else:
            self._silent_chunks += 1
        seconds = len(self._sentence) / (2.0 * SAMPLE_RATE)
        if self._silent_chunks >= END_SILENCE_CHUNKS or seconds >= MAX_SENTENCE_S:
            sentence, self._sentence = bytes(self._sentence), None
            spoken_seconds = seconds - self._silent_chunks * CHUNK_S
            self._last_sentence_s = spoken_seconds
            self._until = time.time() + IDLE_END_S
            if self._speech_chunks >= MIN_SPEECH_CHUNKS:
                self._send_sentence(sentence, spoken_seconds)

    def _send_sentence(self, sentence, spoken_seconds):
        self._busy = True
        threading.Thread(target=self._ask, args=(sentence, spoken_seconds), daemon=True).start()

    def _ask(self, sentence, spoken_seconds):
        try:
            if spoken_seconds <= SHORT_S:
                time.sleep(COMMAND_GRACE_S)
                if self._discard:
                    return
            self.status['state'] = 'thinking'
            self._exchange(sentence)
            self.status['state'] = 'listening'
        except (OSError, http.client.HTTPException, ValueError) as error:
            self._failed('voice server failed (%s)' % error)
        finally:
            self._busy = False
            if self.status['state'] != 'off':
                self._until = time.time() + IDLE_END_S

    def _exchange(self, sentence):
        connection = http.client.HTTPConnection(*VOICE_SERVER, timeout=ANSWER_TIMEOUT_S)
        connection.request('POST', '/utterance?new=%d' % self._new, sentence, {'Content-Type': 'application/octet-stream', 'X-Car-Status': _car_status()})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('HTTP %d' % response.status)
        self._new = False
        speaker, speaker_open, reply = None, False, []
        try:
            while True:
                header = response.read(5)
                if len(header) < 5:
                    return
                kind, length = header[:1], struct.unpack('>I', header[1:])[0]
                payload = response.read(length)
                if kind == b'T':
                    fields = json.loads(payload.decode('ascii'))
                    if 'transcript' in fields:
                        self.status.update(transcript=fields['transcript'], language=fields['language'], reply=None)
                        if fields['transcript']:
                            events.add('voice', 'you: ' + fields['transcript'])
                    if 'sentence' in fields:
                        reply.append(fields['sentence'])
                        self.status['reply'] = ' '.join(reply)
                        events.add('voice', 'claude: ' + fields['sentence'])
                    if 'error' in fields:
                        self.status['error'] = fields['error']
                elif kind == b'A':
                    if not speaker_open:
                        self.status['state'] = 'speaking'
                        speaker, speaker_open = events.open_speaker(SAMPLE_RATE), True
                    events.write_speaker(speaker, payload)
        finally:
            if speaker_open:
                events.close_speaker(speaker)

    def describe(self):
        return dict(self.status, seconds_left=max(0, int(self._until - time.time())))


conversation = Conversation()
