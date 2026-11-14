'''Sound listeners without words: a double clap stops the motors (clap_stop), a loud noise takes a photo (noise_alarm).
Each gets every 0.1 s microphone chunk with its level from audio.py.'''
import audioop

from . import events, features, media
from .car import car

CLAP_PEAK = 20000           # of 32767
CLAP_QUIET_DBFS = -35       # level before a clap must be below this
CLAP_GAP_S = (0.15, 0.8)
QUIET_SMOOTHING = 0.7
NOISE_DBFS = -18
NOISE_CHUNKS = 3
NOISE_COOLDOWN_S = 30
SAMPLE_WIDTH = 2


class ClapStop(object):
    def __init__(self):
        self.claps, self._quiet_level, self._last_clap = 0, -90.0, 0.0

    def feed(self, chunk, level, now):
        if features.enabled('clap_stop') and audioop.max(chunk, SAMPLE_WIDTH) >= CLAP_PEAK and self._quiet_level < CLAP_QUIET_DBFS:
            if CLAP_GAP_S[0] <= now - self._last_clap <= CLAP_GAP_S[1]:
                self.claps += 1
                car.stop('double clap')
                events.add('audio', 'double clap -> STOP', say='stop')
                self._last_clap = 0.0
            else:
                self._last_clap = now
        self._quiet_level = QUIET_SMOOTHING * self._quiet_level + (1 - QUIET_SMOOTHING) * level


class NoiseAlarm(object):
    def __init__(self):
        self.alarms, self._loud_chunks, self._last_alarm = 0, 0, 0.0

    def feed(self, chunk, level, now):
        if not features.enabled('noise_alarm'):
            return
        self._loud_chunks = self._loud_chunks + 1 if level >= NOISE_DBFS else 0
        if self._loud_chunks >= NOISE_CHUNKS and now - self._last_alarm > NOISE_COOLDOWN_S:
            self._last_alarm, self._loud_chunks = now, 0
            self.alarms += 1
            self._take_photo(level)

    @staticmethod
    def _take_photo(level):
        try:
            name = media.snapshot('alarm')
        except OSError:
            name = 'no photo'
        events.add('audio', 'noise alarm %.0f dBFS (%s)' % (level, name), push=True)
