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
# Event replies (events.add(..., say=...)) are German, so the car speaks with one voice (Piper Thorsten)
EVENT_LANGUAGE = 'de'
VOICE_SERVER = {'start': 'Claude ist bereit.', 'stop': 'Claude ist aus.'}
EVENTS = {'clap_stop': 'Stopp.', 'recording': 'Aufnahme läuft.', 'qr_code': 'QR-Code gefunden.', 'treasure_found': 'Gut gemacht!'}
TREASURE_COLORS = {'red': 'Rot', 'orange': 'Orange', 'yellow': 'Gelb', 'green': 'Grün', 'blue': 'Blau', 'purple': 'Lila'}  # tracker.COLOR_PRESETS (tests/test_voice_cache.py)
TREASURE = 'Finde etwas in %s.'
ROOMS = {'going': 'Ich fahre zu %s.', 'arrived': 'Angekommen: %s.', 'failed': 'Ich finde %s nicht.'}  # room names are not recorded: espeak
MAX_NUMBER = 100            # numbers said on their own, for sentences like "31 von 33 verstanden."


def command_reply(spoken):
    '''What the car says after a spoken command: its first German phrase ("kamera mitte").'''
    return spoken.de[0]


def treasure(color):
    return TREASURE % TREASURE_COLORS[color]


def _template_words(template):
    return [word for word in template.split() if '%' not in word]


def sentences():
    '''{(language, text)} of everything said with fixed words.'''
    said = {STARTUP} | {('de', text) for text in UPDATE.values()}
    said |= {(EVENT_LANGUAGE, text) for text in tuple(VOICE_SERVER.values()) + tuple(EVENTS.values())}
    said |= {(EVENT_LANGUAGE, treasure(color)) for color in TREASURE_COLORS}
    said |= {(EVENT_LANGUAGE, command_reply(spoken)) for spoken in vocabulary.SPOKEN}
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
