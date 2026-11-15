'''Everything the car says with fixed words, in one place: tools/prerender_voice.py records it in advance with the
natural Piper voice of the PC (voice_cache.py), the car plays the recordings offline. Text not recorded (a misheard
word, a room name) is spoken by espeak-ng. No hardware here.

The modules that speak take their texts from here. tests/test_voice_cache.py checks that every text they say
is in sentences(), including the ones in tools/picar-update.sh.
'''
from . import phrase_check, vocabulary

STARTUP = ('de', 'Ich bin bereit.')                       # api.py: every start of the car software
UPDATE = {'start': 'Update startet. Ich bin gleich wieder da.', 'done': 'Update fertig.',       # tools/picar-update.sh, German
          'rolled_back': 'Update fehlgeschlagen. Die alte Version läuft wieder.', 'broken': 'Update fehlgeschlagen. Ich laufe nicht mehr richtig.'}
LEARNING = {'start': {'de': 'Wir lernen die Befehle. Sprich jeden nach dem Piepton nach.', 'en': 'Let us learn the commands. Repeat each one after the beep.'},
            'end': {'de': 'Lernen beendet.', 'en': 'Learning finished.'},
            'score': {'de': '%d von %d verstanden.', 'en': '%d of %d understood.'}}
# Event replies (events.add(..., say=...)) are English
VOICE_SERVER = {'start': 'claude ready', 'stop': 'claude off'}
EVENT_REPLIES = ('stop', 'recording', 'QR code', 'well done', 'going to')
TREASURE_COLORS = ('red', 'orange', 'yellow', 'green', 'blue', 'purple')  # tracker.COLOR_PRESETS (checked in tests/test_voice_cache.py)
TREASURE = 'find something %s'
MAX_NUMBER = 100            # numbers said on their own, for sentences like "31 von 33 verstanden."


def command_reply(spoken):
    '''What the car says after a spoken command: its name, English.'''
    return spoken.name.replace('_', ' ')


def _template_words(template):
    return [word for word in template.split() if '%' not in word]


def sentences():
    '''{(language, text)} of everything said with fixed words.'''
    said = {STARTUP} | {('de', text) for text in UPDATE.values()}
    said |= {('en', text) for text in tuple(VOICE_SERVER.values()) + EVENT_REPLIES}
    said |= {('en', TREASURE % color) for color in TREASURE_COLORS}
    said |= {('en', command_reply(spoken)) for spoken in vocabulary.SPOKEN}
    for language in vocabulary.LANGUAGES:
        said |= {(language, LEARNING[key][language]) for key in ('start', 'end')}
        said |= {(language, word) for word in _template_words(LEARNING['score'][language])}
        said |= {(language, str(number)) for number in range(MAX_NUMBER + 1)}
        said |= {(language, verdict.value[language]) for verdict in phrase_check.Verdict}
        said |= {(language, names[language]) for names in vocabulary.GROUP_NAMES.values()}
        said |= {(language, vocabulary.help_text(language))}
        for spoken in vocabulary.SPOKEN:
            for phrase in spoken.phrases(language):
                said |= {(language, phrase), (language, '%s. %s' % (phrase, vocabulary.explanation(spoken, language)))}
    return said
