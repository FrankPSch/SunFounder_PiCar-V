'''
Microphone features. The USB mic can be opened by one process only, so one capture thread
(`arecord`, 16 kHz mono 16-bit, 0.1 s chunks) feeds every consumer:
  sound_level  - RMS level in dBFS for the dashboard
  clap_stop    - two sharp peaks 0.15-0.8 s apart stop the motors
  noise_alarm  - level above NOISE_DBFS for 0.3 s -> photo + event (+ push)
  stop_word    - Vosk keyword "stop"/"halt" stops the motors (acts on partial results, fast)
  voice_commands - Vosk with a small fixed grammar (VOICE_COMMANDS)
  record_audio - writes the chunks to a .wav while a video records
The capture runs only while at least one of these is on.
'''
import audioop
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
DEFAULT_MIC_DEVICE = 'plughw:CARD=Device,DEV=0'
MIC_MIXER = ['amixer', '-q', '-c', 'Device', 'sset', 'Mic', '100%']  # capture volume boots at 0 % (peak 55 of 32767 measured), 100 % = +23.8 dB
VOSK_MODEL_DIR = os.path.join(features.DATA_DIR, 'models', 'vosk-model-small-en-us-0.15')

CLAP_PEAK = 20000           # of 32767
CLAP_QUIET_DBFS = -35       # level before a clap must be below this
CLAP_GAP_S = (0.15, 0.8)
NOISE_DBFS = -18
NOISE_CHUNKS = 3
NOISE_COOLDOWN_S = 30
STOP_WORDS = ('stop', 'halt')
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
    'forward': lambda: _drive_burst(1), 'go': lambda: _drive_burst(1), 'back': lambda: _drive_burst(-1),
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


class Microphone(object):
    def __init__(self):
        self.status = {'running': False, 'level_dbfs': None, 'error': None, 'heard': None, 'heard_time': None,
                       'claps': 0, 'alarms': 0}
        self._wav = None
        self._wav_lock = threading.Lock()
        self._vosk_model = None
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
            device = events.settings().get('mic_device', DEFAULT_MIC_DEVICE)
            try:
                subprocess.run(MIC_MIXER, stderr=subprocess.DEVNULL)
            except OSError:
                pass  # no amixer (simulation)
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
        recognizer, grammar_key = None, None
        quiet_level, last_clap, loud_chunks, last_alarm = -90.0, 0.0, 0, 0.0
        while self.wanted():
            chunk = process.stdout.read(CHUNK_BYTES)
            if len(chunk) < CHUNK_BYTES:
                return
            now = time.time()
            rms = audioop.rms(chunk, 2)
            level = round(20 * math.log10(max(rms, 1) / 32768.0), 1)
            self.status['level_dbfs'] = level
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
                recognizer, grammar_key = self._make_recognizer(wanted_grammar), wanted_grammar
            if recognizer:
                self._recognize(recognizer, chunk)

    def _noise_alarm(self, level):
        from . import media
        try:
            name = media.snapshot('alarm')
        except OSError:
            name = 'no photo'
        events.add('audio', 'noise alarm %.0f dBFS (%s)' % (level, name), push=True)

    # ---- Vosk ----
    def _make_recognizer(self, grammar_key):
        if grammar_key is None:
            return None
        try:
            import vosk
            if self._vosk_model is None:
                vosk.SetLogLevel(-1)
                self._vosk_model = vosk.Model(VOSK_MODEL_DIR)
        except Exception as error:
            self.status['error'] = 'vosk not ready (%s) - run tools/install_extras.sh' % error
            return None
        phrases = list(STOP_WORDS) + (sorted(VOICE_COMMANDS) if grammar_key == 'commands' else [])
        return vosk.KaldiRecognizer(self._vosk_model, SAMPLE_RATE, json.dumps(phrases + ['[unk]']))

    def _recognize(self, recognizer, chunk):
        if recognizer.AcceptWaveform(chunk):
            text = json.loads(recognizer.Result()).get('text', '')
            if text and text != '[unk]':
                self._heard(text)
        else:
            partial = json.loads(recognizer.PartialResult()).get('partial', '')
            if any(word in partial.split() for word in STOP_WORDS) and car.drive_direction:
                car.stop('voice stop')
                events.add('audio', 'heard "stop" -> STOP')

    def _heard(self, text):
        self.status.update(heard=text, heard_time=time.strftime('%H:%M:%S'))
        if any(word in text.split() for word in STOP_WORDS) and 'recording' not in text:
            car.stop('voice stop')
            events.add('audio', 'heard "%s" -> STOP' % text)
            return
        action = VOICE_COMMANDS.get(text) if features.enabled('voice_commands') else None
        if action:
            action()
            events.add('audio', 'voice command: ' + text, say=text)

    def describe(self):
        return dict(self.status, vosk_model=os.path.isdir(VOSK_MODEL_DIR), commands=sorted(VOICE_COMMANDS))


microphone = Microphone()
start_wav, stop_wav = microphone.start_wav, microphone.stop_wav
