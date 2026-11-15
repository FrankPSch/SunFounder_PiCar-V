'''Expected behaviour of every command: web API (POST /api/cmd) and every spoken phrase in German and English,
plus the rules around them (switches, conversation, sentences, car speaking, repeats). Runs on the simulated car.
The tables below are written by hand from the owner's view on purpose: tests/test_commands.py fails when the
command code and this list drift apart in either direction.

  python -m unittest discover tests        (from the repo root, with the sim environment)
'''
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.update(PICAR_SIM='1', PICAR_DATA=tempfile.mkdtemp(prefix='picar_test_'), DJANGO_SETTINGS_MODULE='remote_control.settings')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'remote_control'))
import django  # noqa: E402
django.setup()

from remote_control import api, audio, commands, conversation as conversation_module, events, features, learning, media, rooms, speaker, vocabulary  # noqa: E402
from remote_control.car import car, STEER_MAX, STEER_MIN  # noqa: E402
from remote_control.tracker import tracker  # noqa: E402

conversation = conversation_module.conversation
START_SPEED, START_CAM_STEP = 40, 2

# Spoken phrase -> what the car does, checked on car.state() or on the called function.
# (check, expected): check is a car.state() key, or 'calls' = mocked function names in call order.
SPOKEN = {
    'de': {
        'vorwärts': ('drive_direction', 1), 'los': ('drive_direction', 1),
        'rückwärts': ('drive_direction', -1), 'zurück': ('drive_direction', -1),
        'links': ('steer_angle', 70), 'rechts': ('steer_angle', 110), 'geradeaus': ('steer_angle', 90),
        'schneller': ('speed', 50), 'langsamer': ('speed', 30),
        'kamera links': ('pan_ticks', 20), 'kamera rechts': ('pan_ticks', -20),
        'kamera hoch': ('tilt_ticks', 20), 'kamera oben': ('tilt_ticks', 20),
        'kamera runter': ('tilt_ticks', -20), 'kamera unten': ('tilt_ticks', -20),
        'mitte': ('cam_centred', True), 'startklar': ('ready', True),
        'foto': ('calls', ['snapshot']), 'bild': ('calls', ['snapshot']),
        'aufnahme': ('calls', ['start_recording']), 'aufnahme stopp': ('calls', ['stop_recording']),
        'folge ball': ('feature_on', 'ball_tracking'), 'folge gesicht': ('feature_on', 'face_tracking'),
        'schatzsuche': ('feature_on', 'treasure_hunt'),
        'stopp': ('drive_direction', 0), 'halt': ('drive_direction', 0), 'anhalten': ('drive_direction', 0),
        'claude starten': ('calls', ['server start']), 'claude beenden': ('calls', ['server stop']), 'claude stoppen': ('calls', ['server stop']),
        'hey claude': ('calls', ['conversation start']), 'hallo claude': ('calls', ['conversation start']),
        'hilfe': ('calls', ['help']),
        'befehle lernen': ('calls', ['learn start de']), 'lernen beenden': ('calls', ['learn end']),
    },
    'en': {
        'forward': ('drive_direction', 1), 'forwards': ('drive_direction', 1), 'go': ('drive_direction', 1),
        'back': ('drive_direction', -1), 'backward': ('drive_direction', -1), 'backwards': ('drive_direction', -1), 'reverse': ('drive_direction', -1),
        'left': ('steer_angle', 70), 'right': ('steer_angle', 110), 'straight': ('steer_angle', 90),
        'faster': ('speed', 50), 'slower': ('speed', 30),
        'camera left': ('pan_ticks', 20), 'camera right': ('pan_ticks', -20), 'camera up': ('tilt_ticks', 20), 'camera down': ('tilt_ticks', -20),
        'center': ('cam_centred', True), 'ready': ('ready', True),
        'photo': ('calls', ['snapshot']), 'picture': ('calls', ['snapshot']),
        'record': ('calls', ['start_recording']), 'stop recording': ('calls', ['stop_recording']),
        'follow ball': ('feature_on', 'ball_tracking'), 'follow face': ('feature_on', 'face_tracking'),
        'treasure hunt': ('feature_on', 'treasure_hunt'),
        'stop': ('drive_direction', 0), 'halt': ('drive_direction', 0),
        'start claude': ('calls', ['server start']), 'stop claude': ('calls', ['server stop']),
        'hey claude': ('calls', ['conversation start']), 'hello claude': ('calls', ['conversation start']),
        'help': ('calls', ['help']),
        'learn commands': ('calls', ['learn start en']), 'stop learning': ('calls', ['learn end']),
    },
}
# Phrases that need the car to be driving to show their effect, and the steering ones that start from another angle.
NEEDS_DRIVING = {'stopp', 'stop', 'halt', 'anhalten'}
STARTS_TURNED = {'geradeaus', 'straight'}
STARTS_MOVED = {'mitte', 'center', 'startklar', 'ready'}
NOT_IN_CONVERSATION = {'go', 'los'}  # they confirm a feature build ("go") in a conversation

