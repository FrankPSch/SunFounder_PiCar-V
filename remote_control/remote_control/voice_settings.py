'''Voice values shared by the word recogniser and the conversation: languages and the level at which sound counts as speech.'''
from . import events, voice_protocol
from .car import car

DEFAULT_VOICE_LANGUAGES = 'de'
# A 0.1 s chunk this loud counts as speech. Measured 2026-11-14, Jabra mic gain 7/7 (alsa.MIC_MIXERS): room -59..-60,
# owner's voice -22..-36 parked; motor -24..-29 while driving, voice on top -18..-21.
SPEECH_DBFS = {'parked': -48, 'driving': -22}


def speech_dbfs():
    return SPEECH_DBFS['driving' if car.drive_direction else 'parked']


def parked_speech_dbfs():
    '''For stop words: a stop on motor noise does no harm, a missed stop does.'''
    return SPEECH_DBFS['parked']


def voice_languages():
    '''Setting voice_languages ("de", "de,en"): the car's word recognisers, and the languages Whisper may pick on the PC.'''
    names = [name.strip() for name in str(events.settings().get('voice_languages', DEFAULT_VOICE_LANGUAGES)).split(',')]
    return [name for name in names if name in voice_protocol.LANGUAGES] or [DEFAULT_VOICE_LANGUAGES]
