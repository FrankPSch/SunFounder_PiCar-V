'''Learn and check the spoken commands with the car's own speech recogniser (Vosk, the same grammar as the car).
Nothing here moves the car: no action runs, only recognition is checked.

  learn      On the car, with the car service stopped (the microphone serves one program only): for each command
             the car says the phrase and what it does, beeps, records you repeating it (3 s) and says at once
             whether it understood. Each recording is kept for replay: <data>/voice_check/<language>/<phrase>.wav
  replay     Every recording through the recognisers: heard, confidence, loudness, verdict. On the car or the PC.
  checklist  All commands as a Markdown list to read along.

  sudo systemctl stop picar
  python3 tools/voice_check.py learn [--language de|en] [--group drive] [--command forward]
  sudo systemctl start picar
  python3 tools/voice_check.py replay [--recordings DIR] [--models DIR]
  python3 tools/voice_check.py checklist
Exit code of learn and replay: 0 = every phrase understood.
'''
import argparse
import enum
import json
import os
import subprocess
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'remote_control'))
from remote_control import alsa, speaker, speech_level, vocabulary, voice_protocol  # noqa: E402

SAMPLE_RATE = voice_protocol.UTTERANCE_RATE
CHUNK_BYTES = SAMPLE_RATE // 10 * speech_level.SAMPLE_WIDTH
RECORD_S = 3
MAX_TRIES = 3
SERVICE = 'picar'
ALL_KINDS = frozenset(vocabulary.Kind)


class Verdict(enum.Enum):
    UNDERSTOOD = {'de': 'verstanden', 'en': 'understood'}
    NOTHING = {'de': 'nichts verstanden', 'en': 'heard nothing'}
    OTHER = {'de': 'falsch verstanden', 'en': 'heard something else'}
    UNSURE = {'de': 'unsicher', 'en': 'not sure'}
    QUIET = {'de': 'zu leise', 'en': 'too quiet'}


class Hearing(object):
    '''What the recognisers made of one recording of one phrase.'''
    def __init__(self, language, phrase, heard, confidence, peak_dbfs, other_languages):
        self.language, self.phrase, self.heard, self.confidence, self.peak_dbfs = language, phrase, heard, confidence, peak_dbfs
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


class Recognizers(object):
    '''One Vosk model per language, each limited to all phrases of its language (every switch on).'''
    def __init__(self, models_dir):
        import vosk
        vosk.SetLogLevel(-1)
        self._vosk = vosk
        self._models = {language: vosk.Model(os.path.join(models_dir, folder)) for language, folder in vocabulary.MODEL_FOLDERS.items()
                        if os.path.isdir(os.path.join(models_dir, folder))}
        if not self._models:
            raise SystemExit('no Vosk model in %s (run tools/install_extras.sh)' % models_dir)

    def languages(self):
        return sorted(self._models)

    def hear(self, language, phrase, audio):
        heard = {spoken_language: self._text(spoken_language, audio) for spoken_language in self._models}
        text, confidence = heard.pop(language)
        others = {other: text_heard for other, (text_heard, other_confidence) in heard.items()
                  if text_heard and other_confidence >= vocabulary.MIN_WORD_CONF}
        return Hearing(language, phrase, text, confidence, peak_dbfs(audio), others)

    def _text(self, language, audio):
        # ensure_ascii=False as on the car: an escaped umlaut is not in the vocabulary
        recognizer = self._vosk.KaldiRecognizer(self._models[language], SAMPLE_RATE,
                                                json.dumps(vocabulary.grammar(language, ALL_KINDS), ensure_ascii=False))
        recognizer.SetWords(True)
        results = [recognizer.Result() for chunk in chunks(audio) if recognizer.AcceptWaveform(chunk)] + [recognizer.FinalResult()]
        _, text, confidence = vocabulary.heard_words([word for result in results for word in json.loads(result).get('result', [])])
        return text, confidence


def chunks(audio):
    return [audio[start:start + CHUNK_BYTES] for start in range(0, len(audio) - CHUNK_BYTES + 1, CHUNK_BYTES)]


def peak_dbfs(audio):
    '''The loudest 0.1 s chunk, measured as the car's microphone loop does.'''
    return max((speech_level.level_dbfs(chunk) for chunk in chunks(audio)), default=-90.0)


# ---- recordings ----
def recording_path(recordings_dir, language, phrase):
    return os.path.join(recordings_dir, language, phrase.replace(' ', '_') + '.wav')


def save_wav(path, audio):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, 'wb') as recording:
        recording.setnchannels(1)
        recording.setsampwidth(speech_level.SAMPLE_WIDTH)
        recording.setframerate(SAMPLE_RATE)
        recording.writeframes(audio)


def load_wav(path):
    with wave.open(path, 'rb') as recording:
        if (recording.getframerate(), recording.getnchannels(), recording.getsampwidth()) != (SAMPLE_RATE, 1, speech_level.SAMPLE_WIDTH):
            raise ValueError('%s: not 16 kHz mono 16-bit' % path)
        return recording.readframes(recording.getnframes())


