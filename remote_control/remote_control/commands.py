'''Every command of the car, in one place.

ACTIONS  what the car can do, with arguments. The web page calls them (POST /api/cmd {"cmd": name, ...}).
SPOKEN   the spoken commands: phrases in German and English, each pointing to an action with fixed arguments.
         The word recogniser listens for exactly these phrases, "Hilfe" reads them out by group, and Claude may
         run the ones that do not move the car.

A new command: one Action (if the car cannot do it yet) and one Spoken line. tests/test_commands.py lists the
expected behaviour of every phrase and fails until it knows the new one too.
'''
import dataclasses
import enum
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import events, features, media, rooms, speaker, voice_link
from .car import car
from .tracker import tracker

DRIVE_BURST_S = 1.4         # a spoken "vorwärts" drives this long; the watchdog stops the car after it
KEEPALIVE_S = 0.2
STEER_STEP = 20
SPEED_STEP = 10
CAM_STEPS = 10


class Kind(enum.Enum):
    COMMAND = 'command'     # switch voice_commands; whole phrase only, never inside a sentence
    STOP = 'stop'           # always listened for; acts on partial words while driving, also inside a sentence
    WAKE = 'wake'           # "hey claude": starts a conversation (switch claude_voice)
    SYSTEM = 'system'       # switch claude_voice; whole phrase only


SWITCH = {Kind.COMMAND: 'voice_commands', Kind.WAKE: 'claude_voice', Kind.SYSTEM: 'claude_voice', Kind.STOP: None}


@dataclasses.dataclass(frozen=True)
class Action:
    run: Callable[[dict], object]
    moves: bool = False     # drives the car: Claude may not run it
    web: bool = True        # offered on POST /api/cmd


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


def _drive_burst(direction):
    car.drive(direction)

    def keep_alive():
        for _ in range(int(DRIVE_BURST_S / KEEPALIVE_S)):
            time.sleep(KEEPALIVE_S)
            car.keepalive()
    threading.Thread(target=keep_alive, name='drive-burst', daemon=True).start()


def _changed(args, current):
    return args['value'] if 'value' in args else current + args['delta']


def switch_voice_server(action):
    '''"start" or "stop" the PC voice server; answers with a tone, never blocks the caller.'''
    from .conversation import conversation  # the conversation reports to commands, so it is imported when used
    conversation.end_if_active()
    threading.Thread(target=_switch_voice_server, args=(action,), name='voice-server-' + action, daemon=True).start()


def _switch_voice_server(action):
    problem = voice_link.switch_voice_server(action)
    if problem:
        events.add('voice', problem)
        speaker.play_tones(speaker.Tone.FAILED)
        return
    events.add('voice', 'voice server ' + ('ready' if action == 'start' else 'stopped'), say='claude ' + ('ready' if action == 'start' else 'off'))
    speaker.play_tones(speaker.Tone.START if action == 'start' else speaker.Tone.END)


ACTIONS = {
    'drive':        Action(lambda args: car.drive(args['direction']), moves=True),
    'drive_burst':  Action(lambda args: _drive_burst(args['direction']), moves=True, web=False),
    'keepalive':    Action(lambda args: car.keepalive(), moves=True),
    'stop':         Action(lambda args: car.stop(args.get('reason', 'stop button'))),
    'speed':        Action(lambda args: car.set_speed(_changed(args, car.speed))),
    'steer':        Action(lambda args: car.set_steer(_changed(args, car.steer_angle))),
    'steer_hold':   Action(lambda args: car.hold_steer(args['side'])),
    'cam_move':     Action(lambda args: car.move_cam(args.get('pan', 0), args.get('tilt', 0), args.get('step'))),
    'cam_set':      Action(lambda args: car.set_cam(args['pan'], args['tilt'])),
    'cam_center':   Action(lambda args: car.center_cam()),
    'cam_step':     Action(lambda args: car.set_cam_step(_changed(args, car.cam_step))),
    'ready':        Action(lambda args: car.ready()),
    'feature':      Action(lambda args: features.set_enabled(args['name'], args['on'])),
    'track_color':  Action(lambda args: tracker.set_color(args['name'])),
    'track_teach':  Action(lambda args: tracker.teach()),
    'track_animal': Action(lambda args: tracker.set_animal(args['name'])),
    'photo':        Action(lambda args: media.snapshot()),
    'record':       Action(lambda args: media.start_recording() if args['on'] else media.stop_recording()),
    'setting':      Action(lambda args: events.update_setting(args['name'], args['value'])),
    'room_name':    Action(lambda args: rooms.name_marker(args['id'], args.get('name', ''))),
    'room_goto':    Action(lambda args: rooms.goto(args['name']), moves=True),
    'room_cancel':  Action(lambda args: rooms.cancel()),
    'treasure_next': Action(lambda args: tracker.next_treasure()),
    'voice_server': Action(lambda args: switch_voice_server(args['action']), web=False),
    'help':         Action(lambda args: speak_help(args['language']), web=False),
}

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
    Spoken('stop', 'drive', 'stop', {'reason': 'voice stop'}, ('stopp', 'stop', 'halt', 'anhalten'), ('stop', 'halt'), kind=Kind.STOP),
    Spoken('ready', 'drive', 'ready', {}, ('startklar',), ('ready',)),  # not "bereit": the German model hears it in English "straight"
    Spoken('camera_left', 'camera', 'cam_move', {'pan': CAM_STEPS}, ('kamera links',), ('camera left',)),
    Spoken('camera_right', 'camera', 'cam_move', {'pan': -CAM_STEPS}, ('kamera rechts',), ('camera right',)),
    Spoken('camera_up', 'camera', 'cam_move', {'tilt': CAM_STEPS}, ('kamera hoch', 'kamera oben'), ('camera up',)),
    Spoken('camera_down', 'camera', 'cam_move', {'tilt': -CAM_STEPS}, ('kamera runter', 'kamera unten'), ('camera down',)),
    Spoken('center', 'camera', 'cam_center', {}, ('mitte',), ('center', 'centre')),
    Spoken('follow_ball', 'follow', 'feature', {'name': 'ball_tracking', 'on': True}, ('folge ball',), ('follow ball',)),
    Spoken('follow_face', 'follow', 'feature', {'name': 'face_tracking', 'on': True}, ('folge gesicht',), ('follow face',)),
    Spoken('treasure_hunt', 'follow', 'feature', {'name': 'treasure_hunt', 'on': True}, ('schatzsuche',), ('treasure hunt',)),
    Spoken('photo', 'media', 'photo', {}, ('foto', 'bild'), ('photo', 'picture')),
    Spoken('record', 'media', 'record', {'on': True}, ('aufnahme',), ('record',)),
    Spoken('stop_recording', 'media', 'record', {'on': False}, ('aufnahme stopp',), ('stop recording',)),
    Spoken('hey_claude', 'system', None, {}, ('hey claude', 'hallo claude'), ('hey claude', 'hello claude'), kind=Kind.WAKE),
    Spoken('claude_start', 'system', 'voice_server', {'action': 'start'}, ('claude starten',), ('start claude',), kind=Kind.SYSTEM),
    Spoken('claude_stop', 'system', 'voice_server', {'action': 'stop'}, ('claude beenden', 'claude stoppen'), ('stop claude',), kind=Kind.SYSTEM),
    Spoken('help', 'system', 'help', {}, ('hilfe',), ('help',)),
)
# In a conversation these confirm a feature build ("go", tools/feature_builder.py) instead of driving
NOT_IN_CONVERSATION = ('go', 'los')
HELP_INTRO = {'de': 'Ich bin dein PiCar. Sag eines dieser Wörter, oder hey Claude für ein Gespräch.',
              'en': 'I am your PiCar. Say one of these words, or hey Claude for a conversation.'}
