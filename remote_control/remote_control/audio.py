'''
Microphone features. The USB mic can be opened by one process only, so one capture thread
(`arecord`, 16 kHz mono 16-bit, 0.1 s chunks) feeds every consumer:
  sound_level  - RMS level in dBFS for the dashboard
  clap_stop    - two sharp peaks 0.15-0.8 s apart stop the motors
  noise_alarm  - level above NOISE_DBFS for 0.3 s -> photo + event (+ push)
  stop_word    - Vosk keywords (STOP_WORDS, English + German) stop the motors (act on partial results, fast)
  voice_commands - Vosk with small fixed word lists (VOICE_PHRASES); one recogniser per installed language
  record_audio - writes the chunks to a .wav while a video records
The capture runs only while at least one of these is on.
'''
import audioop
import collections
import concurrent.futures
import json
import math
import os
import subprocess
import threading
import time
import wave

from . import events, features
from .car import car

SAMPLE_RATE = 16000
CHUNK_BYTES = SAMPLE_RATE // 10 * 2
OLD_MIC_MIXER = ['amixer', '-q', '-c', 'Device', 'sset', 'Mic', '100%']  # old USB mic: capture volume boots at 0 % (peak 55 of 32767 measured)
SPEECH_MODELS = {language: os.path.join(features.DATA_DIR, 'models', folder)
                 for language, folder in (('en', 'vosk-model-small-en-us-0.15'), ('de', 'vosk-model-small-de-0.15'))}

CLAP_PEAK = 20000           # of 32767
CLAP_QUIET_DBFS = -35       # level before a clap must be below this
CLAP_GAP_S = (0.15, 0.8)
NOISE_DBFS = -18
NOISE_CHUNKS = 3
NOISE_COOLDOWN_S = 30
STOP_WORDS = {'en': ('stop', 'halt'), 'de': ('stopp', 'halt', 'anhalten')}
# A grammar-limited recogniser maps room noise onto its nearest word ("halt" twice a minute at -63 dBFS),
# so a word only counts when it was loud enough and the recogniser is sure. Rejected words: status['rejected'].
MIN_SPEECH_DBFS = -45       # loudest chunk of the last RECENT_CHUNKS must reach this
RECENT_CHUNKS = 20          # 2 s
MIN_WORD_CONF = 0.8
REPEAT_IGNORE_S = 1.5       # both language recognisers may hear the same word
AUDIO_FEATURES = ('sound_level', 'clap_stop', 'noise_alarm', 'stop_word', 'voice_commands')


def _drive_burst(direction):
    '''Voice "forward"/"back": drive about 1.5 s, the watchdog stops it afterwards.'''
    car.drive(direction)

    def keep():
        for _ in range(7):
            time.sleep(0.2)
            car.keepalive()
    threading.Thread(target=keep, daemon=True).start()


def _vision_follow(feature_name):
    features.set_enabled(feature_name, True)


def _media(action):
    from . import media
    getattr(media, action)()


VOICE_COMMANDS = {
    'forward': lambda: _drive_burst(1), 'back': lambda: _drive_burst(-1),
    'left': lambda: car.set_steer(car.steer_angle - 20), 'right': lambda: car.set_steer(car.steer_angle + 20),
    'straight': lambda: car.set_steer(90),
    'faster': lambda: car.set_speed(car.speed + 10), 'slower': lambda: car.set_speed(car.speed - 10),
    'camera left': lambda: car.move_cam(10, 0), 'camera right': lambda: car.move_cam(-10, 0),
    'camera up': lambda: car.move_cam(0, 10), 'camera down': lambda: car.move_cam(0, -10),
    'center': car.center_cam, 'ready': car.ready,
    'photo': lambda: _media('snapshot'), 'record': lambda: _media('start_recording'),
    'stop recording': lambda: _media('stop_recording'),
    'follow ball': lambda: _vision_follow('ball_tracking'), 'follow face': lambda: _vision_follow('face_tracking'),
    'treasure hunt': lambda: features.set_enabled('treasure_hunt', True),
}

