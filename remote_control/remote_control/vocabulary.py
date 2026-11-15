'''The spoken commands: phrases in German and English, what each one does, and how a recogniser result becomes
a phrase. No hardware here, so tools/voice_check.py can use the same grammar as the car with the car service stopped.

SPOKEN   one line per command, pointing to an action in commands.ACTIONS with fixed arguments
         (the word recogniser listens for exactly these phrases, "Hilfe" reads them out by group).
A new spoken command: one Spoken line and one EXPLANATIONS entry here, the Action (if new) in commands.py.
'''
import dataclasses
import enum
from typing import Dict, List, Optional, Tuple

STEER_STEP = 20
SPEED_STEP = 10
CAM_STEPS = 10
UNKNOWN = '[unk]'           # Vosk's word for speech outside the grammar
MIN_WORD_CONF = 0.8         # below this the recogniser guessed: a grammar-limited recogniser maps noise onto its nearest phrase
MOVING_ACTIONS = frozenset(('drive', 'drive_burst', 'keepalive', 'room_goto'))  # commands.ACTIONS with moves=True (checked there)
MODEL_FOLDERS = {'de': 'vosk-model-small-de-0.15', 'en': 'vosk-model-small-en-us-0.15'}  # in <data>/models, tools/install_extras.sh


class Kind(enum.Enum):
    COMMAND = 'command'     # switch voice_commands; whole phrase only, never inside a sentence
    STOP = 'stop'           # always listened for; acts on partial words while driving, also inside a sentence
    WAKE = 'wake'           # "hey claude": starts a conversation (switch claude_voice)
    SYSTEM = 'system'       # switch claude_voice; whole phrase only


SWITCH = {Kind.COMMAND: 'voice_commands', Kind.WAKE: 'claude_voice', Kind.SYSTEM: 'claude_voice', Kind.STOP: None}


@dataclasses.dataclass(frozen=True)
class Spoken:
    name: str
    group: str
    action: Optional[str]
    args: Dict
    de: Tuple[str, ...]
    en: Tuple[str, ...]
    kind: Kind = Kind.COMMAND
    fast: bool = False      # acts on the partial result, before the phrase ends: steering only (a wrong one just turns the wheels)

    def phrases(self, language):
        return getattr(self, language)


GROUP_NAMES = {'drive': {'de': 'Fahren', 'en': 'Driving'}, 'camera': {'de': 'Kamera', 'en': 'Camera'},
               'follow': {'de': 'Folgen', 'en': 'Following'}, 'media': {'de': 'Fotos und Videos', 'en': 'Photos and videos'},
               'system': {'de': 'System', 'en': 'System'}}

