'''Checking a recording of one phrase with Vosk and the car's grammar (every switch on), and the store of recordings.
Used by the learning session on the car (learning.py) and by tools/voice_check.py replay, also on the PC. No hardware.

Recordings: <recordings>/<parked|driving>/<language>/<phrase>.<n>.wav (spaces as "_", n counts the tries),
each with one line in <recordings>/results.jsonl: what the car heard when it was recorded.'''
import enum
import glob
import json
import os
import threading
import time
import wave

from . import speech_level, vocabulary, voice_protocol

SAMPLE_RATE = voice_protocol.UTTERANCE_RATE
CHUNK_BYTES = SAMPLE_RATE // 10 * speech_level.SAMPLE_WIDTH
ALL_KINDS = frozenset(vocabulary.Kind)
CONDITIONS = ('parked', 'driving')
RESULTS_FILE = 'results.jsonl'
SILENCE_DBFS = -90.0

_models = {}
_models_lock = threading.Lock()


def model(model_dir):
    '''The Vosk model in a folder, loaded once and shared (word recogniser and learning: about 100 MB of RAM each).'''
    import vosk
    with _models_lock:
        if model_dir not in _models:
            vosk.SetLogLevel(-1)
            _models[model_dir] = vosk.Model(model_dir)
        return _models[model_dir]


class Verdict(enum.Enum):
    UNDERSTOOD = {'de': 'verstanden', 'en': 'understood'}
    NOTHING = {'de': 'nichts verstanden', 'en': 'heard nothing'}
    OTHER = {'de': 'falsch verstanden', 'en': 'heard something else'}
    UNSURE = {'de': 'unsicher', 'en': 'not sure'}
    QUIET = {'de': 'zu leise', 'en': 'too quiet'}


class Hearing(object):
    '''What the recognisers made of one recording of one phrase.'''
    def __init__(self, language, phrase, heard, confidence, peak, other_languages):
        self.language, self.phrase, self.heard, self.confidence, self.peak_dbfs = language, phrase, heard, confidence, peak
        self.other_languages = other_languages  # {language: heard text}: what the other recognisers took it for
        self.verdict = self._verdict()

    def _verdict(self):
        if self.peak_dbfs < speech_level.SPEECH_DBFS['parked']:
            return Verdict.QUIET
        if not self.heard:
            return Verdict.NOTHING
        if vocabulary.phrases(self.language).get(self.heard) != vocabulary.phrases(self.language)[self.phrase]:
            return Verdict.OTHER
        return Verdict.UNDERSTOOD if self.confidence >= vocabulary.MIN_WORD_CONF else Verdict.UNSURE

    def spoken_feedback(self):
        if self.verdict == Verdict.OTHER:
            return '%s: %s' % (self.verdict.value[self.language], self.heard)
        return self.verdict.value[self.language]

    def as_dict(self):
        return {'language': self.language, 'phrase': self.phrase, 'heard': self.heard, 'confidence': round(self.confidence, 2),
                'peak_dbfs': self.peak_dbfs, 'verdict': self.verdict.name.lower(), 'other_languages': self.other_languages}


def hear(models, language, phrase, audio):
    '''models: {language: Vosk model}; the phrase's language must be one of them, the others are asked too.'''
    heard = {model_language: _text(language_model, model_language, audio) for model_language, language_model in models.items()}
    text, confidence = heard.pop(language)
    others = {other: other_text for other, (other_text, other_confidence) in heard.items()
              if other_text and other_confidence >= vocabulary.MIN_WORD_CONF}
    return Hearing(language, phrase, text, confidence, peak_dbfs(audio), others)


def _text(language_model, language, audio):
    import vosk
    # ensure_ascii=False as on the car: an escaped umlaut is not in the vocabulary
    recognizer = vosk.KaldiRecognizer(language_model, SAMPLE_RATE, json.dumps(vocabulary.grammar(language, ALL_KINDS), ensure_ascii=False))
    recognizer.SetWords(True)
    results = [recognizer.Result() for chunk in chunks(audio) if recognizer.AcceptWaveform(chunk)] + [recognizer.FinalResult()]
    _, text, confidence = vocabulary.heard_words([word for result in results for word in json.loads(result).get('result', [])])
    return text, confidence


def chunks(audio):
    return [audio[start:start + CHUNK_BYTES] for start in range(0, len(audio) - CHUNK_BYTES + 1, CHUNK_BYTES)]


def peak_dbfs(audio):
    '''The loudest 0.1 s chunk, measured as the car's microphone loop does.'''
    return max((speech_level.level_dbfs(chunk) for chunk in chunks(audio)), default=SILENCE_DBFS)


# ---- recordings ----
def save_recording(recordings_dir, condition, hearing, audio):
    '''Keeps one try: the wav under the next free number, and what was heard in results.jsonl. Returns the wav path.'''
    folder = os.path.join(recordings_dir, condition, hearing.language)
    os.makedirs(folder, exist_ok=True)
    stem = hearing.phrase.replace(' ', '_')
    numbers = [int(os.path.basename(path).split('.')[-2]) for path in glob.glob(os.path.join(folder, stem + '.*.wav'))]
    path = os.path.join(folder, '%s.%d.wav' % (stem, max(numbers, default=0) + 1))
    with wave.open(path, 'wb') as recording:
        recording.setnchannels(1)
        recording.setsampwidth(speech_level.SAMPLE_WIDTH)
        recording.setframerate(SAMPLE_RATE)
        recording.writeframes(audio)
    with open(os.path.join(recordings_dir, RESULTS_FILE), 'a', encoding='utf-8') as results:
        line = dict(hearing.as_dict(), file=os.path.relpath(path, recordings_dir).replace(os.sep, '/'), condition=condition,
                    time=time.strftime('%Y-%m-%d %H:%M:%S'))
        results.write(json.dumps(line, ensure_ascii=False) + '\n')
    return path


def load_wav(path):
    with wave.open(path, 'rb') as recording:
        if (recording.getframerate(), recording.getnchannels(), recording.getsampwidth()) != (SAMPLE_RATE, 1, speech_level.SAMPLE_WIDTH):
            raise ValueError('%s: not 16 kHz mono 16-bit' % path)
        return recording.readframes(recording.getnframes())


def recorded_results(recordings_dir):
    '''The results.jsonl lines whose wav is still there and whose phrase is still in the vocabulary.'''
    path = os.path.join(recordings_dir, RESULTS_FILE)
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8') as results:
        lines = [json.loads(line) for line in results if line.strip()]
    return [line for line in lines if os.path.isfile(os.path.join(recordings_dir, line['file']))
            and line['phrase'] in vocabulary.phrases(line['language'])]