# POST /api/cmd -> expected effect
API = [
    ({'cmd': 'drive', 'direction': 1}, ('drive_direction', 1)),
    ({'cmd': 'drive', 'direction': -1}, ('drive_direction', -1)),
    ({'cmd': 'stop'}, ('drive_direction', 0)),
    ({'cmd': 'speed', 'value': 70}, ('speed', 70)),
    ({'cmd': 'speed', 'delta': -10}, ('speed', 30)),
    ({'cmd': 'speed', 'value': 500}, ('speed', 100)),
    ({'cmd': 'steer', 'value': 100}, ('steer_angle', 100)),
    ({'cmd': 'steer', 'delta': 15}, ('steer_angle', 105)),
    ({'cmd': 'steer_hold', 'side': -1}, ('steer_hold', -1)),
    ({'cmd': 'cam_move', 'pan': 3}, ('pan_ticks', 6)),
    ({'cmd': 'cam_move', 'tilt': -1, 'step': 5}, ('tilt_ticks', -5)),
    ({'cmd': 'cam_set', 'pan': 12, 'tilt': -7}, ('tilt_ticks', -7)),
    ({'cmd': 'cam_center'}, ('cam_centred', True)),
    ({'cmd': 'cam_step', 'value': 4}, ('cam_step', 4)),
    ({'cmd': 'cam_step', 'delta': 1}, ('cam_step', 3)),
    ({'cmd': 'ready'}, ('ready', True)),
    ({'cmd': 'keepalive'}, ('drive_direction', 0)),
    ({'cmd': 'feature', 'name': 'qr_reader', 'on': True}, ('feature_on', 'qr_reader')),
    ({'cmd': 'track_color', 'name': 'red'}, ('calls', ['set_color'])),
    ({'cmd': 'track_teach'}, ('calls', ['teach'])),
    ({'cmd': 'track_animal', 'name': 'dog'}, ('calls', ['set_animal'])),
    ({'cmd': 'photo'}, ('calls', ['snapshot'])),
    ({'cmd': 'record', 'on': True}, ('calls', ['start_recording'])),
    ({'cmd': 'record', 'on': False}, ('calls', ['stop_recording'])),
    ({'cmd': 'setting', 'name': 'timelapse_seconds', 'value': '30'}, ('setting', ('timelapse_seconds', 30))),
    ({'cmd': 'room_name', 'id': 3, 'name': 'Küche'}, ('calls', ['name_marker'])),
    ({'cmd': 'room_goto', 'name': 'Küche'}, ('calls', ['goto'])),
    ({'cmd': 'room_cancel'}, ('calls', ['cancel'])),
    ({'cmd': 'treasure_next'}, ('calls', ['next_treasure'])),
    ({'cmd': 'learn', 'on': True}, ('calls', ['learn start de'])),
    ({'cmd': 'learn', 'on': True, 'language': 'en', 'group': 'camera'}, ('calls', ['learn start en'])),
    ({'cmd': 'learn', 'on': False}, ('calls', ['learn end'])),
]


class FakeRequest(object):
    def __init__(self, body):
        import json
        self.body = json.dumps(body).encode('utf-8')

    def get_host(self):
        return 'localhost:8000'


def hear(language, text, in_sentence=False):
    '''What the word recogniser does with a recognised, loud and sure phrase.'''
    audio.microphone.words.heard(language, text, in_sentence)


def reset_voice_state():
    audio.microphone.words.reset_repeats()


