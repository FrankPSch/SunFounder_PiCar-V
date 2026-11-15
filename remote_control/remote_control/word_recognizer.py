'''Offline word recognition (Vosk) of the spoken commands in vocabulary.SPOKEN: one recogniser per language in the
setting voice_languages, each limited to the phrases of the switches that are on. A grammar-limited recogniser maps
room noise onto its nearest phrase ("halt" twice a minute at -63 dBFS), so a phrase only counts when it was loud
enough (voice_settings.speech_dbfs) and the recogniser is sure (vocabulary.MIN_WORD_CONF).'''
import json
import os
import time

from . import commands, events, features, speaker, vocabulary, voice_protocol
from .car import car
from .conversation import conversation, SHORT_S
from .vocabulary import Kind, MIN_WORD_CONF, UNKNOWN
from .voice_settings import parked_speech_dbfs, speech_dbfs, voice_languages

SAMPLE_RATE = voice_protocol.UTTERANCE_RATE
REPEAT_IGNORE_S = 1.5       # both language recognisers may hear the same phrase


def _listened_kinds():
    kinds = {Kind.STOP}
    if features.enabled('voice_commands'):
        kinds.add(Kind.COMMAND)
    if features.enabled('claude_voice'):
        kinds.update((Kind.WAKE, Kind.SYSTEM))
    return kinds


def wanted():
    '''Whether any word needs listening for, and which: the recognisers are rebuilt when this changes.'''
    if not (features.enabled('voice_commands') or features.enabled('stop_word') or features.enabled('claude_voice')):
        return None
    return frozenset(_listened_kinds())


def _switch_on(spoken):
    switch = vocabulary.SWITCH[spoken.kind]
    return switch is None or features.enabled(switch)


