'''Loudness of a microphone chunk (16-bit mono) in dBFS, and the level at which it counts as speech.'''
import audioop
import math

SAMPLE_WIDTH = 2
FULL_SCALE = 32768.0
# A 0.1 s chunk this loud counts as speech. Measured 2026-11-14, Jabra mic gain 7/7 (alsa.MIC_MIXERS): room -59..-60,
# owner's voice -22..-36 parked; motor -24..-29 while driving, voice on top -18..-21.
SPEECH_DBFS = {'parked': -48, 'driving': -22}


def level_dbfs(chunk):
    return round(20 * math.log10(max(audioop.rms(chunk, SAMPLE_WIDTH), 1) / FULL_SCALE), 1)
