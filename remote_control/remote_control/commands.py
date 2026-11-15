'''Every command of the car, in one place.

ACTIONS  what the car can do, with arguments. The web page calls them (POST /api/cmd {"cmd": name, ...}).
         The spoken commands (vocabulary.SPOKEN) point to them; Claude may run the spoken ones that do not move the car.

A new command: one Action here (if the car cannot do it yet), and for a spoken one a Spoken line in vocabulary.py.
tests/test_commands.py lists the expected behaviour of every phrase and fails until it knows the new one too.
'''
import dataclasses
import threading
import time
from typing import Callable, List

from . import events, features, learning, media, rooms, speaker, spoken_texts, voice_link
from .car import car, cam, fw, CALIBRATION_FILE
from .tracker import tracker
from .vocabulary import Kind, MOVING_ACTIONS, SPOKEN, SPOKEN_BY_NAME, GROUP_NAMES, LANGUAGES, help_text
from .voice_settings import voice_languages

DRIVE_BURST_S = 1.4         # a spoken "vorwärts" drives this long; the watchdog stops the car after it
KEEPALIVE_S = 0.2


@dataclasses.dataclass(frozen=True)
class Action:
    run: Callable[[dict], object]
    moves: bool = False     # drives the car: Claude may not run it
    web: bool = True        # offered on POST /api/cmd


def _drive_burst(direction):
    car.drive(direction)

    def keep_alive():
        for _ in range(int(DRIVE_BURST_S / KEEPALIVE_S)):
            time.sleep(KEEPALIVE_S)
            car.keepalive()
    threading.Thread(target=keep_alive, name='drive-burst', daemon=True).start()


def _changed(args, current):
    return args['value'] if 'value' in args else current + args['delta']


def _learn(args):
    '''{"on": true, "language": "de", "group": "drive"} starts a learning session (language and group optional), "on": false ends it.'''
    if not args['on']:
        return learning.session.end()
    language, group = args.get('language') or voice_languages()[0], args.get('group')
    if language not in LANGUAGES or group not in (None,) + tuple(GROUP_NAMES):
        raise KeyError(language if language not in LANGUAGES else group)
    return learning.session.start(language, group)


def switch_voice_server(action):
    '''"start" or "stop" the PC voice server; answers with a tone, never blocks the caller.'''
    from .conversation import conversation  # the conversation reports to commands, so it is imported when used
    conversation.end_if_active()
    threading.Thread(target=_switch_voice_server, args=(action,), name='voice-server-' + action, daemon=True).start()


def end_conversation():
    '''"danke claude" (switch pause_voice_commands): the conversation ends now and the spoken commands act again.'''
    if features.enabled('pause_voice_commands'):
        from .conversation import conversation
        conversation.end_if_active()


def _switch_voice_server(action):
    problem = voice_link.switch_voice_server(action)
    if problem:
        events.add('voice', problem)
        speaker.play_tones(speaker.Tone.FAILED)
        return
    events.add('voice', 'voice server ' + ('ready' if action == 'start' else 'stopped'), say=spoken_texts.VOICE_SERVER[action])
    speaker.play_tones(speaker.Tone.START if action == 'start' else speaker.Tone.END)


# Switch dashboard_calibration: the camera and the steering are calibrated on the dashboard (servos only). The rear
# wheels drive while they are calibrated, so they stay on the old page /cali/.
CALIBRATION_STEPS = {
    'camera': {'start': cam.calibration, 'up': cam.cali_up, 'down': cam.cali_down, 'left': cam.cali_left, 'right': cam.cali_right, 'ok': cam.cali_ok},
    'steering': {'start': fw.calibration, 'left': fw.cali_left, 'right': fw.cali_right, 'ok': fw.cali_ok},
}
CALIBRATION_NAMES = ('turning_offset', 'pan_offset', 'tilt_offset')


def calibrate(part, step):
    if not features.enabled('dashboard_calibration'):
        raise ValueError('feature dashboard_calibration is off')
    CALIBRATION_STEPS[part][step]()
    events.add('calibration', '%s %s' % (part, step))


def calibration():
    '''The saved offsets (DATA_DIR/calibration) for the dashboard, or None while dashboard_calibration is off.'''
    if not features.enabled('dashboard_calibration'):
        return None
    with open(CALIBRATION_FILE) as saved:
        values = dict((line.split('=')[0].strip(), line.split('=')[1].strip()) for line in saved if '=' in line)
    return {name: int(values.get(name, 0)) for name in CALIBRATION_NAMES}


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
    'conversation_end': Action(lambda args: end_conversation(), web=False),
    'help':         Action(lambda args: speak_help(args['language']), web=False),
    'learn':        Action(_learn),
    'calibrate':    Action(lambda args: calibrate(args['part'], args['step'])),
}


def _check_table():
    assert MOVING_ACTIONS == {name for name, action in ACTIONS.items() if action.moves}, 'vocabulary.MOVING_ACTIONS is out of date'
    for spoken in SPOKEN:
        assert spoken.action in ACTIONS or spoken.kind == Kind.WAKE, spoken.name
        assert not spoken.fast or not ACTIONS[spoken.action].moves, 'a fast phrase must not drive: ' + spoken.name


_check_table()


def run(name, args):
    '''One web command. KeyError: unknown command or missing argument.'''
    if not ACTIONS[name].web:
        raise KeyError(name)
    return ACTIONS[name].run(args)


def api_names():
    return [name for name, action in ACTIONS.items() if action.web]


def run_spoken(spoken, language, heard_text):
    '''A recognised phrase: runs its action and logs it (spoken name as the voice prompt).'''
    ACTIONS[spoken.action].run(dict(spoken.args, language=language))
    events.add('audio', 'voice command: %s -> %s' % (heard_text, spoken.name), say=spoken_texts.command_reply(spoken))


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


def speak_help(language):
    speaker.say_later(help_text(language), language)