SPOKEN_BY_NAME = {spoken.name: spoken for spoken in SPOKEN}


def _check_table():
    assert len(SPOKEN_BY_NAME) == len(SPOKEN), 'two spoken commands with one name'
    for spoken in SPOKEN:
        assert spoken.action in ACTIONS or spoken.kind == Kind.WAKE, spoken.name
        assert spoken.group in GROUP_NAMES, spoken.name
        assert not spoken.fast or not ACTIONS[spoken.action].moves, 'a fast phrase must not drive: ' + spoken.name
    for language in HELP_INTRO:
        all_phrases = [phrase for spoken in SPOKEN for phrase in spoken.phrases(language)]
        duplicates = {phrase for phrase in all_phrases if all_phrases.count(phrase) > 1}
        assert not duplicates, 'phrase used twice in %s: %s' % (language, duplicates)
    assert all(any(phrase in spoken.de + spoken.en for spoken in SPOKEN) for phrase in NOT_IN_CONVERSATION)


_check_table()


def run(name, args):
    '''One web command. KeyError: unknown command or missing argument.'''
    if not ACTIONS[name].web:
        raise KeyError(name)
    return ACTIONS[name].run(args)


def phrases(language):
    '''Phrase -> Spoken, for the word recogniser.'''
    return {phrase: spoken for spoken in SPOKEN for phrase in spoken.phrases(language)}


def api_names():
    return [name for name, action in ACTIONS.items() if action.web]


def run_spoken(spoken, language, heard_text):
    '''A recognised phrase: runs its action and logs it (spoken name as the voice prompt).'''
    ACTIONS[spoken.action].run(dict(spoken.args, language=language))
    events.add('audio', 'voice command: %s -> %s' % (heard_text, spoken.name), say=spoken.name.replace('_', ' '))


def for_claude() -> List[List[str]]:
    '''[name, description] of the spoken commands Claude may run: none of them moves the car.'''
    return [[spoken.name, '%s: %s' % (GROUP_NAMES[spoken.group]['en'], spoken.en[0])] for spoken in SPOKEN
            if spoken.kind in (Kind.COMMAND, Kind.STOP) and not ACTIONS[spoken.action].moves]


def run_for_claude(name):
    '''Claude asked for a spoken command by name. Returns what happened, for the log.'''
    if name not in {allowed for allowed, _ in for_claude()}:
        return 'claude may not run %r' % name
    spoken = SPOKEN_BY_NAME[name]
    ACTIONS[spoken.action].run(dict(spoken.args, language='en'))
    return 'claude ran ' + name


def help_text(language):
    groups = []
    for group, names in GROUP_NAMES.items():
        words = [spoken.phrases(language)[0] for spoken in SPOKEN if spoken.group == group]
        groups.append('%s: %s.' % (names[language], ', '.join(words)))
    return ' '.join([HELP_INTRO[language]] + groups)


def speak_help(language):
    speaker.say_later(help_text(language), language)
