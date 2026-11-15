'''Voice values shared by the word recogniser and the conversation: languages and the level at which sound counts as speech.'''
from . import events, voice_protocol
from .car import car
from .speech_level import SPEECH_DBFS

DEFAULT_VOICE_LANGUAGES = 'de'


def speech_dbfs():
    return SPEECH_DBFS['driving' if car.drive_direction else 'parked']


def parked_speech_dbfs():
    '''For stop words: a stop on motor noise does no harm, a missed stop does.'''
    return SPEECH_DBFS['parked']


def voice_languages():
    '''Setting voice_languages ("de", "de,en"): the car's word recognisers, and the languages Whisper may pick on the PC.'''
    names = [name.strip() for name in str(events.settings().get('voice_languages', DEFAULT_VOICE_LANGUAGES)).split(',')]
    return [name for name in names if name in voice_protocol.LANGUAGES] or [DEFAULT_VOICE_LANGUAGES]