SPOKEN = (
    Spoken('forward', 'drive', 'drive_burst', {'direction': 1}, ('vorwärts', 'los'), ('forward', 'forwards', 'go')),
    Spoken('back', 'drive', 'drive_burst', {'direction': -1}, ('rückwärts', 'zurück'), ('back', 'backward', 'backwards', 'reverse')),
    Spoken('left', 'drive', 'steer', {'delta': -STEER_STEP}, ('links',), ('left',), fast=True),
    Spoken('right', 'drive', 'steer', {'delta': STEER_STEP}, ('rechts',), ('right',), fast=True),
    Spoken('straight', 'drive', 'steer', {'value': 90}, ('geradeaus',), ('straight',), fast=True),
    Spoken('faster', 'drive', 'speed', {'delta': SPEED_STEP}, ('schneller',), ('faster',)),
    Spoken('slower', 'drive', 'speed', {'delta': -SPEED_STEP}, ('langsamer',), ('slower',)),
    Spoken('stop', 'drive', 'stop', {'reason': 'voice stop'}, ('stopp', 'halt', 'anhalten'), ('stop', 'halt'), kind=Kind.STOP),
    Spoken('ready', 'drive', 'ready', {}, ('startklar',), ('ready',)),  # not "bereit": the German model hears it in English "straight"
    Spoken('camera_left', 'camera', 'cam_move', {'pan': CAM_STEPS}, ('kamera links',), ('camera left',)),
    Spoken('camera_right', 'camera', 'cam_move', {'pan': -CAM_STEPS}, ('kamera rechts',), ('camera right',)),
    Spoken('camera_up', 'camera', 'cam_move', {'tilt': CAM_STEPS}, ('kamera hoch', 'kamera oben'), ('camera up',)),
    Spoken('camera_down', 'camera', 'cam_move', {'tilt': -CAM_STEPS}, ('kamera runter', 'kamera unten'), ('camera down',)),
    Spoken('camera_center', 'camera', 'cam_center', {}, ('mitte', 'kamera mitte'), ('center',)),
    Spoken('follow_ball', 'follow', 'feature', {'name': 'ball_tracking', 'on': True}, ('folge ball',), ('follow ball',)),
    Spoken('follow_face', 'follow', 'feature', {'name': 'face_tracking', 'on': True}, ('folge gesicht',), ('follow face',)),
    Spoken('treasure_hunt', 'follow', 'feature', {'name': 'treasure_hunt', 'on': True}, ('schatzsuche',), ('treasure hunt',)),
    Spoken('photo', 'media', 'photo', {}, ('foto', 'bild'), ('photo', 'picture')),
    Spoken('record', 'media', 'record', {'on': True}, ('aufnahme',), ('record',)),
    Spoken('stop_recording', 'media', 'record', {'on': False}, ('aufnahme stopp',), ('stop recording',)),
    Spoken('hey_claude', 'system', None, {}, ('hey claude', 'hallo claude'), ('hey claude', 'hello claude'), kind=Kind.WAKE),
    Spoken('claude_start', 'system', 'voice_server', {'action': 'start'}, ('claude starten',), ('start claude',), kind=Kind.SYSTEM),
    Spoken('claude_stop', 'system', 'voice_server', {'action': 'stop'}, ('claude beenden', 'claude stoppen'), ('stop claude',), kind=Kind.SYSTEM),
    Spoken('claude_end', 'system', 'conversation_end', {}, ('danke claude',), ('thank you claude',), kind=Kind.SYSTEM),
    Spoken('help', 'system', 'help', {}, ('hilfe',), ('help',)),
    Spoken('learn', 'system', 'learn', {'on': True}, ('befehle lernen',), ('learn commands',)),
    Spoken('learn_stop', 'system', 'learn', {'on': False}, ('lernen beenden',), ('stop learning',)),
)
EXPLANATIONS = {  # name -> what it does (German, English), for learning the commands
    'forward': ('Fährt kurz vorwärts.', 'Drives forward for a moment.'),
    'back': ('Fährt kurz rückwärts.', 'Drives backward for a moment.'),
    'left': ('Lenkt nach links.', 'Steers left.'),
    'right': ('Lenkt nach rechts.', 'Steers right.'),
    'straight': ('Lenkt geradeaus.', 'Steers straight.'),
    'faster': ('Fährt schneller.', 'Speeds up.'),
    'slower': ('Fährt langsamer.', 'Slows down.'),
    'stop': ('Hält sofort an.', 'Stops at once.'),
    'ready': ('Hält an und stellt Lenkung und Kamera in die Mitte.', 'Stops and centres steering and camera.'),
    'camera_left': ('Dreht die Kamera nach links.', 'Turns the camera left.'),
    'camera_right': ('Dreht die Kamera nach rechts.', 'Turns the camera right.'),
    'camera_up': ('Neigt die Kamera nach oben.', 'Tilts the camera up.'),
    'camera_down': ('Neigt die Kamera nach unten.', 'Tilts the camera down.'),
    'camera_center': ('Stellt die Kamera in die Mitte.', 'Centres the camera.'),
    'follow_ball': ('Folgt einem Ball.', 'Follows a ball.'),
    'follow_face': ('Folgt einem Gesicht.', 'Follows a face.'),
    'treasure_hunt': ('Startet die Schatzsuche.', 'Starts the treasure hunt.'),
    'photo': ('Macht ein Foto.', 'Takes a photo.'),
    'record': ('Startet eine Videoaufnahme.', 'Starts recording a video.'),
    'stop_recording': ('Beendet die Videoaufnahme.', 'Stops recording.'),
    'hey_claude': ('Startet ein Gespräch mit Claude.', 'Starts a conversation with Claude.'),
    'claude_start': ('Startet den Sprachserver auf dem PC.', 'Starts the voice server on the PC.'),
    'claude_stop': ('Beendet den Sprachserver auf dem PC.', 'Stops the voice server on the PC.'),
    'claude_end': ('Beendet das Gespräch mit Claude, die Befehle gelten wieder.', 'Ends the conversation with Claude, commands work again.'),
    'help': ('Liest alle Befehle vor.', 'Reads out all commands.'),
    'learn': ('Übt alle Befehle mit dir: ich sage sie vor, du sprichst nach.', 'Practises all commands with you: I say them, you repeat them.'),
    'learn_stop': ('Beendet das Lernen.', 'Ends learning.'),
}
# In a conversation these confirm a feature build ("go", tools/feature_builder.py) instead of driving
NOT_IN_CONVERSATION = ('go', 'los')
HELP_INTRO = {'de': 'Ich bin dein PiCar. Sag eines dieser Wörter, oder hey Claude für ein Gespräch.',
              'en': 'I am your PiCar. Say one of these words, or hey Claude for a conversation.'}
