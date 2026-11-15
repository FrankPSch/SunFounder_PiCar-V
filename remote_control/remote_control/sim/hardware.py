'''
The car's hardware in the digital twin (PICAR_SIM=1), next to fake_picar.py (servos, motor) and world.py (camera):
  readings()            what telemetry reads on the Pi: temperature, clock, throttling, memory, disk, Wi-Fi, I2C, devices
                        car-like defaults; a test changes them by writing DATA_DIR/twin_hardware.json ({"disk": {...}})
  MicrophoneProcess     stands in for arecord: plays the .wav files a test drops into DATA_DIR/twin_microphone/, else silence
  spoken()              the speaker: every sentence the car says goes to DATA_DIR/twin_speaker.jsonl instead of a sound card
  chown(), owner()      file owners as on the car (Windows has none): DATA_DIR/twin_owners.json, relative path -> user
'''
import json
import os
import time
import wave

ACTIVE = os.environ.get('PICAR_SIM') == '1'
DATA_DIR = os.environ.get('PICAR_DATA', '/home/pi/picar-data')
HARDWARE_FILE = os.path.join(DATA_DIR, 'twin_hardware.json')
MICROPHONE_DIR = os.path.join(DATA_DIR, 'twin_microphone')
SPEAKER_FILE = os.path.join(DATA_DIR, 'twin_speaker.jsonl')
OWNERS_FILE = os.path.join(DATA_DIR, 'twin_owners.json')
DEVICE = 'twin'

# measured on the real car, 2026-11-15
READINGS = {
    'cpu_temp_c': 48.0, 'cpu_mhz': 1400, 'load': [0.8, 0.7, 0.6], 'uptime_s': 3600, 'ip': '192.168.178.131',
    'throttled': {'flags': '0x0', 'undervoltage_now': False, 'throttled_now': False, 'undervoltage_since_boot': False},
    'memory': {'total_mb': 926, 'available_mb': 520, 'used_pct': 43.8},
    'disk': {'total_gb': 62.3, 'free_gb': 54.8, 'used_pct': 8.0},
    'wifi': {'ssid': 'twin', 'level_dbm': -55, 'tx_bitrate': '72.2 MBit/s'},
    'services': {'picar': 'active', 'picar-update.timer': 'active'},
    'i2c': {'PCA9685 servo/PWM': True, 'PCF8591 ADC': True},
    'camera': {'device': True, 'streamer': True},
    'microphone': {'present': True, 'name': 'Jabra SPEAK 510 USB (twin)'},
}


def _load(path, empty):
    try:
        with open(path, encoding='utf-8') as source:
            return json.load(source)
    except (OSError, ValueError):
        return empty


def readings():
    return dict(READINGS, **_load(HARDWARE_FILE, {}))


class _Silence(object):
    def read(self):
        return b''


class MicrophoneProcess(object):
    '''The part of a Popen(arecord ...) that audio.Microphone uses, in real time: 16 kHz mono 16-bit chunks.'''
    stderr = _Silence()

    def __init__(self, rate):
        self._rate, self._queued = rate, b''
        self.stdout = self

    def read(self, size):
        time.sleep(size / 2.0 / self._rate)
        if len(self._queued) < size:
            self._queued += self._next_recording()
        chunk, self._queued = self._queued[:size], self._queued[size:]
        return chunk + bytes(size - len(chunk))

    def _next_recording(self):
        names = sorted(name for name in os.listdir(MICROPHONE_DIR) if name.endswith('.wav')) if os.path.isdir(MICROPHONE_DIR) else []
        if not names:
            return b''
        path = os.path.join(MICROPHONE_DIR, names[0])
        with wave.open(path, 'rb') as recording:
            audio = recording.readframes(recording.getnframes())
        os.remove(path)             # played once
        return audio

    def kill(self):
        pass


def spoken(text, language, voice):
    with open(SPEAKER_FILE, 'a', encoding='utf-8') as speaker:
        speaker.write(json.dumps({'time': time.strftime('%H:%M:%S'), 'text': text, 'language': language, 'voice': voice}, ensure_ascii=False) + '\n')


def chown(path, user):
    owners = _load(OWNERS_FILE, {})
    owners[os.path.relpath(path, DATA_DIR).replace(os.sep, '/')] = user
    with open(OWNERS_FILE, 'w', encoding='utf-8') as saved:
        json.dump(owners, saved, indent=1, sort_keys=True)


def owner(path):
    return _load(OWNERS_FILE, {}).get(os.path.relpath(path, DATA_DIR).replace(os.sep, '/'))
