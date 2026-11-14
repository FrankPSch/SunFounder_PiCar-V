'''The PC voice server end to end in echo mode (Whisper and Piper real, Claude replaced by repeating the sentence),
its tools, and the launcher's stop rules. Voice environment; run by tools/run_tests.py.'''
import http.client
import http.server
import json
import os
import sys
import threading
import unittest
from unittest import mock

os.environ['CLAUDE_VOICE_MODEL'] = 'echo'
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import claude_chat  # noqa: E402
import pc_settings  # noqa: E402
import text_to_speech  # noqa: E402
import voice_launcher  # noqa: E402
import voice_server  # noqa: E402

protocol = pc_settings.voice_protocol
TEST_PORT = 18866
STUB_PORT = 18867
server = http.server.ThreadingHTTPServer(('127.0.0.1', TEST_PORT), voice_server.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def request(method, path, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', TEST_PORT, timeout=60)
    connection.request(method, path, body, headers or {})
    response = connection.getresponse()
    frames = list(protocol.read_frames(response))
    return response, [json.loads(payload) for kind, payload in frames if kind == protocol.TEXT], [payload for kind, payload in frames if kind == protocol.AUDIO]


def seconds(audio, rate):
    return len(audio) / 2.0 / rate


class EndpointTest(unittest.TestCase):
    def test_health(self):
        connection = http.client.HTTPConnection('127.0.0.1', TEST_PORT, timeout=10)
        connection.request('GET', '/health')
        self.assertEqual(json.loads(connection.getresponse().read())['model'], 'echo')

    def test_greeting_at_the_rate_the_car_asks(self):
        for asked, expected in ((None, 16000), ('48000', 48000), ('12345', 16000)):
            with self.subTest(asked=asked):
                response, texts, audio = request('GET', '/hello?language=de', headers={protocol.PLAYBACK_RATE: asked} if asked else {})
                self.assertEqual(int(response.getheader(protocol.AUDIO_RATE)), expected)
                self.assertEqual(texts, [{'sentence': 'Hallo.'}])
                self.assertTrue(0.5 < seconds(audio[0], expected) < 2.5)

    def test_unknown_language_greets_in_german(self):
        self.assertEqual(request('GET', '/hello?language=xx')[1], [{'sentence': 'Hallo.'}])

    def test_news_once(self):
        voice_server.announce('de', 'Das Feature Hupen ist gebaut.')
        self.assertEqual(request('GET', '/news')[1], [{'sentence': 'Das Feature Hupen ist gebaut.'}])
        self.assertEqual(request('GET', '/news')[1], [])

    def test_utterance_in_the_cars_language(self):
        german = text_to_speech.synthesize('Wie geht es dir?', 'de')
        english = text_to_speech.synthesize('How are you today?', 'en')
        for audio, languages, expected_language in ((german, 'de', 'de'), (english, 'de', 'de'), (english, 'de,en', 'en')):
            with self.subTest(languages=languages, expected=expected_language):
                response, texts, speech = request('POST', '/utterance?new=1', audio, {protocol.VOICE_LANGUAGES: languages,
                                                                                    protocol.PLAYBACK_RATE: '48000'})
                self.assertEqual(texts[0]['language'], expected_language)
                self.assertEqual(texts[1]['sentence'], texts[0]['transcript'])  # echo
                self.assertTrue(speech)

    def test_shutdown_refused_while_building(self):
        with mock.patch.object(voice_server.builder, 'state', 'building'):
            connection = http.client.HTTPConnection('127.0.0.1', TEST_PORT, timeout=10)
            connection.request('POST', '/shutdown')
            self.assertEqual(connection.getresponse().status, 409)


class FakeHandler(object):
    def __init__(self):
        self.texts = []

    def send_text(self, fields):
        self.texts.append(fields)


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.handler = FakeHandler()
        car = {'status': '{}', 'features': [['qr_reader', False, 'Read QR codes']], 'commands': [['photo', 'Photos: photo']], 'languages': ('de',)}
        self.turn = voice_server.Turn(self.handler, car)

    def test_run_command_sends_it_to_the_car(self):
        self.assertEqual(self.turn._run_tool('run_command', {'name': 'photo'}, ''), 'Ran photo.')
        self.assertEqual(self.handler.texts, [{'run_command': 'photo'}])

    def test_run_command_refuses_what_the_car_did_not_offer(self):
        self.assertIn('Unknown command', self.turn._run_tool('run_command', {'name': 'forward'}, ''))
        self.assertEqual(self.handler.texts, [])

    def test_set_feature(self):
        self.assertEqual(self.turn._run_tool('set_feature', {'name': 'qr_reader', 'on': True}, ''), 'Switched qr_reader on.')
        self.assertIn('Unknown feature', self.turn._run_tool('set_feature', {'name': 'flying', 'on': True}, ''))

    def test_status_line_lists_switches_and_commands(self):
        line = self.turn._status_line()
        self.assertIn('qr_reader=off', line)
        self.assertIn('photo (Photos: photo)', line)

    def test_tool_input_checked(self):
        self.assertTrue(claude_chat.valid_tool_input('run_command', {'name': 'photo'}))
        self.assertFalse(claude_chat.valid_tool_input('run_command', {'name': 3}))
        self.assertFalse(claude_chat.valid_tool_input('run_command', {}))
        self.assertFalse(claude_chat.valid_tool_input('fly', {}))


class LauncherTest(unittest.TestCase):
    '''launcher.stop against a stand-in voice server answering /shutdown with the given status.'''
    def stop_with(self, status):
        class Stub(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.send_response(status)
                self.end_headers()

            def do_GET(self):
                self.send_response(500)
                self.end_headers()
        stub = http.server.ThreadingHTTPServer(('127.0.0.1', STUB_PORT), Stub)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(voice_launcher, 'VOICE_SERVER_URL', 'http://127.0.0.1:%d' % STUB_PORT):
                return voice_launcher.Launcher().stop('test')
        finally:
            stub.shutdown()
            stub.server_close()

    def test_stopped(self):
        self.assertEqual(self.stop_with(200), 200)

    def test_build_running(self):
        self.assertEqual(self.stop_with(409), 409)

    def test_old_server_without_shutdown(self):
        self.assertEqual(self.stop_with(404), 502)

    def test_car_gone_stops_the_server_and_clears_the_voice_stop(self):
        launcher = voice_launcher.Launcher()
        launcher.stopped_by_voice, stops = True, []
        with mock.patch.object(voice_launcher, 'car_reachable', lambda: False), \
                mock.patch.object(launcher, 'server_state', lambda: protocol.SERVER_RUNNING), \
                mock.patch.object(launcher, 'stop', lambda reason: stops.append(reason)), \
                mock.patch.object(voice_launcher.time, 'sleep', mock.Mock(side_effect=StopIteration)):
            with self.assertRaises(StopIteration):
                launcher.watch()
        self.assertEqual(len(stops), 1)
        self.assertFalse(launcher.stopped_by_voice)


if __name__ == '__main__':
    unittest.main()