LANGUAGES = tuple(HELP_INTRO)
SPOKEN_BY_NAME = {spoken.name: spoken for spoken in SPOKEN}
# Never two spellings of one sound in a language (stop/stopp, center/centre): Vosk splits its confidence between them,
# 0.5 each, and the car drops both as unsure. tools/voice_check.py replay finds such pairs.


def _check_table():
    assert len(SPOKEN_BY_NAME) == len(SPOKEN), 'two spoken commands with one name'
    assert set(EXPLANATIONS) == set(SPOKEN_BY_NAME), 'EXPLANATIONS and SPOKEN differ: %s' % (set(EXPLANATIONS) ^ set(SPOKEN_BY_NAME))
    for spoken in SPOKEN:
        assert spoken.group in GROUP_NAMES, spoken.name
    for language in LANGUAGES:
        all_phrases = [phrase for spoken in SPOKEN for phrase in spoken.phrases(language)]
        duplicates = {phrase for phrase in all_phrases if all_phrases.count(phrase) > 1}
        assert not duplicates, 'phrase used twice in %s: %s' % (language, duplicates)
    assert all(any(phrase in spoken.de + spoken.en for spoken in SPOKEN) for phrase in NOT_IN_CONVERSATION)


_check_table()


def phrases(language):
    '''Phrase -> Spoken, for the word recogniser.'''
    return {phrase: spoken for spoken in SPOKEN for phrase in spoken.phrases(language)}


def match(language, text):
    '''(phrase, Spoken) for a recognised text, or (None, None): the whole text, else the longest phrase it ends with
    when other grammar words came first (room noise heard as "oben" before "claude starten", 2026-11-15).
    A phrase that drives the car only counts whole: "hey claude los" must not drive.'''
    known = phrases(language)
    if text in known:
        return text, known[text]
    endings = [phrase for phrase, spoken in known.items() if text.endswith(' ' + phrase) and spoken.action not in MOVING_ACTIONS]
    phrase = max(endings, key=len, default=None)
    return phrase, known.get(phrase)


def grammar(language, kinds) -> List[str]:
    '''The phrases a Vosk recogniser is limited to: those of the given kinds, plus UNKNOWN for anything else.'''
    return sorted(phrase for phrase, spoken in phrases(language).items() if spoken.kind in kinds) + [UNKNOWN]


def heard_words(result_words):
    '''Vosk result words -> (words in the grammar, their text, the lowest confidence). UNKNOWN is dropped:
    "[unk] links" still means "links".'''
    words = [word for word in result_words if word['word'] != UNKNOWN]
    return words, ' '.join(word['word'] for word in words), min((word.get('conf', 0) for word in words), default=0)


def explanation(spoken, language):
    return EXPLANATIONS[spoken.name][LANGUAGES.index(language)]


def help_text(language):
    groups = []
    for group, names in GROUP_NAMES.items():
        words = [spoken.phrases(language)[0] for spoken in SPOKEN if spoken.group == group]
        groups.append('%s: %s.' % (names[language], ', '.join(words)))
    return ' '.join([HELP_INTRO[language]] + groups)