# Spoken phrase -> command. Every word was checked against the model's vocabulary (a missing word is silently dropped).
VOICE_PHRASES = {
    'en': {'forward': 'forward', 'forwards': 'forward', 'go': 'forward',
           'back': 'back', 'backward': 'back', 'backwards': 'back', 'reverse': 'back',
           'left': 'left', 'right': 'right', 'straight': 'straight', 'faster': 'faster', 'slower': 'slower',
           'camera left': 'camera left', 'camera right': 'camera right', 'camera up': 'camera up', 'camera down': 'camera down',
           'center': 'center', 'centre': 'center', 'ready': 'ready', 'photo': 'photo', 'picture': 'photo',
           'record': 'record', 'stop recording': 'stop recording',
           'follow ball': 'follow ball', 'follow face': 'follow face', 'treasure hunt': 'treasure hunt'},
    'de': {'vorwärts': 'forward', 'los': 'forward', 'rückwärts': 'back', 'zurück': 'back',
           'links': 'left', 'rechts': 'right', 'geradeaus': 'straight', 'schneller': 'faster', 'langsamer': 'slower',
           'kamera links': 'camera left', 'kamera rechts': 'camera right', 'kamera hoch': 'camera up', 'kamera runter': 'camera down',
           'mitte': 'center', 'startklar': 'ready',  # not "bereit": the German model hears it in English "straight"
           'foto': 'photo', 'bild': 'photo',
           'aufnahme': 'record', 'aufnahme stopp': 'stop recording',
           'folge ball': 'follow ball', 'folge gesicht': 'follow face', 'schatzsuche': 'treasure hunt'},
}
for _phrases in VOICE_PHRASES.values():
    assert set(_phrases.values()) == set(VOICE_COMMANDS), 'phrase list out of step with VOICE_COMMANDS'
assert set(VOICE_PHRASES) == set(STOP_WORDS) == set(SPEECH_MODELS)