def saved_recordings(recordings_dir):
    '''(language, phrase, path) of every recording of a phrase that is still in the vocabulary.'''
    found = []
    for language in vocabulary.LANGUAGES:
        for phrase in sorted(vocabulary.phrases(language)):
            path = recording_path(recordings_dir, language, phrase)
            if os.path.isfile(path):
                found.append((language, phrase, path))
    return found


# ---- learn (on the car) ----
def selected_phrases(language, group, command):
    return [(spoken, phrase) for spoken in vocabulary.SPOKEN if group in (None, spoken.group) and command in (None, spoken.name)
            for phrase in spoken.phrases(language)]


def record(device):
    command = ['arecord', '-q', '-D', device, '-f', 'S16_LE', '-r', str(SAMPLE_RATE), '-c', '1', '-t', 'raw', '-d', str(RECORD_S)]
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def service_running():
    return subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE]).returncode == 0


def learn(recognizers, recordings_dir, language, group, command):
    if service_running():
        raise SystemExit('the car service holds the microphone: sudo systemctl stop %s (and start it again afterwards)' % SERVICE)
    device = alsa.usb_device('arecord')
    if device is None:
        raise SystemExit('no USB microphone found')
    alsa.set_mic_gain(device)
    hearings, current_group = [], None
    for spoken, phrase in selected_phrases(language, group, command):
        if spoken.group != current_group:
            current_group = spoken.group
            speaker.say(vocabulary.GROUP_NAMES[current_group][language], language)
        speaker.say('%s. %s' % (phrase, vocabulary.explanation(spoken, language)), language)
        for _ in range(MAX_TRIES):
            speaker.play_tones(speaker.Tone.START)
            audio = record(device)
            hearing = recognizers.hear(language, phrase, audio)
            print_hearing(hearing)
            speaker.say(hearing.spoken_feedback(), language)
            if hearing.verdict == Verdict.UNDERSTOOD:
                save_wav(recording_path(recordings_dir, language, phrase), audio)
                break
        else:
            save_wav(recording_path(recordings_dir, language, phrase), audio)  # the last try, to replay what went wrong
        hearings.append(hearing)
    return hearings


# ---- report ----
def print_hearing(hearing):
    others = ', '.join('%s: %s' % item for item in sorted(hearing.other_languages.items()))
    print('%-2s  %-16s %-18s %4.2f %6.1f dBFS  %-11s %s' % (hearing.language, hearing.phrase, hearing.heard or '-', hearing.confidence,
                                                         hearing.peak_dbfs, hearing.verdict.name.lower(), others))


def summary(hearings):
    understood = sum(hearing.verdict == Verdict.UNDERSTOOD for hearing in hearings)
    print('%d of %d phrases understood' % (understood, len(hearings)))
    return understood == len(hearings)


def replay(recognizers, recordings_dir):
    hearings = []
    for language, phrase, path in saved_recordings(recordings_dir):
        if language in recognizers.languages():
            hearings.append(recognizers.hear(language, phrase, load_wav(path)))
            print_hearing(hearings[-1])
    if not hearings:
        raise SystemExit('no recordings in %s: run learn first' % recordings_dir)
    return hearings


def checklist():
    lines = ['# PiCar voice commands', '', 'Switches: voice_commands for the commands, claude_voice for System. Stop always listens.']
    for group, names in vocabulary.GROUP_NAMES.items():
        lines += ['', '## %s / %s' % (names['de'], names['en']), '', '| Deutsch | English | Was passiert | What happens |', '|---|---|---|---|']
        for spoken in vocabulary.SPOKEN:
            if spoken.group == group:
                lines.append('| %s | %s | %s | %s |' % (', '.join(spoken.de), ', '.join(spoken.en), *vocabulary.EXPLANATIONS[spoken.name]))
    return '\n'.join(lines)


def data_dir():
    from remote_control import features  # imported only here: loading it writes features.json into the data folder
    return features.DATA_DIR


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('mode', choices=('learn', 'replay', 'checklist'))
    parser.add_argument('--language', choices=vocabulary.LANGUAGES, default='de')
    parser.add_argument('--group', choices=sorted(vocabulary.GROUP_NAMES))
    parser.add_argument('--command', choices=sorted(vocabulary.SPOKEN_BY_NAME))
    parser.add_argument('--recordings', help='default <data>/voice_check')
    parser.add_argument('--models', help='folder with the Vosk models, default <data>/models')
    args = parser.parse_args(argv)
    if args.mode == 'checklist':
        print(checklist())
        return 0
    recognizers = Recognizers(args.models or os.path.join(data_dir(), 'models'))
    recordings_dir = args.recordings or os.path.join(data_dir(), 'voice_check')
    if args.mode == 'learn':
        hearings = learn(recognizers, recordings_dir, args.language, args.group, args.command)
    else:
        hearings = replay(recognizers, recordings_dir)
    return 0 if summary(hearings) else 1


if __name__ == '__main__':
    sys.exit(main())
