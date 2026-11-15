'''A learning session for the spoken commands, inside the running car service (it owns the microphone, so nothing
has to be stopped). Started by "Befehle lernen" or POST /api/cmd {"cmd": "learn", "on": true} (tools/voice_check.py learn).
For each phrase the car says it and what it does, beeps, takes RECORD_S of microphone audio and says at once whether
it understood (phrase_check). Every try is kept (phrase_check.save_recording), parked or driving.
Meanwhile no phrase runs except stop and "Lernen beenden" (word_recognizer).
It ends with "Lernen beenden", POST learn off, after the last phrase, or after QUIET_TRIES_TO_END quiet tries in a row.'''
import os
import threading

from . import events, features, phrase_check, speaker, vocabulary
from .spoken_texts import LEARNING
from .car import car
from .phrase_check import Verdict

RECORD_S = 3
RECORD_CHUNKS = RECORD_S * 10           # 0.1 s chunks from the microphone loop
WAIT_MARGIN_S = 2                       # the microphone may need a moment to start
MAX_TRIES = 3
QUIET_TRIES_TO_END = 3                  # nobody there any more
NOT_PRACTISED = ('learn', 'learn_stop')
RUNS_WHILE_LEARNING = ('learn_stop',)   # besides the stop words


def practice_phrases(language, group=None):
    return [(spoken, phrase) for spoken in vocabulary.SPOKEN if spoken.name not in NOT_PRACTISED and group in (None, spoken.group)
            for phrase in spoken.phrases(language)]


class Session(object):
    def __init__(self, models_dir, recordings_dir):
        self.models_dir, self.recordings_dir = models_dir, recordings_dir
        self.status = {'active': False, 'language': None, 'group': None, 'phrase': None, 'results': [], 'error': None}
        self._lock = threading.Lock()
        self._ending = threading.Event()
        self._recorded = threading.Event()
        self._chunks = None

    def active(self):
        return self.status['active']

    def start(self, language, group=None):
        with self._lock:
            if self.status['active']:
                return
            self.status.update(active=True, language=language, group=group, phrase=None, results=[], error=None)
            self._ending.clear()
        threading.Thread(target=self._run, args=(language, group), name='learning', daemon=True).start()

    def end(self):
        self._ending.set()
        self._recorded.set()

    def feed(self, chunk):
        '''Every microphone chunk; kept only while a try records.'''
        recording = self._chunks
        if recording is not None:
            recording.append(chunk)
            if len(recording) >= RECORD_CHUNKS:
                self._recorded.set()

    # ---- the session thread ----
    def _run(self, language, group):
        try:
            self._practise(language, {language: phrase_check.model(os.path.join(self.models_dir, vocabulary.MODEL_FOLDERS[language]))}, group)
        except Exception as error:  # a missing model must not leave the session on and every command muted
            self.status['error'] = str(error)
            events.add('learn', 'learning failed: %s' % error)
        finally:
            understood, practised = self._score()
            self.status.update(active=False, phrase=None)
            speaker.say('%s %s' % (LEARNING['end'][language], LEARNING['score'][language] % (understood, practised)), language)

    def _practise(self, language, models, group):
        speaker.say(LEARNING['start'][language], language)
        current_group, quiet_in_a_row = None, 0
        for spoken, phrase in practice_phrases(language, group):
            if self._ending.is_set():
                return
            if spoken.group != current_group:
                current_group = spoken.group
                speaker.say(vocabulary.GROUP_NAMES[current_group][language], language)
            speaker.say('%s. %s' % (phrase, vocabulary.explanation(spoken, language)), language)
            self.status['phrase'] = phrase
            for _ in range(MAX_TRIES):
                hearing = self._try(language, models, phrase)
                if hearing is None:
                    return
                speaker.say(hearing.spoken_feedback(), language)
                quiet_in_a_row = quiet_in_a_row + 1 if hearing.verdict == Verdict.QUIET else 0
                if quiet_in_a_row >= QUIET_TRIES_TO_END:
                    return
                if hearing.verdict == Verdict.UNDERSTOOD:
                    break

    def _try(self, language, models, phrase):
        speaker.play_tones(speaker.Tone.START)
        audio = self._record()
        if audio is None:
            return None
        hearing = phrase_check.hear(models, language, phrase, audio)
        condition = 'driving' if car.drive_direction else 'parked'
        saved = phrase_check.save_recording(self.recordings_dir, condition, hearing, audio)
        for path in (saved, os.path.dirname(saved), os.path.join(self.recordings_dir, phrase_check.RESULTS_FILE)):
            features.give_to_pi(path)
        self.status['results'].append(dict(hearing.as_dict(), condition=condition))
        events.add('learn', '%s "%s": %s' % (language, phrase, hearing.spoken_feedback()))
        return hearing

    def _record(self):
        '''RECORD_S of microphone audio, or None when the session was ended or the microphone delivers nothing.'''
        self._recorded.clear()
        self._chunks = []
        self._recorded.wait(RECORD_S + WAIT_MARGIN_S)
        recording, self._chunks = self._chunks, None
        if self._ending.is_set() or len(recording) < RECORD_CHUNKS:
            return None
        return b''.join(recording[:RECORD_CHUNKS])

    def _score(self):
        '''(phrases understood, phrases tried) in this session.'''
        tried = {result['phrase'] for result in self.status['results']}
        understood = {result['phrase'] for result in self.status['results'] if result['verdict'] == Verdict.UNDERSTOOD.name.lower()}
        return len(understood), len(tried)

    def describe(self):
        return dict(self.status, results=list(self.status['results']))


session = Session(os.path.join(features.DATA_DIR, 'models'), os.path.join(features.DATA_DIR, 'voice_check'))