class WordRecognizer(object):
    def __init__(self, model_dirs):
        self.model_dirs = model_dirs
        self.status = {'heard': None, 'heard_time': None, 'rejected': None, 'rejected_count': 0, 'wake_time': None,
                       'languages': [], 'missing_words': {}, 'error': None}
        self._models = {}
        self._recognizers = []
        self._last_command = (None, 0.0, None)

    # ---- grammar ----
    def rebuild(self, kinds):
        self._recognizers = []
        if kinds is None:
            self.status['languages'] = []
            return
        try:
            import vosk
            vosk.SetLogLevel(-1)
            for language in voice_languages():
                model_dir = self.model_dirs.get(language)
                if not model_dir or not os.path.isdir(model_dir):
                    continue
                if language not in self._models:
                    self._models[language] = vosk.Model(model_dir)
                self._recognizers.append((language, self._recognizer(vosk, language, kinds)))
        except Exception as error:  # vosk raises plain Exception for a bad model
            self.status['error'] = 'vosk not ready (%s) - run tools/install_extras.sh' % error
            return
        self.status['languages'] = [language for language, _ in self._recognizers]
        if not self._recognizers:
            self.status['error'] = 'no speech model - run tools/install_extras.sh'

    def _recognizer(self, vosk, language, kinds):
        model = self._models[language]
        phrases = vocabulary.grammar(language, kinds)
        self.status['missing_words'][language] = sorted({word for phrase in phrases[:-1] for word in phrase.split()
                                                         if hasattr(model, 'find_word') and model.find_word(word) < 0})
        # ensure_ascii=False: an escaped umlaut is not found in the vocabulary and the word is silently dropped
        recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE, json.dumps(phrases, ensure_ascii=False))
        recognizer.SetWords(True)
        return recognizer

    # ---- audio ----
    def recognizers(self):
        return list(self._recognizers)

    def recognize(self, language, recognizer, chunk, recent_peak_dbfs):
        if speaker.speaking.is_set() and not car.drive_direction:  # the car's own voice must not trigger a command; while driving, stop stays live
            recognizer.Reset()
            return
        if recognizer.AcceptWaveform(chunk):
            self._final(language, json.loads(recognizer.Result()).get('result', []), recent_peak_dbfs)
        else:
            self._partial(language, recognizer, json.loads(recognizer.PartialResult()).get('partial', ''), recent_peak_dbfs)

    def _final(self, language, all_words, recent_peak_dbfs):
        words, text, confidence = vocabulary.heard_words(all_words)  # in a conversation, unknown words make it a sentence
        if not words:
            return
        has_stop_word = any(word['word'] in self._stop_words(language) for word in words)
        if recent_peak_dbfs < (parked_speech_dbfs() if has_stop_word else speech_dbfs()) or confidence < MIN_WORD_CONF:
            self.status['rejected'] = '%s %s conf %.2f peak %.0f dBFS' % (time.strftime('%H:%M:%S'), text, confidence, recent_peak_dbfs)
            self.status['rejected_count'] += 1
            return
        in_sentence = conversation.active() and (len(words) != len(all_words) or conversation.speech_seconds() > SHORT_S)
        more_words_seconds = all_words[-1].get('end', 0) - all_words[0].get('start', 0) if len(words) != len(all_words) else None
        self.heard(language, text, in_sentence, more_words_seconds)

    def _partial(self, language, recognizer, partial, recent_peak_dbfs):
        words = partial.replace(UNKNOWN, '').strip()
        if car.drive_direction and recent_peak_dbfs >= parked_speech_dbfs() and any(word in partial.split() for word in self._stop_words(language)):
            car.stop('voice stop')
            events.add('audio', 'heard "%s" -> STOP' % partial)
            return
        spoken = vocabulary.phrases(language).get(words)
        if (spoken and spoken.fast and recent_peak_dbfs >= speech_dbfs() and features.enabled('voice_commands')
                and not speaker.speaking.is_set() and not conversation.active()):
            recognizer.Reset()  # or the final result would run it a second time
            self.heard(language, words)

    @staticmethod
    def _stop_words(language):
        return [phrase for phrase, spoken in vocabulary.phrases(language).items() if spoken.kind == Kind.STOP]

    # ---- a recognised phrase ----
    def heard(self, language, text, in_sentence=False, more_words_seconds=None):
        '''in_sentence: part of a longer sentence during a conversation, which goes to Claude and runs nothing.
        more_words_seconds: length of the whole utterance, when it held words outside the grammar.'''
        self.status.update(heard=text, heard_time=time.strftime('%H:%M:%S'))
        spoken = vocabulary.phrases(language).get(text)
        muted = speaker.speaking.is_set()  # stop words stay live while the car speaks and drives
        if spoken and spoken.kind == Kind.WAKE and not muted and _switch_on(spoken):
            self.status['wake_time'] = time.strftime('%H:%M:%S')
            events.add('audio', 'heard "%s"' % text)
            conversation.start(more_words_seconds)
            return
        if spoken and spoken.kind in (Kind.COMMAND, Kind.SYSTEM) and _switch_on(spoken) and not in_sentence and not muted:
            if not (text in vocabulary.NOT_IN_CONVERSATION and conversation.active()):
                self._run(spoken, language, text)
            return
        if any(word in text.split() for word in self._stop_words(language)) and (not in_sentence or car.drive_direction):
            conversation.discard_sentence()
            commands.run_spoken(vocabulary.SPOKEN_BY_NAME['stop'], language, text)

    def _run(self, spoken, language, text):
        now = time.time()
        conversation.discard_sentence()
        if spoken.name == self._last_command[0] and language != self._last_command[2] and now - self._last_command[1] < REPEAT_IGNORE_S:
            return  # the other language's recogniser heard the same phrase
        self._last_command = (spoken.name, now, language)
        commands.run_spoken(spoken, language, text)

    def reset_repeats(self):
        self._last_command = (None, 0.0, None)

    def describe(self):
        return dict(self.status, phrases={language: sorted(vocabulary.phrases(language)) for language in vocabulary.LANGUAGES})
