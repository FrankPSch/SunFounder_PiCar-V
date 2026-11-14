'''Voice rules on the simulated car, with stand-ins for the PC (voice server, launcher):
loudness thresholds parked/driving, "Claude starten/beenden", the spoken greeting after "hey claude".'''
import http.server
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.update(PICAR_SIM='1', PICAR_DATA=tempfile.mkdtemp(prefix='picar_test_'), DJANGO_SETTINGS_MODULE='remote_control.settings')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'remote_control'))
import django  # noqa: E402
django.setup()

from remote_control import audio, features, speaker, voice_link, voice_settings  # noqa: E402
from remote_control import conversation as conversation_module  # noqa: E402
from remote_control.car import car  # noqa: E402

conversation = conversation_module.conversation
STUB_VOICE_PORT, STUB_LAUNCHER_PORT, CLOSED_PORT = 18766, 18767, 18799
WINDOWS_REFUSAL_S = 6  # a refused connection takes about 2 s on Windows


class Stubs(object):
    '''Stand-in voice server and launcher; record what the car asked.'''
    def __init__(self):
        self.paths, self.voice_up, self.build_running, self.hello = [], False, False, True
        stubs = self

        class Voice(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):
                pass

            def do_GET(self):
                stubs.paths.append(self.path)
                if self.path.startswith('/hello') and stubs.hello:
                    self.send_response(200)
                    self.end_headers()
                    for kind, payload in ((b'T', json.dumps({'sentence': 'Hallo.'}).encode()), (b'A', b'\1\0' * 16000)):
                        self.wfile.write(kind + struct.pack('>I', len(payload)) + payload)
                    return
                self.send_response(200 if self.path == '/health' and stubs.voice_up else 404)
                self.end_headers()

        class Launcher(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                stubs.paths.append(self.path)
                if self.path == '/start':
                    threading.Timer(1, lambda: setattr(stubs, 'voice_up', True)).start()
                status = 409 if self.path == '/stop' and stubs.build_running else 200
                if self.path == '/stop' and status == 200:
                    stubs.voice_up = False
                body = json.dumps({'server': 'starting' if self.path == '/start' else 'off'}).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        for port, handler in ((STUB_VOICE_PORT, Voice), (STUB_LAUNCHER_PORT, Launcher)):
            threading.Thread(target=http.server.ThreadingHTTPServer(('127.0.0.1', port), handler).serve_forever, daemon=True).start()


stubs = Stubs()


class Sounds(object):
    '''Records tones and played speech instead of using a speaker.'''
    def __init__(self):
        self.tones, self.speech_bytes = [], 0

    def wait_for_tones(self, count, limit_s=20):
        end = time.time() + limit_s
        while len(self.tones) < count and time.time() < end:
            time.sleep(0.1)


class FakeStream(object):
    def __init__(self, sounds):
        self.sounds = sounds

    def write(self, audio):
        self.sounds.speech_bytes += len(audio)

    def close(self):
        pass


class VoiceTest(unittest.TestCase):
    def setUp(self):
        self.sounds = Sounds()
        stubs.paths.clear()
        stubs.voice_up, stubs.build_running, stubs.hello = False, False, True
        features.set_enabled('claude_voice', True)
        features.set_enabled('voice_commands', True)
        speaker.speaking.clear()
        car.stop()
        for patch in self.link_patches(STUB_VOICE_PORT, STUB_LAUNCHER_PORT):
            patch.start()
            self.addCleanup(patch.stop)

    def link_patches(self, voice_port, launcher_port):
        sounds = self.sounds
        return [
            mock.patch.object(voice_link, 'voice_server_port', voice_port),
            mock.patch.object(voice_link, 'launcher_port', launcher_port),
            mock.patch.object(speaker, 'play_tones', lambda *tones: sounds.tones.append(tones[0])),
            mock.patch.object(speaker, 'play_tones_later', lambda *tones: None),
            mock.patch.object(speaker, 'open_stream', lambda rate: FakeStream(sounds)),
        ]

    def tone(self, name):
        return getattr(speaker.Tone, name.replace('TONE_', ''))

    def server_by_voice(self, phrase, language='de'):
        audio.microphone.words.reset_repeats()
        audio.microphone.words.heard(language, phrase)


class ServerPhraseTest(VoiceTest):
    def test_start_then_ready_tone(self):
        self.server_by_voice('claude starten')
        self.sounds.wait_for_tones(1)
        self.assertEqual(stubs.paths[:1], ['/start'])
        self.assertEqual(self.sounds.tones, [self.tone('TONE_START')])

    def test_stop_then_falling_tone(self):
        self.server_by_voice('stop claude', 'en')
        self.sounds.wait_for_tones(1)
        self.assertEqual(stubs.paths, ['/stop'])
        self.assertEqual(self.sounds.tones, [self.tone('TONE_END')])

    def test_build_running_low_tone(self):
        stubs.build_running = True
        self.server_by_voice('claude beenden')
        self.sounds.wait_for_tones(1)
        self.assertEqual(self.sounds.tones, [self.tone('TONE_FAILED')])

    def test_pc_off_low_tone(self):
        for patch in self.link_patches(STUB_VOICE_PORT, CLOSED_PORT):
            patch.start()
            self.addCleanup(patch.stop)
        self.server_by_voice('claude starten')
        self.sounds.wait_for_tones(1, WINDOWS_REFUSAL_S)
        self.assertEqual(self.sounds.tones, [self.tone('TONE_FAILED')])


class GreetingTest(VoiceTest):
    def tearDown(self):
        conversation.end()

    def test_hallo_instead_of_tone(self):
        conversation.start()
        time.sleep(1.5)
        self.assertEqual(stubs.paths, ['/hello?language=de'])
        self.assertEqual(self.sounds.speech_bytes, 32000)
        self.assertEqual(self.sounds.tones, [])
        self.assertEqual(conversation.status['state'], 'listening')

    def test_old_server_gets_the_tone(self):
        stubs.hello = False
        conversation.start()
        self.sounds.wait_for_tones(1, 3)
        self.assertEqual(self.sounds.tones, [self.tone('TONE_START')])

    def test_no_server_low_tone(self):
        for patch in self.link_patches(CLOSED_PORT, STUB_LAUNCHER_PORT):
            patch.start()
            self.addCleanup(patch.stop)
        conversation.start()
        self.sounds.wait_for_tones(1, WINDOWS_REFUSAL_S)
        self.assertEqual(self.sounds.tones, [self.tone('TONE_FAILED')])


class FakeRecognizer(object):
    def __init__(self, word):
        self.word = word

    def AcceptWaveform(self, chunk):
        return True

    def Result(self):
        return json.dumps({'result': [{'word': self.word, 'conf': 1.0, 'start': 0, 'end': 0.5}]})

    def Reset(self):
        pass


class ThresholdTest(unittest.TestCase):
    '''Measured 2026-11-14 at mic gain 7/7: room -59, voice -22..-36 parked; motor -24..-29, voice -18..-21 driving.'''
    def setUp(self):
        features.set_enabled('voice_commands', True)
        speaker.speaking.clear()
        car.stop()
        car.set_steer(90)

    def acts(self, word, peak_dbfs, driving):
        heard = []
        car.drive_direction = 1 if driving else 0
        with mock.patch.object(audio.microphone.words, 'heard', lambda language, text, *rest: heard.append(text)):
            audio.microphone.words.recognize('de', FakeRecognizer(word), b'', peak_dbfs)
        car.drive_direction = 0
        return bool(heard)

    def test_parked_voice_acts(self):
        self.assertTrue(self.acts('links', -40, False))

    def test_parked_room_noise_ignored(self):
        self.assertFalse(self.acts('links', -58, False))

    def test_driving_motor_noise_ignored(self):
        self.assertFalse(self.acts('links', -25, True))

    def test_driving_voice_acts(self):
        self.assertTrue(self.acts('links', -20, True))

    def test_driving_stop_at_parked_level_still_acts(self):
        self.assertTrue(self.acts('stopp', -40, True))

    def test_conversation_thresholds(self):
        self.assertEqual(voice_settings.speech_dbfs(), -48)
        car.drive_direction = 1
        self.assertEqual(voice_settings.speech_dbfs(), -22)
        car.drive_direction = 0


if __name__ == '__main__':
    unittest.main()