class CommandTest(unittest.TestCase):
    def setUp(self):
        features.set_enabled('watchdog', False)
        for name in ('voice_commands', 'claude_voice', 'ball_tracking', 'face_tracking', 'treasure_hunt', 'qr_reader'):
            features.set_enabled(name, False)
        features.set_enabled('voice_commands', True)
        features.set_enabled('claude_voice', True)
        car.ready()
        car.set_speed(START_SPEED)
        car.set_cam_step(START_CAM_STEP)
        reset_voice_state()
        speaker.speaking.clear()
        self.calls = []
        record = lambda label: (lambda *args, **kwargs: self.calls.append(label))  # noqa: E731
        patches = [
            mock.patch.object(media, 'snapshot', record('snapshot')),
            mock.patch.object(media, 'start_recording', record('start_recording')),
            mock.patch.object(media, 'stop_recording', record('stop_recording')),
            mock.patch.object(rooms, 'name_marker', record('name_marker')),
            mock.patch.object(rooms, 'goto', record('goto')),
            mock.patch.object(rooms, 'cancel', record('cancel')),
            mock.patch.object(tracker, 'set_color', record('set_color')),
            mock.patch.object(tracker, 'teach', record('teach')),
            mock.patch.object(tracker, 'set_animal', record('set_animal')),
            mock.patch.object(tracker, 'next_treasure', record('next_treasure')),
            mock.patch.object(commands, 'switch_voice_server', lambda action: self.calls.append('server ' + action)),
            mock.patch.object(conversation, 'start', lambda *args: self.calls.append('conversation start')),
            mock.patch.object(conversation, 'active', lambda: False),
        ]
        patches.append(mock.patch.object(commands, 'speak_help', record('help')))
        patches.append(mock.patch.object(learning.session, 'start', lambda language, group=None: self.calls.append('learn start ' + language)))
        patches.append(mock.patch.object(learning.session, 'end', record('learn end')))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def check(self, effect, label):
        key, expected = effect
        state = car.state()
        if key == 'calls':
            self.assertEqual(self.calls, expected, label)
        elif key == 'feature_on':
            self.assertTrue(features.enabled(expected), label)
        elif key == 'cam_centred':
            self.assertEqual((state['pan_ticks'], state['tilt_ticks']), (0, 0), label)
        elif key == 'ready':
            self.assertEqual((state['drive_direction'], state['steer_angle'], state['pan_ticks'], state['tilt_ticks']), (0, 90, 0, 0), label)
        elif key == 'setting':
            self.assertEqual(events.settings().get(expected[0]), expected[1], label)
        else:
            self.assertEqual(state[key], expected, label)



class SpokenPhraseTest(CommandTest):
    def test_every_phrase(self):
        for language, phrases in SPOKEN.items():
            for phrase, effect in phrases.items():
                with self.subTest(language=language, phrase=phrase):
                    self.setUp_phrase(phrase)
                    hear(language, phrase)
                    self.check(effect, '%s "%s"' % (language, phrase))

    def setUp_phrase(self, phrase):
        car.ready()
        car.set_speed(START_SPEED)
        self.calls.clear()
        reset_voice_state()
        for name in ('ball_tracking', 'face_tracking', 'treasure_hunt'):
            features.set_enabled(name, False)
        if phrase in NEEDS_DRIVING:
            car.drive(1)
        if phrase in STARTS_TURNED:
            car.set_steer(70)
        if phrase in STARTS_MOVED:
            car.set_cam(30, -30)
            car.set_steer(STEER_MAX)

    def test_voice_commands_switch_off(self):
        features.set_enabled('voice_commands', False)
        hear('de', 'links')
        self.assertEqual(car.state()['steer_angle'], 90)

    def test_stop_works_without_voice_commands(self):
        features.set_enabled('voice_commands', False)
        car.drive(1)
        hear('de', 'stopp')
        self.assertEqual(car.state()['drive_direction'], 0)

    def test_claude_voice_switch_off(self):
        features.set_enabled('claude_voice', False)
        hear('de', 'claude starten')
        hear('de', 'hey claude')
        self.assertEqual(self.calls, [])

    def test_car_speaking_mutes_commands(self):
        speaker.speaking.set()
        hear('de', 'links')
        hear('de', 'claude starten')
        self.assertEqual(car.state()['steer_angle'], 90)
        self.assertEqual(self.calls, [])

    def test_inside_a_sentence_nothing_steers(self):
        with mock.patch.object(conversation, 'active', lambda: True):
            hear('de', 'links', in_sentence=True)
            hear('de', 'claude beenden', in_sentence=True)
        self.assertEqual(car.state()['steer_angle'], 90)
        self.assertEqual(self.calls, [])

    def test_stop_inside_a_sentence_while_driving(self):
        car.drive(1)
        with mock.patch.object(conversation, 'active', lambda: True):
            hear('de', 'stopp', in_sentence=True)
        self.assertEqual(car.state()['drive_direction'], 0)

    def test_go_and_los_do_not_drive_in_a_conversation(self):
        with mock.patch.object(conversation, 'active', lambda: True):
            for language, phrase in (('en', 'go'), ('de', 'los')):
                hear(language, phrase)
        self.assertEqual(car.state()['drive_direction'], 0)

    def test_vorwaerts_drives_in_a_conversation(self):
        with mock.patch.object(conversation, 'active', lambda: True):
            hear('de', 'vorwärts')
        self.assertEqual(car.state()['drive_direction'], 1)

    def test_same_command_from_both_languages_runs_once(self):
        hear('de', 'links')
        hear('en', 'left')
        self.assertEqual(car.state()['steer_angle'], 70)

    def test_unknown_phrase_does_nothing(self):
        hear('de', 'banane')
        self.assertEqual(car.state()['steer_angle'], 90)
        self.assertEqual(self.calls, [])


