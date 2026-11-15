'''
Claude conversation, the car's half (the PC's half is tools/voice_server.py, reached through voice_link.py).
"hey claude" -> the PC says "Hallo" -> every sentence goes to the PC, its spoken answer plays on the car's speaker.
Listening ends after IDLE_END_S without speech (falling tone). "hey claude, wie geht es dir" works in one breath.
Spoken commands stay on the car (word_recognizer.py): a whole command phrase runs and is not sent; words inside a
sentence do not steer. Claude may flip switches (set_feature) and run spoken commands that do not move the car
(run_command, commands.for_claude). While claude_voice is on, the car asks the PC every NEWS_POLL_S for
announcements (a feature built by voice request is ready, ...) and speaks them when nobody is talking.
'''
import collections
import json
import threading
import time

from . import commands, events, features, services, speaker, telemetry, voice_link, voice_protocol as protocol
from .car import car
from .voice_settings import speech_dbfs, voice_languages

IDLE_END_S = 60
NEWS_POLL_S = 10
SAMPLE_RATE = protocol.UTTERANCE_RATE
CHUNK_S = 0.1
PRE_ROLL_CHUNKS = 3         # the chunks before the level rose: the start of the first word
END_SILENCE_CHUNKS = 7
MIN_SPEECH_CHUNKS = 3
MAX_SENTENCE_S = 20
ROLLING_S = 10              # audio kept for "hey claude" and the rest of the sentence in one breath
WAKE_MARGIN_S = 1.0
SHORT_S = 1.6               # a command phrase is shorter; a longer utterance is a sentence
COMMAND_GRACE_S = 0.5       # the word recogniser may decide just after the end of speech
STATUS_MAX_CHARS = 4000


def _seconds(audio):
    return len(audio) / (2.0 * SAMPLE_RATE)


def _car_headers():
    status = dict(car.state())
    status.update({name: value for name, value in telemetry.latest.items() if isinstance(value, (int, float, str))})
    return {protocol.STATUS: json.dumps(status)[:STATUS_MAX_CHARS],
            protocol.FEATURES: json.dumps([[feature['name'], feature['on'], feature['help']] for feature in features.describe()]),
            protocol.COMMANDS: json.dumps(commands.for_claude()),
            protocol.VOICE_LANGUAGES: ','.join(voice_languages()),
            'Content-Type': 'application/octet-stream'}


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
        self._reply = []
        services.start('voice-news', self._poll_news)

    # ---- asked by the word recogniser ----
    def active(self):
        return time.time() < self._until or self._busy

    def speech_seconds(self):
        return _seconds(self._sentence) if self._sentence is not None else self._last_sentence_s

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
            threading.Thread(target=self._greet, name='voice-greeting', daemon=True).start()

    def end(self):
        self._until = 0.0
        self.status['state'] = 'off'
        events.add('voice', 'conversation ended')
        speaker.play_tones_later(speaker.Tone.END)

    def end_if_active(self):
        if self.active():
            self.end()

    # ---- the microphone's chunks ----
    def feed(self, chunk, level):
        '''Every microphone chunk, from the capture thread: never blocks.'''
        self._rolling.append(chunk)
        if self.status['state'] == 'off':
            return
        if not features.enabled('claude_voice') or (time.time() >= self._until and not self._busy and self._sentence is None):
            self._sentence = None
            self.end()
            return
        if self._busy or speaker.speaking.is_set():
            self._sentence = None
            return
        if self._sentence is None:
            if level >= speech_dbfs():
                self._begin_sentence()
            return
        self._continue_sentence(chunk, level)

    def _begin_sentence(self):
        pre_roll = list(self._rolling)[-PRE_ROLL_CHUNKS:]
        self._sentence, self._speech_chunks, self._silent_chunks, self._discard = bytearray(b''.join(pre_roll)), 0, 0, False

    def _continue_sentence(self, chunk, level):
        self._sentence += chunk
        if level >= speech_dbfs():
            self._speech_chunks, self._silent_chunks = self._speech_chunks + 1, 0
        else:
            self._silent_chunks += 1
        seconds = _seconds(self._sentence)
        if self._silent_chunks < END_SILENCE_CHUNKS and seconds < MAX_SENTENCE_S:
            return
        sentence, self._sentence = bytes(self._sentence), None
        spoken_seconds = seconds - self._silent_chunks * CHUNK_S
        self._last_sentence_s = spoken_seconds
        self._until = time.time() + IDLE_END_S
        if self._speech_chunks >= MIN_SPEECH_CHUNKS:
            self._send_sentence(sentence, spoken_seconds)

    # ---- talking to the PC ----
    def _greet(self):
        try:
            response = voice_link.greeting(voice_languages()[0])
            if response is None:
                speaker.play_tones(speaker.Tone.START)
                return
            self._play(response)
            self.status['state'] = 'listening'
        except voice_link.LinkError as error:
            self._failed('voice server not reachable (%s)' % error)

    def _send_sentence(self, sentence, spoken_seconds):
        self._busy = True
        threading.Thread(target=self._ask, args=(sentence, spoken_seconds), name='voice-ask', daemon=True).start()

    def _ask(self, sentence, spoken_seconds):
        try:
            if spoken_seconds <= SHORT_S:
                time.sleep(COMMAND_GRACE_S)
                if self._discard:
                    return
            self.status['state'] = 'thinking'
            self._play(voice_link.utterance(sentence, self._new, _car_headers()))
            self._new = False
            self.status['state'] = 'listening'
        except (voice_link.LinkError,) + voice_link.CONNECTION_ERRORS as error:
            self._failed('voice server failed (%s)' % error)
        finally:
            self._busy = False
            if self.status['state'] != 'off':
                self._until = time.time() + IDLE_END_S

    def _poll_news(self):
        while True:
            time.sleep(NEWS_POLL_S)
            if not features.enabled('claude_voice') or self.active() or self._sentence is not None or speaker.speaking.is_set():
                continue
            try:
                response = voice_link.news()
                if response is not None:
                    self._play(response)
            except (voice_link.LinkError,) + voice_link.CONNECTION_ERRORS:
                pass  # no voice server running: nothing to announce

    def _play(self, response):
        self._reply = []
        voice_link.play_reply(response, self._on_text, lambda: self.status.update(state='speaking'))

    def _on_text(self, fields):
        '''One text frame of the PC's reply: show and log it, run what Claude asked for.'''
        if 'transcript' in fields:
            self.status.update(transcript=fields['transcript'], language=fields['language'], reply=None)
            if fields['transcript']:
                events.add('voice', 'you: ' + fields['transcript'])
        if 'sentence' in fields:
            self._reply.append(fields['sentence'])
            self.status['reply'] = ' '.join(self._reply)
            events.add('voice', 'claude: ' + fields['sentence'])
        if 'error' in fields:
            self.status['error'] = fields['error']
        if 'set_feature' in fields:
            try:
                features.set_enabled(fields['set_feature'], fields['on'])
                events.add('voice', 'claude switched %s %s' % (fields['set_feature'], 'on' if fields['on'] else 'off'))
            except KeyError as error:
                events.add('voice', 'claude: %s' % error)
        if 'run_command' in fields:
            events.add('voice', commands.run_for_claude(fields['run_command']))

    def _failed(self, text):
        events.add('voice', text)
        self._until = 0.0
        self.status.update(state='off', error=text)
        speaker.play_tones(speaker.Tone.FAILED)

    def describe(self):
        return dict(self.status, seconds_left=max(0, int(self._until - time.time())))


conversation = Conversation()
