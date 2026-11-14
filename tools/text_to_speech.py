'''Piper on the PC: text -> 16-bit mono speech at the rate the car asks for.'''
import audioop
import os
import subprocess
import threading
import wave

import pc_settings

VOICES = {'de': 'de_DE-thorsten-medium', 'en': 'en_US-lessac-medium'}  # 22.05 kHz; "low" (16 kHz) sounded buzzy (owner, 2026-11-14)
PIPER = os.path.join(pc_settings.VOICE_DIR, 'piper', 'piper', 'piper.exe')
PIPER_PACE = os.environ.get('PIPER_LENGTH_SCALE', '1.0')  # Piper's own pace; it sounded too fast only while the car played 16 kHz at 48 kHz
SENTENCE_PAUSE_S = '0.4'
SAMPLE_WIDTH = 2
SPEECH_DIR = os.path.join(pc_settings.VOICE_DIR, 'tmp')
assert set(VOICES) == set(pc_settings.voice_protocol.LANGUAGES)

os.makedirs(SPEECH_DIR, exist_ok=True)
_pipers = {}
_lock = threading.Lock()


def _piper(language):
    '''Piper stays loaded (starting it costs about 1 s): each input line becomes a wav file, whose path it prints when done.'''
    process = _pipers.get(language)
    if process is None or process.poll() is not None:
        process = _pipers[language] = subprocess.Popen(
            [PIPER, '--model', os.path.join(pc_settings.VOICE_DIR, 'piper', VOICES[language] + '.onnx'), '--output_dir', SPEECH_DIR,
             '--length_scale', PIPER_PACE, '--sentence_silence', SENTENCE_PAUSE_S],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return process


def synthesize(sentence, language, rate=pc_settings.voice_protocol.DEFAULT_SPEECH_RATE):
    with _lock:
        process = _piper(language)
        process.stdin.write((' '.join(sentence.replace('*', ' ').split()) + '\n').encode('utf-8'))
        process.stdin.flush()
        path = process.stdout.readline().decode('utf-8').strip()
    with wave.open(path, 'rb') as speech:
        audio, voice_rate = speech.readframes(speech.getnframes()), speech.getframerate()
    os.remove(path)
    if voice_rate != rate:
        audio = audioop.ratecv(audio, SAMPLE_WIDTH, 1, voice_rate, rate, None)[0]
    return audio