class ApiCommandTest(CommandTest):
    def test_every_api_command(self):
        for body, effect in API:
            with self.subTest(body=body):
                car.ready()
                car.set_speed(START_SPEED)
                car.set_cam_step(START_CAM_STEP)
                car.set_steer(90)
                self.calls.clear()
                if effect[0] == 'cam_centred' or effect[0] == 'ready':
                    car.set_cam(30, -30)
                if body['cmd'] == 'steer' and 'delta' in body:
                    car.set_steer(90)
                if body['cmd'] == 'cam_step' and 'delta' in body:
                    car.set_cam_step(2)
                response = api.command(FakeRequest(body))
                self.assertEqual(response.status_code, 200, body)
                self.check(effect, str(body))

    def test_unknown_command_is_rejected(self):
        self.assertEqual(api.command(FakeRequest({'cmd': 'fly'})).status_code, 400)

    def test_missing_argument_is_rejected(self):
        self.assertEqual(api.command(FakeRequest({'cmd': 'drive'})).status_code, 400)


class HelpTest(unittest.TestCase):
    def test_german_help_names_groups_and_commands(self):
        text = vocabulary.help_text('de')
        self.assertTrue(text.startswith(vocabulary.HELP_INTRO['de']))
        for expected in ('Fahren: vorwärts', 'Kamera: kamera links', 'Folgen: folge ball', 'Fotos und Videos: foto', 'System: hey claude', 'claude starten', 'hilfe'):
            self.assertIn(expected, text)

    def test_english_help(self):
        self.assertIn('Driving: forward, back, left', vocabulary.help_text('en'))


class ClaudeCommandTest(CommandTest):
    def test_claude_gets_no_command_that_moves(self):
        names = {name for name, _ in commands.for_claude()}
        self.assertTrue({'photo', 'camera_left', 'center', 'stop', 'follow_ball', 'help'} <= names)
        self.assertFalse({'forward', 'back', 'hey_claude', 'claude_start', 'claude_stop'} & names)

    def test_claude_may_take_a_photo(self):
        self.assertEqual(commands.run_for_claude('photo'), 'claude ran photo')
        self.assertEqual(self.calls, ['snapshot'])

    def test_claude_may_not_drive(self):
        self.assertIn('may not', commands.run_for_claude('forward'))
        self.assertIn('may not', commands.run_for_claude('no_such_command'))
        self.assertEqual(car.state()['drive_direction'], 0)

    def test_claude_reply_runs_the_command(self):
        conversation._on_text({'run_command': 'camera_up'})
        self.assertEqual(car.state()['tilt_ticks'], 20)
        conversation._on_text({'run_command': 'forward'})
        self.assertEqual(car.state()['drive_direction'], 0)

    def test_web_cannot_reach_voice_only_actions(self):
        self.assertEqual(api.command(FakeRequest({'cmd': 'help', 'language': 'de'})).status_code, 400)


class TableTest(unittest.TestCase):
    '''The command code and this file's tables list the same commands and phrases.'''
    def test_spoken_phrases_match(self):
        for language, phrases in SPOKEN.items():
            self.assertEqual(set(vocabulary.phrases(language)), set(phrases), language)

    def test_api_commands_match(self):
        self.assertEqual(set(commands.api_names()), {body['cmd'] for body, _ in API})

    def test_steering_limits(self):
        self.assertLess(STEER_MIN, 70)
        self.assertGreater(STEER_MAX, 110)



if __name__ == '__main__':
    unittest.main()