class Microphone(object):
    def __init__(self):
        self.status = {'running': False, 'level_dbfs': None, 'error': None, 'heard': None, 'heard_time': None,
                       'claps': 0, 'alarms': 0, 'rejected': None, 'rejected_count': 0, 'speech_load': None, 'device': None}
        self._wav = None
        self._wav_lock = threading.Lock()
        self._vosk_models = {}
        self._speech_pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(SPEECH_MODELS))
        self._last_command = (None, 0.0)
        threading.Thread(target=self._loop, name='microphone', daemon=True).start()

    def wanted(self):
        return any(features.enabled(name) for name in AUDIO_FEATURES) or self._wav is not None

    # ---- wav recording (called by media.py) ----
    def start_wav(self, path):
        with self._wav_lock:
            self._wav = wave.open(path, 'wb')
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(SAMPLE_RATE)

    def stop_wav(self):
        with self._wav_lock:
            if self._wav:
                self._wav.close()
                self._wav = None

    # ---- capture loop ----
    def _loop(self):
        while True:
            if not self.wanted():
                self.status.update(running=False, level_dbfs=None)
                time.sleep(0.5)
                continue
            device = events.settings().get('mic_device') or events.audio_device('arecord')
            self.status['device'] = device
            if device is None:
                self.status.update(running=False, error='no microphone found')
                time.sleep(5)
                continue
            if 'CARD=Device' in device:
                subprocess.run(OLD_MIC_MIXER, stderr=subprocess.DEVNULL)
            command = ['arecord', '-q', '-D', device, '-f', 'S16_LE', '-r', str(SAMPLE_RATE), '-c', '1', '-t', 'raw']
            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as error:
                self.status.update(running=False, error='arecord: %s' % error)
                time.sleep(5)
                continue
            self.status.update(running=True, error=None)
            self._consume(process)
            process.kill()
            error_text = process.stderr.read().decode('utf-8', 'replace').strip()
            if error_text:
                self.status['error'] = error_text[-200:]
            self.status['running'] = False
            time.sleep(1)

    def _consume(self, process):
        recognizers, grammar_key = [], None
        quiet_level, last_clap, loud_chunks, last_alarm = -90.0, 0.0, 0, 0.0
        recent_levels = collections.deque(maxlen=RECENT_CHUNKS)
        while self.wanted():
            chunk = process.stdout.read(CHUNK_BYTES)
            if len(chunk) < CHUNK_BYTES:
                return
            now = time.time()
            rms = audioop.rms(chunk, 2)
            level = round(20 * math.log10(max(rms, 1) / 32768.0), 1)
            self.status['level_dbfs'] = level
            recent_levels.append(level)
            with self._wav_lock:
                if self._wav:
                    self._wav.writeframes(chunk)

            if features.enabled('clap_stop'):
                if audioop.max(chunk, 2) >= CLAP_PEAK and quiet_level < CLAP_QUIET_DBFS:
                    if CLAP_GAP_S[0] <= now - last_clap <= CLAP_GAP_S[1]:
                        self.status['claps'] += 1
                        car.stop('double clap')
                        events.add('audio', 'double clap -> STOP', say='stop')
                        last_clap = 0.0
                    else:
                        last_clap = now
            quiet_level = 0.7 * quiet_level + 0.3 * level

            if features.enabled('noise_alarm'):
                loud_chunks = loud_chunks + 1 if level >= NOISE_DBFS else 0
                if loud_chunks >= NOISE_CHUNKS and now - last_alarm > NOISE_COOLDOWN_S:
                    last_alarm, loud_chunks = now, 0
                    self.status['alarms'] += 1
                    self._noise_alarm(level)

            wanted_grammar = 'commands' if features.enabled('voice_commands') else ('stop' if features.enabled('stop_word') else None)
            if wanted_grammar != grammar_key:
                recognizers, grammar_key = self._make_recognizers(wanted_grammar), wanted_grammar
            if recognizers:
                peak = max(recent_levels)
                list(self._speech_pool.map(lambda item: self._recognize(item[0], item[1], chunk, peak), recognizers))
                self.status['speech_load'] = round((time.time() - now) / (CHUNK_BYTES / 2.0 / SAMPLE_RATE), 2)

    def _noise_alarm(self, level):
        from . import media
        try:
            name = media.snapshot('alarm')
        except OSError:
            name = 'no photo'
        events.add('audio', 'noise alarm %.0f dBFS (%s)' % (level, name), push=True)

    # ---- Vosk ----
    def _make_recognizers(self, grammar_key):
        if grammar_key is None:
            return []
        recognizers = []
        try:
            import vosk
            vosk.SetLogLevel(-1)
            for language, model_dir in SPEECH_MODELS.items():
                if not os.path.isdir(model_dir):
                    continue
                if language not in self._vosk_models:
                    self._vosk_models[language] = vosk.Model(model_dir)
                phrases = list(STOP_WORDS[language]) + (sorted(VOICE_PHRASES[language]) if grammar_key == 'commands' else [])
                # ensure_ascii=False: an escaped umlaut is not found in the vocabulary and the word is silently dropped
                recognizer = vosk.KaldiRecognizer(self._vosk_models[language], SAMPLE_RATE, json.dumps(phrases + ['[unk]'], ensure_ascii=False))
                recognizer.SetWords(True)
                recognizers.append((language, recognizer))
        except Exception as error:
            self.status['error'] = 'vosk not ready (%s) - run tools/install_extras.sh' % error
            return []
        if not recognizers:
            self.status['error'] = 'no speech model - run tools/install_extras.sh'
        return recognizers

    def _recognize(self, language, recognizer, chunk, recent_peak_dbfs):
        if events.speaking.is_set():  # the car's own voice prompt must not trigger a command
            recognizer.Reset()
            return
        if recognizer.AcceptWaveform(chunk):
            # "[unk]" stands for speech outside the list: "[unk] links" still means "links"
            words = [word for word in json.loads(recognizer.Result()).get('result', []) if word['word'] != '[unk]']
            if words:
                text = ' '.join(word['word'] for word in words)
                confidence = min(word.get('conf', 0) for word in words)
                if recent_peak_dbfs >= MIN_SPEECH_DBFS and confidence >= MIN_WORD_CONF:
                    self._heard(language, text)
                else:
                    self.status['rejected'] = '%s %s conf %.2f peak %.0f dBFS' % (time.strftime('%H:%M:%S'), text, confidence, recent_peak_dbfs)
                    self.status['rejected_count'] += 1
        else:
            partial = json.loads(recognizer.PartialResult()).get('partial', '')
            if any(word in partial.split() for word in STOP_WORDS[language]) and car.drive_direction and recent_peak_dbfs >= MIN_SPEECH_DBFS:
                car.stop('voice stop')
                events.add('audio', 'heard "%s" -> STOP' % partial)

    def _heard(self, language, text):
        self.status.update(heard=text, heard_time=time.strftime('%H:%M:%S'))
        command = VOICE_PHRASES[language].get(text) if features.enabled('voice_commands') else None
        if command:
            now = time.time()
            if command == self._last_command[0] and now - self._last_command[1] < REPEAT_IGNORE_S:
                return
            self._last_command = (command, now)
            VOICE_COMMANDS[command]()
            events.add('audio', 'voice command: %s -> %s' % (text, command), say=command)
        elif any(word in text.split() for word in STOP_WORDS[language]):
            car.stop('voice stop')
            events.add('audio', 'heard "%s" -> STOP' % text)

    def describe(self):
        return dict(self.status, vosk_model=os.path.isdir(SPEECH_MODELS['en']),
                    languages=[language for language, model_dir in SPEECH_MODELS.items() if os.path.isdir(model_dir)],
                    phrases={language: sorted(phrases) for language, phrases in VOICE_PHRASES.items()})


microphone = Microphone()
start_wav, stop_wav = microphone.start_wav, microphone.stop_wav
