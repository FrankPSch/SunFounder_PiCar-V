'''
The microphone. It can be opened by one process only, so one capture thread (`arecord`, 16 kHz mono 16-bit,
0.1 s chunks) feeds every consumer of a chunk:
  sound_level     level in dBFS for the dashboard
  listeners.py    clap_stop, noise_alarm
  word_recognizer stop_word, voice_commands, claude_voice phrases (vocabulary.SPOKEN)
  conversation    claude_voice: the sentences after "hey claude"
  record_audio    a .wav next to a video (media.py)
The capture runs only while at least one of these is on.
'''
import collections
import concurrent.futures
import os
import subprocess
import threading
import time
import wave

from . import alsa, events, features, learning, listeners, vocabulary, voice_protocol, word_recognizer
from .speech_level import SAMPLE_WIDTH, level_dbfs
from .conversation import conversation

SAMPLE_RATE = voice_protocol.UTTERANCE_RATE
CHUNK_BYTES = SAMPLE_RATE // 10 * SAMPLE_WIDTH
SILENCE_DBFS = -90.0
RECENT_CHUNKS = 20          # 2 s: the loudest of these must reach the speech level for a phrase to count
SPEECH_MODELS = {language: os.path.join(features.DATA_DIR, 'models', folder) for language, folder in vocabulary.MODEL_FOLDERS.items()}
AUDIO_FEATURES = ('sound_level', 'clap_stop', 'noise_alarm', 'stop_word', 'voice_commands', 'claude_voice')
IDLE_POLL_S = 0.5
NO_DEVICE_RETRY_S = 5
RESTART_PAUSE_S = 1
ERROR_TAIL_CHARS = 200


class Microphone(object):
    def __init__(self):
        self.status = {'running': False, 'level_dbfs': None, 'error': None, 'speech_load': None, 'device': None}
        self.clap_stop, self.noise_alarm = listeners.ClapStop(), listeners.NoiseAlarm()
        self.words = word_recognizer.WordRecognizer(SPEECH_MODELS)
        self._wav = None
        self._wav_lock = threading.Lock()
        self._speech_pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(SPEECH_MODELS))
        threading.Thread(target=self._loop, name='microphone', daemon=True).start()

    def wanted(self):
        return any(features.enabled(name) for name in AUDIO_FEATURES) or self._wav is not None or learning.session.active()

    # ---- wav recording (called by media.py) ----
    def start_wav(self, path):
        with self._wav_lock:
            self._wav = wave.open(path, 'wb')
            self._wav.setnchannels(1)
            self._wav.setsampwidth(SAMPLE_WIDTH)
            self._wav.setframerate(SAMPLE_RATE)

    def stop_wav(self):
        with self._wav_lock:
            if self._wav:
                self._wav.close()
                self._wav = None

    # ---- capture ----
    def _loop(self):
        while True:
            if not self.wanted():
                self.status.update(running=False, level_dbfs=None)
                time.sleep(IDLE_POLL_S)
                continue
            device = events.settings().get('mic_device') or alsa.usb_device('arecord')
            self.status['device'] = device
            if device is None:
                self.status.update(running=False, error='no microphone found')
                time.sleep(NO_DEVICE_RETRY_S)
                continue
            alsa.set_mic_gain(device)
            self._record(device)
            time.sleep(RESTART_PAUSE_S)

    def _record(self, device):
        command = ['arecord', '-q', '-D', device, '-f', 'S16_LE', '-r', str(SAMPLE_RATE), '-c', '1', '-t', 'raw']
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            self.status.update(running=False, error='arecord: %s' % error)
            time.sleep(NO_DEVICE_RETRY_S)
            return
        self.status.update(running=True, error=None)
        self._consume(process)
        process.kill()
        error_text = process.stderr.read().decode('utf-8', 'replace').strip()
        if error_text:
            self.status['error'] = error_text[-ERROR_TAIL_CHARS:]
        self.status['running'] = False

    def _consume(self, process):
        recent_levels = collections.deque(maxlen=RECENT_CHUNKS)
        grammar = None
        while self.wanted():
            chunk = process.stdout.read(CHUNK_BYTES)
            if len(chunk) < CHUNK_BYTES:
                return
            now, level = time.time(), level_dbfs(chunk)
            self.status['level_dbfs'] = level
            recent_levels.append(level)
            self._write_wav(chunk)
            learning.session.feed(chunk)
            self.clap_stop.feed(chunk, level, now)
            self.noise_alarm.feed(chunk, level, now)
            if features.enabled('claude_voice') or conversation.active():
                conversation.feed(chunk, level)
            if word_recognizer.wanted() != grammar:
                grammar = word_recognizer.wanted()
                self.words.rebuild(grammar)
            self._recognize(chunk, max(recent_levels), now)

    def _write_wav(self, chunk):
        with self._wav_lock:
            if self._wav:
                self._wav.writeframes(chunk)

    def _recognize(self, chunk, peak, started):
        recognizers = self.words.recognizers()
        if recognizers:
            list(self._speech_pool.map(lambda item: self.words.recognize(item[0], item[1], chunk, peak), recognizers))
            self.status['speech_load'] = round((time.time() - started) / (CHUNK_BYTES / float(SAMPLE_WIDTH) / SAMPLE_RATE), 2)

    def describe(self):
        status = dict(self.status, claps=self.clap_stop.claps, alarms=self.noise_alarm.alarms, conversation=conversation.describe(), learning=learning.session.describe(),
                      vosk_model=os.path.isdir(SPEECH_MODELS['en']),
                      installed_languages=[language for language, model_dir in SPEECH_MODELS.items() if os.path.isdir(model_dir)])
        words = self.words.describe()
        status.update(words, error=self.status['error'] or words['error'])
        return status


microphone = Microphone()
start_wav, stop_wav = microphone.start_wav, microphone.stop_wav
